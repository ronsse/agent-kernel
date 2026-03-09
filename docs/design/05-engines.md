# Agent Engines

**Version:** 1.0.1  
**Status:** Design Phase

Agent Engines are **pluggable components** that turn `ContextPacket` into `Plan`. The kernel doesn't care which engine produces the plan, as long as it validates against schema.

---

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      AGENT ENGINE LAYER                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Interface: AgentEngine                                          │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │  propose(context_packet, agent_profile) -> Plan             ││
│  │  revise(plan, observations) -> Plan  (optional)             ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
│  Implementations                                                 │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐               │
│  │   Custom    │ │  LangGraph  │ │  Semantic   │               │
│  │   Engine    │ │   Adapter   │ │   Kernel    │               │
│  │  (default)  │ │  (optional) │ │  (optional) │               │
│  └─────────────┘ └─────────────┘ └─────────────┘               │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## AgentEngine Interface

### Protocol Definition

```python
from typing import Protocol

class AgentEngine(Protocol):
    """
    Pluggable engine that produces Plans from context.
    
    IMPORTANT: Engines must NOT:
    - Call tools directly
    - Own memory stores
    - Decide approval policies
    - Log traces (emit metadata; kernel logs)
    
    Engines SHOULD:
    - Accept ContextPacket and AgentProfile
    - Return valid Plan with citations
    - Provide metadata (engine_id, version)
    """
    
    @property
    def engine_id(self) -> str:
        """Unique identifier for this engine."""
        ...
    
    @property
    def version(self) -> str:
        """Engine version string."""
        ...
    
    async def propose(
        self,
        context_packet: ContextPacket,
        agent_profile: AgentProfile,
    ) -> Plan:
        """
        Generate a plan from context.
        
        Args:
            context_packet: Assembled context with budget info
            agent_profile: Agent configuration including model settings
        
        Returns:
            Validated Plan with actions and citations
        """
        ...
    
    async def revise(
        self,
        plan: Plan,
        observations: list[str],
    ) -> Plan:
        """
        Optionally revise a plan based on feedback.
        
        Use sparingly - prefer single-shot planning.
        
        Args:
            plan: Previous plan
            observations: Feedback or observations
        
        Returns:
            Revised Plan
        """
        ...
```

### Engine Metadata

```python
class EngineMetadata(BaseModel):
    """Metadata about an engine for tracing."""
    
    engine_id: str
    version: str
    model_provider: str
    model_name: str
    prompt_hash: str | None = None
```

---

## Custom Engine (Default)

The default implementation using direct LLM calls with structured output.

### Prompt serialization (TOON toggle)

The Custom Engine can render context using a prompt serializer when
`AgentProfile.prompt_config` is set. Supported formats:

- `markdown`: Human-readable list with excerpts (default)
- `json`: Structured JSON payload
- `toon`: Token-optimized encoding for structured arrays (optional library)
- `mixed`: TOON/JSON for structured data + markdown excerpts

Behavior:
- If `prompt_config.enable_toon` is `false`, `toon`/`mixed` fall back to
  `prompt_config.fallback_format`.
- If the serializer fails, the engine falls back to markdown rendering.
- The engine passes the selected `context_format` in the system prompt so the
  LLM knows how to interpret the context.

### System prompt composition

The engine composes system prompts from prompt packs (base + vault + project +
workflow + agent) and appends runtime details such as available capabilities and
context format. Prompt refs with `metadata.kind=system_prompt` are **not**
included in user context or citations.

### Implementation

