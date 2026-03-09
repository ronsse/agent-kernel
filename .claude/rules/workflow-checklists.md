# Agent Workflow & Checklists

## Before Starting a Task

- [ ] Read nearest CLAUDE.md / AGENTS.md and relevant `.claude/rules/`
- [ ] Identify affected components (schemas, tools, engines, etc.)
- [ ] Check existing implementations for patterns
- [ ] Plan parallelizable steps to reduce round-trips

## During Implementation

- [ ] Prefer small, composable functions; add types to public interfaces
- [ ] Match existing formatting and naming conventions
- [ ] Handle edge cases early with guard clauses
- [ ] Add minimal, high-value docstrings where non-obvious
- [ ] Use async/await patterns for I/O operations
- [ ] Implement proper error handling with custom exceptions from `core/errors.py`

## Before Submitting

- [ ] Run lints/tests locally (`make lint`, `make test`)
- [ ] Validate schema changes work with existing code
- [ ] Test new capabilities with the Tool Broker
- [ ] Provide a succinct PR description (what/why/how, risks, follow-ups)

## Schema Changes Checklist

- [ ] Define Pydantic model in `src/agent_kernel/core/schemas/`
- [ ] Export in `schemas/__init__.py`
- [ ] Update docs in `docs/design/01-schemas.md`
- [ ] Add unit tests in `tests/unit/core/schemas/`

## Tool Capability Checklist

- [ ] Create capability YAML in `configs/capabilities/`
- [ ] Implement adapter in `src/agent_kernel/tools/adapters/`
- [ ] Register in capability registry
- [ ] Add to `allowed_capabilities` in relevant agent profiles

## Agent Engine Checklist

- [ ] Implement `AgentEngine` protocol
- [ ] Must accept `ContextPacket` + `AgentProfile`, return `Plan`
- [ ] Must NOT call tools directly or own memory
- [ ] Add as optional dependency in `pyproject.toml`
