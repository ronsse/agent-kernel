# Contributing to Agent Kernel

Thank you for your interest in contributing to Agent Kernel! This guide will help you get started.

## Prerequisites

- **Python 3.11+** (3.12 and 3.13 also supported)
- **uv** package manager (recommended) or pip
- **Git** for version control

## Development Setup

```bash
# Clone the repository
git clone https://github.com/agent-kernel/agent-kernel.git
cd agent-kernel

# Create and activate virtual environment
uv venv && source .venv/bin/activate

# Install with development dependencies
uv pip install -e ".[dev]"

# Copy environment configuration
cp .env.example .env
```

## Running Quality Checks

Agent Kernel uses a Makefile for common development tasks:

```bash
make lint          # Lint with ruff
make format        # Format code with ruff
make typecheck     # Type checking with mypy
make test          # Run all tests
make test-unit     # Unit tests only
make test-cov      # Tests with coverage report
```

All checks must pass before submitting a pull request.

## Code Style

- **Formatter:** ruff (88 character line length)
- **Docstrings:** Google style
- **Type hints:** Required on all public API functions and methods
- **Logging:** Use `structlog` -- never use `print()` in library code
- **Nesting:** Maximum 3 levels deep; prefer guard clauses
- **Error handling:** Explicit error handling with helpful messages for all external API calls

## Architecture Overview

When contributing, keep these design principles in mind:

1. **Schemas are the contract.** All data flows through typed Pydantic models. Never pass unstructured data between components.
2. **Tool Broker is sacred.** All tool execution goes through the Tool Broker. Never call tools directly from agents or engines.
3. **Traces are immutable.** Every `DecisionTrace` is a complete, auditable record. Never modify traces after creation.
4. **Engines are thin adapters.** Agent engines produce `Plan` objects from `ContextPacket` input. They must not call tools, own memory, or log traces.

For detailed architecture documentation, see the [Concepts](https://agent-kernel.github.io/agent-kernel/concepts/) section of the docs.

## Pull Request Process

### Before Submitting

- Run all quality checks locally (`make lint`, `make test`, `make typecheck`)
- Ensure no debugging code or print statements remain
- Add tests for new functionality
- Update relevant documentation

### PR Guidelines

- **Keep PRs small and focused** -- one concern per PR
- **Write descriptive commit messages** explaining the "why", not just the "what"
- **Keep CI green** -- fix failures before requesting review
- **Include a clear PR description** covering:
  - **What:** Description of changes
  - **Why:** Motivation and context
  - **How:** Technical approach
  - **Risks:** Potential issues or breaking changes

### Schema Changes

When modifying or adding Pydantic schemas:

1. Define the model in `src/agent_kernel/core/schemas/`
2. Export in `schemas/__init__.py`
3. Update documentation in `docs/design/01-schemas.md`
4. Add unit tests in `tests/unit/core/schemas/`

### Tool Capabilities

When adding a new tool capability:

1. Create capability YAML in `configs/capabilities/`
2. Implement adapter in `src/agent_kernel/tools/adapters/`
3. Register in the capability registry
4. Add to `allowed_capabilities` in relevant agent profiles

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