```python
class CustomEngine:
    """
    Default agent engine using direct LLM calls.
    
    Uses structured output (JSON mode) to produce Plans.
    """
    
    def __init__(
        self,
        llm_service: LLMService,
    ):
        self.llm = llm_service
        self._engine_id = "custom"
        self._version = "1.0.0"
    
    @property
    def engine_id(self) -> str:
        return self._engine_id
    
    @property
    def version(self) -> str:
        return self._version
    
    async def propose(
        self,
        context_packet: ContextPacket,
        agent_profile: AgentProfile,
    ) -> Plan:
        """Generate a plan using LLM."""
        
        # 1. Build system prompt
        system_prompt = self._build_system_prompt(agent_profile)
        
        # 2. Build user prompt with context
        user_prompt = self._build_user_prompt(context_packet)
        
        # 3. Get allowed capabilities for the prompt
        capabilities_prompt = self._build_capabilities_prompt(
            agent_profile.allowed_capabilities
        )
        
        # 4. Call LLM with structured output
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": capabilities_prompt + "\n\n" + user_prompt},
        ]
        
        response = await self.llm.generate_structured(
            messages=messages,
            response_model=Plan,
            model=agent_profile.model_config.model,
            temperature=agent_profile.model_config.temperature,
        )
        
        # 5. Validate citations
        self._validate_citations(response, context_packet)
        
        return response
    
    async def revise(
        self,
        plan: Plan,
        observations: list[str],
    ) -> Plan:
        """Revise a plan based on observations."""
        
        # Build revision prompt
        revision_prompt = f"""
        Previous plan:
        {plan.summary}
        
        Observations/feedback:
        {chr(10).join(f'- {obs}' for obs in observations)}
        
        Please revise the plan based on this feedback.
        """
        
        messages = [
            {"role": "system", "content": "You are revising a plan based on feedback."},
            {"role": "user", "content": revision_prompt},
        ]
        
        return await self.llm.generate_structured(
            messages=messages,
            response_model=Plan,
        )
    
    def _build_system_prompt(self, profile: AgentProfile) -> str:
        """Build system prompt for agent."""
        
        return f"""You are {profile.name}, an AI assistant that creates structured plans.

Your role:
- Analyze the provided context
- Create a plan with specific actions
- Always cite your sources
- Assess risk level accurately

Output format:
- summary: 1-5 sentences describing the plan
- context_refs_used: list of refs you actually used (MUST cite)
- actions: specific ActionRequests to execute
- risk: assessment with level and reasons
- validation: note any missing info or assumptions

Guidelines:
- Only propose actions from allowed capabilities
- Mark external writes as requires_approval=true
- Keep notes concise (avoid lengthy chain-of-thought)
- Be specific and actionable
"""
    
    def _build_user_prompt(self, packet: ContextPacket) -> str:
        """Build user prompt with context."""
        
        context_text = "\n\n".join([
            f"[{item.ref.ref_type.value}:{item.ref.ref_id}]\n{item.excerpt}"
            for item in packet.items
        ])
        
        return f"""Intent: {packet.intent}

Project: {packet.project_id or "none"}

Available Context:
{context_text}

Please create a plan to address this intent.
"""
    
    def _build_capabilities_prompt(
        self,
        capabilities: list[str],
    ) -> str:
        """Build capabilities list for prompt."""
        
        return f"""Available capabilities you can use:
{chr(10).join(f'- {cap}' for cap in capabilities)}
"""
    
    def _validate_citations(
        self,
        plan: Plan,
        packet: ContextPacket,
    ) -> None:
        """Ensure all citations reference actual context."""
        
        available_ids = {item.ref.ref_id for item in packet.items}
        
        for ref in plan.context_refs_used:
            if ref.ref_id not in available_ids:
                raise ValueError(f"Citation {ref.ref_id} not in context")
```

---

## LangGraph Adapter (Optional)

Adapter for using LangGraph as the planning engine.

### Implementation

