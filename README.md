# Agent Kernel (Archived)

> **This project has been superseded by [Experience Graph](https://github.com/ronsse/experience-graph).**

Agent Kernel was a framework-agnostic foundation for building reliable, auditable AI agent systems. The core ideas — schemas as contracts, governed execution, immutable traces, pluggable stores — have evolved into **Experience Graph**, an org-scale context graph and experience store for AI agents and teams.

## What changed?

Agent Kernel focused on agent *runtime* (planning, execution, tool brokering). Experience Graph focuses on the *knowledge layer* that agents read from and write to:

- **Traces** — structured records of agent/human actions with outcomes
- **Entities** — nodes in a shared knowledge graph
- **Evidence** — provenance-tracked artifacts
- **Precedents** — curated institutional knowledge extracted from traces
- **Policies** — governance rules for the write pipeline
- **Packs** — retrieval bundles assembled for specific tasks

## Migration

The following components were ported to Experience Graph:

| Agent Kernel | Experience Graph |
|---|---|
| Store backends (SQLite doc/graph/vector/event) | `xpgraph/stores/` |
| ULID generation, content hashing | `xpgraph/core/` |
| Pydantic base models + versioning | `xpgraph/schemas/` |
| Importance scoring + hybrid search | `xpgraph/retrieve/` |
| DeterministicExecutor | `xpgraph/mutate/` (governed write pipeline) |
| Enrichment service | `xpgraph_workers/enrichment/` |
| Experience miner | `xpgraph_workers/learning/` |
| Thinking policy | `xpgraph_workers/engine/` |
| Vault indexer | `integrations/obsidian/` |

## License

MIT License - see [LICENSE](LICENSE) for details.
