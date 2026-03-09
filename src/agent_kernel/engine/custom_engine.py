"""Custom Engine - LLM-based planner with structured output.

This engine uses direct LLM calls to generate plans from context.
It enforces structured output via JSON schema.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

import structlog

from agent_kernel.core.errors import PlanGenerationError
from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas import (
    ActionRequest,
    AgentProfile,
    ContextPacket,
    ContextRef,
    Plan,
    PlanValidation,
    RiskAssessment,
    RiskLevel,
    SideEffect,
)
from agent_kernel.engine.agent_engine import BaseAgentEngine
from agent_kernel.prompting import (
    PromptBundle,
    PromptRegistry,
    get_prompt_serializer,
    split_context_items,
)
from agent_kernel.tools.registry import CapabilityRegistry

logger = structlog.get_logger(__name__)


# Runtime prompt additions for plan generation
SYSTEM_PROMPT_SUFFIX = """Context format: {context_format}

{capabilities_section}

Important rules:
- Only use capabilities from the allowed list above
- Use ONLY the parameter names shown in each capability's schema - do NOT invent parameters
- Always cite context items you reference (evidence only)
- Be specific about action arguments
- Keep summaries concise (1-5 sentences)
- Mark external writes as requiring approval when appropriate
- Skills may appear in context as ref_type=skill with metadata and descriptions.
  If you need full skill content, use skills.load@v1 with the skill_id and include files.
- CRITICAL: If a capability is NOT in the allowed list, DO NOT use it even if mentioned in context.
  Check the allowed capabilities list before selecting any capability."""

USER_PROMPT = """Intent: {intent}

Context:
{context_items}

{graph_context}

Create a plan to address this intent. Respond with a JSON object containing:
- summary: Brief description of the plan (1-5 sentences)
- context_refs_used: List of context refs you're citing
- actions: List of actions to take (must include at least one action)
  Each action needs: capability_name, args (dict), side_effect, requires_approval
- risk: Risk assessment with level and reasons
- questions: Any clarifying questions
- notes: Brief rationale
- validation: Missing info and assumptions