```python
class LangGraphAdapter:
    """
    Adapter for LangGraph-based planning.
    
    Wraps LangGraph graphs to produce kernel-compatible Plans.
    """
    
    def __init__(
        self,
        graph: CompiledGraph,  # LangGraph compiled graph
    ):
        self.graph = graph
        self._engine_id = "langgraph"
        self._version = "0.2.0"
    
    @property
    def engine_id(self) -> str:
        return self._engine_id
    
    @property
    def version(self) -> str:
        return self._version
    
    async def propose(
        self,
        context_packet: ContextPacket,
        agent_profile: AgentProfile,
    ) -> Plan:
        """Run LangGraph and convert output to Plan."""
        
        # 1. Convert ContextPacket to LangGraph state
        initial_state = self._to_langgraph_state(context_packet, agent_profile)
        
        # 2. Run graph
        config = {"configurable": {"thread_id": context_packet.packet_id}}
        final_state = await self.graph.ainvoke(initial_state, config)
        
        # 3. Extract Plan from final state
        return self._from_langgraph_state(final_state, context_packet)
    
    async def revise(
        self,
        plan: Plan,
        observations: list[str],
    ) -> Plan:
        """Continue graph with observations."""
        
        # Add observations to state and continue
        state_update = {"observations": observations, "plan": plan}
        final_state = await self.graph.ainvoke(state_update)
        
        return self._from_langgraph_state(final_state)
    
    def _to_langgraph_state(
        self,
        packet: ContextPacket,
        profile: AgentProfile,
    ) -> dict:
        """Convert kernel types to LangGraph state."""
        
        return {
            "messages": [],
            "intent": packet.intent,
            "context": [item.model_dump() for item in packet.items],
            "capabilities": profile.allowed_capabilities,
            "model_config": profile.model_config.model_dump(),
        }
    
    def _from_langgraph_state(
        self,
        state: dict,
        packet: ContextPacket | None = None,
    ) -> Plan:
        """Convert LangGraph state to kernel Plan."""
        
        # Extract plan data from state
        # (Implementation depends on your LangGraph graph structure)
        
        return Plan(
            plan_id=generate_ulid(),
            intent=state.get("intent", ""),
            summary=state.get("summary", ""),
            context_refs_used=self._extract_citations(state),
            actions=self._extract_actions(state),
            risk=RiskAssessment(
                level=RiskLevel(state.get("risk_level", "low")),
                reasons=state.get("risk_reasons", []),
            ),
            validation=PlanValidation(
                missing_info=state.get("missing_info", []),
                assumptions=state.get("assumptions", []),
            ),
        )
```

---

## Semantic Kernel Adapter (Optional)

Adapter for Microsoft Semantic Kernel.

```python
class SemanticKernelAdapter:
    """
    Adapter for Semantic Kernel planners.
    """
    
    def __init__(
        self,
        kernel: Kernel,  # Semantic Kernel instance
    ):
        self.kernel = kernel
        self._engine_id = "semantic_kernel"
        self._version = "1.0.0"
    
    @property
    def engine_id(self) -> str:
        return self._engine_id
    
    @property
    def version(self) -> str:
        return self._version
    
    async def propose(
        self,
        context_packet: ContextPacket,
        agent_profile: AgentProfile,
    ) -> Plan:
        """Use Semantic Kernel planner to generate Plan."""
        
        # 1. Set up planner with context
        planner = SequentialPlanner(self.kernel)
        
        # 2. Add context as kernel variables
        variables = KernelArguments(
            intent=context_packet.intent,
            context=self._format_context(context_packet),
        )
        
        # 3. Generate plan
        sk_plan = await planner.create_plan_async(
            goal=context_packet.intent,
            arguments=variables,
        )
        
        # 4. Convert to kernel Plan
        return self._from_sk_plan(sk_plan, context_packet)
    
    async def revise(
        self,
        plan: Plan,
        observations: list[str],
    ) -> Plan:
        """Revise using Semantic Kernel."""
        raise NotImplementedError("Revision not yet implemented for SK")
```

---

## Engine Registry

Manages available engines and their instantiation.

```python
class EngineRegistry:
    """Registry of available agent engines."""
    
    def __init__(self):
        self._engines: dict[str, AgentEngine] = {}
        self._factories: dict[str, Callable[[], AgentEngine]] = {}
    
    def register(
        self,
        engine_id: str,
        factory: Callable[[], AgentEngine],
    ) -> None:
        """Register an engine factory."""
        self._factories[engine_id] = factory
    
    def get(self, engine_id: str) -> AgentEngine:
        """Get or create an engine instance."""
        
        if engine_id not in self._engines:
            if engine_id not in self._factories:
                raise ValueError(f"Unknown engine: {engine_id}")
            self._engines[engine_id] = self._factories[engine_id]()
        
        return self._engines[engine_id]
    
    def list_engines(self) -> list[str]:
        """List registered engine IDs."""
        return list(self._factories.keys())


# Default registry setup
def create_default_registry(
    llm_service: LLMService,
) -> EngineRegistry:
    """Create registry with default engines."""
    
    registry = EngineRegistry()
    
    # Always available
    registry.register(
        "custom",
        lambda: CustomEngine(llm_service),
    )
    
    # Optional: LangGraph
    try:
        from langgraph.graph import CompiledGraph
        registry.register(
            "langgraph",
            lambda: LangGraphAdapter(create_default_graph()),
        )
    except ImportError:
        pass
    
    # Optional: Semantic Kernel
    try:
        from semantic_kernel import Kernel
        registry.register(
            "semantic_kernel",
            lambda: SemanticKernelAdapter(create_default_kernel()),
        )
    except ImportError:
        pass
    
    return registry
```

