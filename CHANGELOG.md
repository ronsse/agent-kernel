# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-03-XX

### Added

- Core schemas: `ContextPacket`, `Plan`, `DecisionTrace`, `ToolCallRecord`, `ActionRequest`, `AgentProfile`, `ContextRef`
- Deterministic executor with trust boundary enforcement (agent hints vs system-computed policy)
- Tool Broker with capability registry, approval gates, rate limiting, and audit logging
- Context Assembler with retrieval planning and deterministic context packet assembly
- Pluggable agent engines: `CustomEngine` implementation and `EngineRegistry` for discovery
- SQLite-backed stores: document, vector (with LanceDB option), graph, event log, traces
- Workflow runner with checkpoint/resume, approval persistence, and step-level state tracking
- 4-tier thinking escalation system with adaptive policy controller
- LLM semantic cache with tier-aware TTL and effort ranking
- Adaptive timeout manager (per-capability P99-based tuning)
- Success rate router for model selection based on historical performance
- Cost anomaly detector with rolling window analysis
- Circuit breaker and retry with exponential backoff for tool execution
- CLI interface via Typer: workflow management, trace inspection, approval handling
- Skills system for portable procedural guidance
- Workflow triggers and chaining (`on_complete`, `TriggerType.WORKFLOW`)
- Graph ontology with 30+ node types and 40+ edge types
- Universal entity model for cross-source context (notes, messages, emails)
- Experience memory: cases, lessons, playbooks derived from traces
- Schema versioning with migration registry