You MUST include at least one action in the actions array."""


class CustomEngine(BaseAgentEngine):
    """Custom LLM-based planning engine.

    Uses direct LLM calls with structured output to generate plans.
    Supports multiple LLM providers via the llm_service.
    """

    def __init__(
        self,
        llm_service: Any = None,
        engine_id: str = "custom",
        version: str = "1.0.0",
        prompt_registry: PromptRegistry | None = None,
        capability_registry: CapabilityRegistry | None = None,
    ) -> None:
        """Initialize custom engine.

        Args:
            llm_service: LLM service for generating completions.
            engine_id: Engine identifier.
            version: Engine version.
            prompt_registry: Registry for prompt templates.
            capability_registry: Registry for capability schemas (enables schema injection).
        """
        super().__init__(engine_id, version)
        self._llm_service = llm_service
        self._prompt_registry = prompt_registry or PromptRegistry()
        self._capability_registry = capability_registry
        logger.info(
            "custom_engine_initialized",
            engine_id=engine_id,
            version=version,
            has_capability_registry=capability_registry is not None,
        )

    def set_llm_service(self, llm_service: Any) -> None:
        """Set the LLM service.

        Args:
            llm_service: The LLM service to use.
        """
        self._llm_service = llm_service

    _MAX_PLAN_RETRIES = 2
    _RETRY_DELAY_SECONDS = 2.0

    async def propose(
        self,
        context_packet: ContextPacket,
        agent_profile: AgentProfile,
        thinking_policy: Any = None,
    ) -> Plan:
        """Generate a plan from context using LLM.

        Retries up to _MAX_PLAN_RETRIES times on empty responses or
        JSON parse failures before raising PlanGenerationError.

        Args:
            context_packet: The assembled context.
            agent_profile: The agent's configuration.
            thinking_policy: Optional ThinkingPolicy with model/reasoning
                overrides from the thinking policy controller.

        Returns:
            A structured Plan.
        """
        if self._llm_service is None:
            return self._create_stub_plan(context_packet, agent_profile)

        # Build prompts once (reused across retries)
        prompt_bundle = self._prompt_registry.compose_from_items(context_packet.items)
        context_packet = self._strip_system_prompts(context_packet)
        context_text, context_format = self._render_context(context_packet, agent_profile)
        system_prompt = self._build_system_prompt(
            prompt_bundle=prompt_bundle,
            context_format=context_format,
            capabilities=agent_profile.allowed_capabilities,
        )
        graph_text = self._format_graph(context_packet)

        user_prompt = USER_PROMPT.format(
            intent=context_packet.intent,
            context_items=context_text,
            graph_context=graph_text,
        )

        # Resolve LLM parameters: thinking_policy overrides agent_profile
        if thinking_policy is not None:
            llm_model = thinking_policy.model_id
            llm_temperature = thinking_policy.temperature
            llm_max_tokens = thinking_policy.max_tokens
            effort = thinking_policy.reasoning_effort
            # ThinkingPolicy.reasoning_effort may be an enum or string
            llm_reasoning_effort = (
                effort.value if hasattr(effort, "value") else str(effort)
            )
            logger.debug(
                "propose_using_thinking_policy",
                model=llm_model,
                temperature=llm_temperature,
                max_tokens=llm_max_tokens,
                reasoning_effort=llm_reasoning_effort,
            )
        else:
            llm_model = agent_profile.llm_config.model
            llm_temperature = agent_profile.llm_config.temperature
            llm_max_tokens = agent_profile.llm_config.max_tokens
            llm_reasoning_effort = agent_profile.llm_config.reasoning_effort

        last_error: Exception | None = None
        for attempt in range(1, self._MAX_PLAN_RETRIES + 2):  # attempts 1..max+1
            try:
                response = await self._llm_service.generate(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    model=llm_model,
                    temperature=llm_temperature,
                    max_tokens=llm_max_tokens,
                    reasoning_effort=llm_reasoning_effort,
                )

                if not response or not response.strip():
                    raise PlanGenerationError(
                        "LLM returned empty response", self.engine_id
                    )

                plan = self._parse_plan_response(
                    response,
                    context_packet,
                    agent_profile,
                )

                logger.info(
                    "plan_generated",
                    plan_id=plan.plan_id,
                    actions_count=len(plan.actions),
                    risk_level=plan.risk.level.value,
                    attempt=attempt,
                )

                return plan

            except Exception as e:
                last_error = e
                if attempt <= self._MAX_PLAN_RETRIES:
                    logger.warning(
                        "plan_generation_retry",
                        attempt=attempt,
                        max_retries=self._MAX_PLAN_RETRIES,
                        error=str(e),
                    )
                    await asyncio.sleep(self._RETRY_DELAY_SECONDS * attempt)
                else:
                    logger.error(
                        "plan_generation_failed",
                        error=str(e),
                        attempts=attempt,
                        exc_info=True,
                    )

        raise PlanGenerationError(str(last_error), self.engine_id)

    def _render_context(
        self,
        packet: ContextPacket,
        agent_profile: AgentProfile,
    ) -> tuple[str, str]:
        """Render context using the prompt serializer when configured."""
        prompt_config = agent_profile.prompt_config
        if prompt_config is None:
            return self._format_context(packet), "markdown"

        format_id = prompt_config.format
        if not prompt_config.enable_toon and format_id in {"toon", "mixed"}:
            format_id = prompt_config.fallback_format

        serializer = get_prompt_serializer(format_id)
        try:
            return serializer.render(packet, agent_profile), format_id
        except Exception as exc:
            logger.warning(
                "prompt_serializer_failed",
                error=str(exc),
                format_id=format_id,
            )
            return self._format_context(packet), "markdown"

    def _strip_system_prompts(self, packet: ContextPacket) -> ContextPacket:
        prompt_items, evidence_items = split_context_items(packet.items)
        if not prompt_items:
            return packet
        report = packet.retrieval_report
        updated_report = report.model_copy(
            update={
                "items_selected": max(report.items_selected - len(prompt_items), 0),
            }
        )
        return packet.model_copy(update={"items": evidence_items, "retrieval_report": updated_report})

    def _format_capabilities_section(self, capability_names: list[str]) -> str:
        """Format capabilities with their input schemas for the prompt.

        If a capability registry is available, includes JSON schema for each
        capability's parameters. This helps the LLM use the correct parameter names.

        Args:
            capability_names: List of capability names from agent profile.

        Returns:
            Formatted capabilities section for the system prompt.
        """
        if not self._capability_registry:
            # Fallback: just list names (original behavior)
            return f"Available capabilities: {', '.join(capability_names)}"

        lines = ["Available capabilities (use ONLY these parameter names):"]
        lines.append("")

        for cap_name in capability_names:
            cap_def = self._capability_registry.get(cap_name)
            if cap_def:
                lines.append(f"## {cap_name}")
                if cap_def.description:
                    # Truncate long descriptions
                    desc = cap_def.description.strip()
                    if len(desc) > 200:
                        desc = desc[:200] + "..."
                    lines.append(f"Description: {desc}")

                # Format input schema properties
                input_schema = cap_def.input_schema
                if input_schema and "properties" in input_schema:
                    props = input_schema["properties"]
                    required = input_schema.get("required", [])
                    lines.append("Parameters:")
                    for prop_name, prop_def in props.items():
                        prop_type = prop_def.get("type", "any")
                        prop_desc = prop_def.get("description", "")
                        req_marker = " (required)" if prop_name in required else ""
                        # Handle enum values
                        if "enum" in prop_def:
                            prop_type = f"enum: {prop_def['enum']}"
                        lines.append(f"  - {prop_name}: {prop_type}{req_marker}")
                        if prop_desc:
                            lines.append(f"      {prop_desc[:100]}")
                lines.append("")
            else:
                # Capability not in registry - just list name
                lines.append(f"## {cap_name}")
                lines.append("(No schema available)")
                lines.append("")

        return "\n".join(lines)

    def _build_system_prompt(
        self,
        prompt_bundle: PromptBundle,
        context_format: str,
        capabilities: list[str],
    ) -> str:
        """Build the system prompt with capability schemas."""
        capabilities_section = self._format_capabilities_section(capabilities)

        prompt_sections = []
        if prompt_bundle.text:
            prompt_sections.append(prompt_bundle.text)
        prompt_sections.append(
            SYSTEM_PROMPT_SUFFIX.format(
                context_format=context_format,
                capabilities_section=capabilities_section,
            )
        )
        return "\n\n".join(section for section in prompt_sections if section)

    def _format_context(self, packet: ContextPacket) -> str:
        """Format context items for the prompt."""
        if not packet.items:
            return "No context items available."

        lines = []
        for i, item in enumerate(packet.items, 1):
            ref = item.ref
            lines.append(f"[{i}] {ref.ref_type.value} - {ref.ref_id}")
            if ref.metadata.get("title"):
                lines.append(f"    Title: {ref.metadata['title']}")
            lines.append(f"    Excerpt: {item.excerpt[:200]}...")
            lines.append("")

        return "\n".join(lines)

    def _format_graph(self, packet: ContextPacket) -> str:
        """Format graph context for the prompt."""
        if not packet.graph_slice:
            return ""

        nodes = packet.graph_slice.nodes
        # edges = packet.graph_slice.edges  # Available for future use

        if not nodes:
            return ""

        lines = ["Graph Context:"]
        for node in nodes[:10]:  # Limit nodes shown
            props = node.get("properties", {})
            node_type = node.get("node_type")
            name = props.get("name", node.get("node_id"))
            lines.append(f"  - {node_type}: {name}")

        if len(nodes) > 10:
            lines.append(f"  ... and {len(nodes) - 10} more nodes")

        return "\n".join(lines)

    def _parse_plan_response(
        self,
        response: str,
        context_packet: ContextPacket,
        agent_profile: AgentProfile,
    ) -> Plan:
        """Parse LLM response into a Plan object."""
        def _ensure_list(value: Any) -> list[Any]:
            if value is None:
                return []
            if isinstance(value, list):
                return value
            return [value]

        try:
            # Try to extract JSON from response
            data = self._extract_json(response)

            # Build context refs
            context_refs = []
            for ref_data in _ensure_list(data.get("context_refs_used", [])):
                if isinstance(ref_data, dict):
                    context_refs.append(ContextRef(**ref_data))
            if not context_refs and agent_profile.context_policy.must_cite:
                from agent_kernel.prompting.system_prompts import is_system_prompt_ref

                evidence_refs = [
                    item.ref
                    for item in context_packet.items
                    if not is_system_prompt_ref(item.ref)
                ]
                if evidence_refs:
                    context_refs = evidence_refs[:3]

            # Build actions
            actions = []
            for action_data in _ensure_list(data.get("actions", [])):
                if isinstance(action_data, dict):
                    capability_name = (
                        action_data.get("capability_name")
                        or action_data.get("capability")
                        or action_data.get("tool")
                        or action_data.get("name")
                    )
                    if not capability_name:
                        continue
                    # Parse side_effect - handle string descriptions by defaulting to NONE
                    side_effect_str = action_data.get("side_effect", "none")
                    if isinstance(side_effect_str, str):
                        # Try to parse as enum value, default to NONE if invalid
                        try:
                            side_effect = SideEffect(side_effect_str.lower())
                        except (ValueError, AttributeError):
                            # If it's a description or invalid value, default to NONE
                            side_effect = SideEffect.NONE
                    else:
                        side_effect = SideEffect.NONE
                    args = action_data.get("args", {})
                    idempotency_key = action_data.get("idempotency_key")
                    cap_group = action_data.get("cap_group")
                    cap_limit = action_data.get("cap_limit")
                    if side_effect.is_write and not idempotency_key:
                        args_hash = hashlib.sha256(
                            json.dumps(args, sort_keys=True).encode()
                        ).hexdigest()[:16]
                        idempotency_key = f"{capability_name}:{args_hash}"
                    action = ActionRequest(
                        capability_name=capability_name,
                        args=args,
                        side_effect=side_effect,
                        requires_approval=action_data.get("requires_approval", False),
                        idempotency_key=idempotency_key,
                        cap_group=cap_group,
                        cap_limit=cap_limit,
                    )
                    actions.append(action)
            # If no actions were generated, log warning but don't add fallback
            # actions that bypass the agent profile's allowed_capabilities
            if not actions:
                logger.warning(
                    "no_actions_in_plan",
                    intent=context_packet.intent[:100],
                    agent_profile_id=agent_profile.agent_profile_id,
                    allowed_capabilities=agent_profile.allowed_capabilities[:5],
                )

            # Build risk assessment
            risk_data = data.get("risk", {})
            if not isinstance(risk_data, dict):
                risk_data = {"level": "low", "reasons": risk_data}
            risk_level_str = risk_data.get("level", "low").lower()
            risk_level_map = {
                "low": RiskLevel.LOW,
                "medium": RiskLevel.MEDIUM,
                "med": RiskLevel.MEDIUM,
                "high": RiskLevel.HIGH,
                "critical": RiskLevel.CRITICAL,
            }
            risk = RiskAssessment(
                level=risk_level_map.get(risk_level_str, RiskLevel.LOW),
                reasons=_ensure_list(risk_data.get("reasons", [])),
            )

            # Build validation
            validation_data = data.get("validation", {})
            if not isinstance(validation_data, dict):
                validation_data = {"missing_info": validation_data, "assumptions": []}
            validation = PlanValidation(
                missing_info=_ensure_list(validation_data.get("missing_info", [])),
                assumptions=_ensure_list(validation_data.get("assumptions", [])),
            )

            return Plan(
                plan_id=generate_ulid(),
                intent=context_packet.intent,
                summary=data.get("summary", "Plan generated"),
                context_refs_used=context_refs,
                actions=actions,
                risk=risk,
                questions=_ensure_list(data.get("questions", [])),
                notes=data.get("notes"),
                validation=validation,
            )

        except Exception as e:
            logger.error("plan_parse_failed", error=str(e), response=response[:200])
            raise PlanGenerationError(f"Failed to parse plan: {e}", self.engine_id)

    def _extract_json(self, text: str) -> dict[str, Any]:
        """Extract JSON from LLM response text.

        Raises:
            ValueError: If no valid JSON found or response appears truncated.
        """
        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to find JSON block
        import re
        json_match = re.search(r"```json?\s*([\s\S]*?)```", text)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"Malformed JSON in code block (possible truncation — "
                    f"increase max_tokens): {e}"
                ) from e

        # Try to find raw JSON object
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"Malformed JSON (possible truncation — increase max_tokens): {e}"
                ) from e

        # Check if response contains an opening brace but no closing — truncation
        if "{" in text and "}" not in text:
            raise ValueError(
                "Response appears truncated (opening '{' without closing '}') — "
                "increase max_tokens in agent profile"
            )

        raise ValueError("No valid JSON found in response")

    def _create_stub_plan(
        self,
        context_packet: ContextPacket,
        agent_profile: AgentProfile,
    ) -> Plan:
        """Create a stub plan when no LLM is available."""
        stub_reasons = ["Stub plan - no actions"]
        stub_note = (
            "This is a stub plan generated without LLM. "
            "Configure llm_service to generate real plans."
        )
        return Plan(
            plan_id=generate_ulid(),
            intent=context_packet.intent,
            summary=f"Stub plan for: {context_packet.intent}",
            context_refs_used=[item.ref for item in context_packet.items[:3]],
            actions=[],
            risk=RiskAssessment(level=RiskLevel.LOW, reasons=stub_reasons),
            questions=["LLM service not configured. What actions?"],
            notes=stub_note,
            validation=PlanValidation(
                missing_info=["LLM service"],
                assumptions=["User will provide actions manually"],
            ),
        )