---

## LLM Service Interface

The engines use an LLM service for completions. The service is provider-agnostic, allowing each agent to use a different LLM provider/model.

### Protocol Definition

```python
class LLMService(Protocol):
    """Interface for LLM completions."""
    
    @property
    def provider(self) -> str:
        """Provider identifier (openai, anthropic, ollama, custom)."""
        ...
    
    async def generate(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """Generate text completion."""
        ...
    
    async def generate_structured(
        self,
        messages: list[dict[str, str]],
        response_model: type[BaseModel],
        model: str | None = None,
        temperature: float = 0.7,
    ) -> BaseModel:
        """Generate structured output matching a Pydantic model."""
        ...
```

### Provider Implementations

```
LLMService Protocol
├── OpenAIService (default) - gpt-4o, gpt-4o-mini, etc.
├── AnthropicService        - claude-3-5-sonnet, etc.
├── OllamaService           - llama3.2, mistral, etc. (local)
└── CustomHTTPService       - Self-hosted endpoints
```

### OpenAI Service (Default)

```python
class OpenAILLMService:
    """OpenAI implementation of LLM service."""
    
    def __init__(
        self,
        api_key: str,
        default_model: str = "gpt-4o",
        base_url: str | None = None,  # For OpenAI-compatible APIs
    ):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.default_model = default_model
    
    @property
    def provider(self) -> str:
        return "openai"
    
    async def generate(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        response = await self.client.chat.completions.create(
            model=model or self.default_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content
    
    async def generate_structured(
        self,
        messages: list[dict[str, str]],
        response_model: type[BaseModel],
        model: str | None = None,
        temperature: float = 0.7,
    ) -> BaseModel:
        """Use structured outputs / JSON mode."""
        response = await self.client.beta.chat.completions.parse(
            model=model or self.default_model,
            messages=messages,
            response_format=response_model,
            temperature=temperature,
        )
        return response.choices[0].message.parsed
```

### Anthropic Service

```python
class AnthropicLLMService:
    """Anthropic implementation of LLM service."""
    
    def __init__(
        self,
        api_key: str,
        default_model: str = "claude-3-5-sonnet-20241022",
    ):
        self.client = AsyncAnthropic(api_key=api_key)
        self.default_model = default_model
    
    @property
    def provider(self) -> str:
        return "anthropic"
    
    async def generate(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        # Convert OpenAI message format to Anthropic format
        system = next((m["content"] for m in messages if m["role"] == "system"), None)
        user_messages = [m for m in messages if m["role"] != "system"]
        
        response = await self.client.messages.create(
            model=model or self.default_model,
            system=system or "",
            messages=user_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.content[0].text
    
    async def generate_structured(
        self,
        messages: list[dict[str, str]],
        response_model: type[BaseModel],
        model: str | None = None,
        temperature: float = 0.7,
    ) -> BaseModel:
        """Use tool_use for structured output."""
        # Anthropic uses tool_use pattern for structured output
        # Wrap the schema as a tool and extract the response
        tool = {
            "name": "respond",
            "description": "Respond with structured data",
            "input_schema": response_model.model_json_schema(),
        }
        
        system = next((m["content"] for m in messages if m["role"] == "system"), None)
        user_messages = [m for m in messages if m["role"] != "system"]
        
        response = await self.client.messages.create(
            model=model or self.default_model,
            system=system or "",
            messages=user_messages,
            tools=[tool],
            tool_choice={"type": "tool", "name": "respond"},
            temperature=temperature,
            max_tokens=4096,
        )
        
        tool_use = next(b for b in response.content if b.type == "tool_use")
        return response_model.model_validate(tool_use.input)
```

### Ollama Service (Local LLMs)

```python
class OllamaLLMService:
    """Ollama implementation for local LLMs."""
    
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        default_model: str = "llama3.2",
    ):
        self.base_url = base_url
        self.default_model = default_model
        self.http_client = httpx.AsyncClient(base_url=base_url)
    
    @property
    def provider(self) -> str:
        return "ollama"
    
    async def generate(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        response = await self.http_client.post(
            "/api/chat",
            json={
                "model": model or self.default_model,
                "messages": messages,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
                "stream": False,
            },
        )
        response.raise_for_status()
        return response.json()["message"]["content"]
    
    async def generate_structured(
        self,
        messages: list[dict[str, str]],
        response_model: type[BaseModel],
        model: str | None = None,
        temperature: float = 0.7,
    ) -> BaseModel:
        """Use JSON mode with schema in prompt."""
        schema_prompt = f"\n\nRespond with valid JSON matching this schema:\n{response_model.model_json_schema()}"
        
        # Append schema to last user message
        messages = messages.copy()
        messages[-1] = {
            "role": messages[-1]["role"],
            "content": messages[-1]["content"] + schema_prompt,
        }
        
        response = await self.http_client.post(
            "/api/chat",
            json={
                "model": model or self.default_model,
                "messages": messages,
                "format": "json",
                "options": {"temperature": temperature},
                "stream": False,
            },
        )
        response.raise_for_status()
        
        content = response.json()["message"]["content"]
        return response_model.model_validate_json(content)
```

### Custom HTTP Service (Self-Hosted)

```python
class CustomHTTPLLMService:
    """Custom HTTP endpoint for self-hosted LLMs."""
    
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        default_model: str = "default",
    ):
        self.base_url = base_url
        self.default_model = default_model
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.http_client = httpx.AsyncClient(base_url=base_url, headers=headers)
    
    @property
    def provider(self) -> str:
        return "custom"
    
    # Implement generate() and generate_structured() based on your API
```

### LLM Service Factory

```python
def create_llm_service(
    provider: str,
    api_key: str | None = None,
    base_url: str | None = None,
    default_model: str | None = None,
) -> LLMService:
    """Create an LLM service based on provider configuration."""
    
    if provider == "openai":
        return OpenAILLMService(
            api_key=api_key or os.environ["OPENAI_API_KEY"],
            default_model=default_model or "gpt-4o",
            base_url=base_url,
        )
    elif provider == "anthropic":
        return AnthropicLLMService(
            api_key=api_key or os.environ["ANTHROPIC_API_KEY"],
            default_model=default_model or "claude-3-5-sonnet-20241022",
        )
    elif provider == "ollama":
        return OllamaLLMService(
            base_url=base_url or "http://localhost:11434",
            default_model=default_model or "llama3.2",
        )
    elif provider == "custom":
        return CustomHTTPLLMService(
            base_url=base_url,
            api_key=api_key,
            default_model=default_model or "default",
        )
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")
```

### Per-Agent Model Configuration

Each `AgentProfile` can specify its own provider and model:

```yaml
# configs/agents/daily_review_agent.yaml
agent_profile_id: daily_review_agent
name: Daily Review Agent
engine: custom

llm_config:
  provider: openai        # openai | anthropic | ollama | custom
  model: gpt-4o           # Provider-specific model name
  temperature: 0.3
  max_tokens: 4096
  base_url: null          # Optional: override base URL
```

```yaml
# configs/agents/local_summarizer.yaml
agent_profile_id: local_summarizer
name: Local Summarizer (Ollama)
engine: custom

llm_config:
  provider: ollama
  model: llama3.2
  temperature: 0.5
  max_tokens: 2048
  base_url: http://localhost:11434
```

This allows different agents to use different LLM providers without code changes.

---

## Best Practices

### What Engines Should Do

✅ Accept `ContextPacket` and `AgentProfile`  
✅ Return valid `Plan` with proper citations  
✅ Respect `allowed_capabilities` from profile  
✅ Set appropriate `risk` levels  
✅ Provide `engine_id` and `version` for tracing  

### What Engines Should NOT Do

❌ Call tools directly  
❌ Own or manage memory stores  
❌ Make approval decisions  
❌ Write traces (kernel handles this)  
❌ Persist state between calls  

---

## Related Documents

- [00-overview.md](00-overview.md) - Design principles
- [01-schemas.md](01-schemas.md) - Plan, AgentProfile schemas
- [04-context.md](04-context.md) - ContextPacket assembly
- [06-executor.md](06-executor.md) - Plan execution
