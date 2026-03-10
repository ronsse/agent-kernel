# Experience Graph Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the `experience-graph` repo from scratch — core schemas, stores, mutation pipeline, CLI, workers — then archive `agent-kernel`.

**Architecture:** Fresh repo with three packages: `xpgraph` (core library with schemas, stores, ingest, retrieve, mutate, curate), `xpgraph_cli` (thin Typer CLI), and `xpgraph_workers` (curation workflows + enrichment engine). Stores are ported from agent-kernel's battle-tested SQLite backends. The mutation pipeline replaces the deterministic executor with a narrower write-gate scope.

**Tech Stack:** Python 3.11+, Pydantic 2.0+, structlog, ulid-py, Typer/Rich (CLI), SQLite (default stores), pytest + pytest-asyncio, ruff, mypy.

**Design Doc:** `docs/plans/2026-03-10-experience-graph-pivot-design.md`

---

## Phase 1: New Repo + Core Schemas

**Deliverable:** A new `experience-graph` repo with project scaffolding, CI, and the 6 core entity schemas fully defined with tests.

### Task 1.1: Create Repo and Project Scaffolding

**Files:**
- Create: `experience-graph/pyproject.toml`
- Create: `experience-graph/Makefile`
- Create: `experience-graph/README.md`
- Create: `experience-graph/.github/workflows/ci.yml`
- Create: `experience-graph/.pre-commit-config.yaml`
- Create: `experience-graph/.gitignore`
- Create: `experience-graph/src/xpgraph/__init__.py`
- Create: `experience-graph/src/xpgraph/py.typed`
- Create: `experience-graph/src/xpgraph_cli/__init__.py`
- Create: `experience-graph/src/xpgraph_workers/__init__.py`
- Create: `experience-graph/tests/__init__.py`
- Create: `experience-graph/tests/unit/__init__.py`

**Step 1: Create the repo directory and initialize git**

```bash
mkdir -p ~/experience-graph
cd ~/experience-graph
git init
```

**Step 2: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling", "hatch-vcs"]
build-backend = "hatchling.build"

[project]
name = "xpgraph"
dynamic = ["version"]
description = "A shared experience store: traces + provenance + curated precedent + retrieval packs"
readme = "README.md"
license = "MIT"
requires-python = ">=3.11"
authors = [{ name = "Experience Graph Contributors" }]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Typing :: Typed",
]
dependencies = [
    "pydantic>=2.0.0,<3.0.0",
    "pydantic-settings>=2.0.0,<3.0.0",
    "structlog>=23.0.0,<26.0.0",
    "ulid-py>=1.1.0,<2.0.0",
    "python-dateutil>=2.8.0,<3.0.0",
    "pyyaml>=6.0",
    "typer>=0.9",
    "rich>=13.0",
    "httpx>=0.27",
]

[project.optional-dependencies]
vectors = [
    "lancedb>=0.3.0,<1.0.0",
    "pyarrow>=14.0.0",
    "pandas>=2.0.0",
    "numpy>=1.24",
]
cli = ["xpgraph"]
workers = [
    "xpgraph",
]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "pytest-cov>=4.0",
    "ruff>=0.8",
    "mypy>=1.13",
    "pre-commit>=4.0",
]
all = ["xpgraph[vectors,cli,workers]"]

[project.scripts]
xpg = "xpgraph_cli.main:app"

[tool.hatch.version]
source = "vcs"

[tool.hatch.build.hooks.vcs]
version-file = "src/xpgraph/_version.py"

[tool.hatch.build.targets.wheel]
packages = ["src/xpgraph", "src/xpgraph_cli", "src/xpgraph_workers"]

[tool.ruff]
line-length = 88
target-version = "py311"
src = ["src"]

[tool.ruff.lint]
select = [
    "E", "F", "B", "W", "I", "N", "UP", "ANN", "S", "C4",
    "DTZ", "T10", "EM", "ISC", "ICN", "LOG", "G", "PIE", "PT",
    "RET", "SIM", "TCH", "ARG", "PTH", "ERA", "PL", "TRY",
    "FLY", "PERF", "FURB", "RUF",
]
ignore = [
    "ANN401",
    "S101",
    "PLR0913",
    "TC001",
    "TC003",
    "RUF022",
]

[tool.ruff.lint.per-file-ignores]
"tests/**/*.py" = ["S101", "ANN", "PLR2004", "ARG002", "ARG005"]

[tool.mypy]
python_version = "3.11"
strict = false
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = false

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "-v --tb=short"
pythonpath = ["src"]

[tool.coverage.run]
source = ["src/xpgraph"]
branch = true
```

**Step 3: Create Makefile**

```makefile
.PHONY: help install install-dev lint format typecheck test clean

help:
	@echo "Experience Graph - Available Commands"
	@echo ""
	@echo "  make install-dev   Install development dependencies"
	@echo "  make lint          Run linting (ruff)"
	@echo "  make format        Format code (ruff format)"
	@echo "  make typecheck     Run type checking (mypy)"
	@echo "  make test          Run all tests"
	@echo "  make clean         Remove build artifacts"

install:
	uv pip install -e .

install-dev:
	uv pip install -e ".[dev]"
	pre-commit install || true

lint:
	ruff check src/ tests/

format:
	ruff format src/ tests/
	ruff check --fix src/ tests/

typecheck:
	mypy src/

test:
	pytest tests/ -v

clean:
	rm -rf build/ dist/ *.egg-info .pytest_cache .mypy_cache .ruff_cache htmlcov/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
```

**Step 4: Create README.md**

```markdown
# Experience Graph

A shared experience store for AI agents and teams: traces + provenance + curated precedent + retrieval packs, with policy-gated mutations and immutable audit trails.

> **This is not an agent orchestration framework.** It is the org's context and precedent system of record that agents can read from and write to under governance.

## Install

\`\`\`bash
pip install xpgraph
\`\`\`

## CLI

\`\`\`bash
pip install xpgraph-cli
xpg admin init
xpg ingest trace trace.json
xpg retrieve pack --intent "deploy checklist" --domain platform
\`\`\`
```

**Step 5: Create CI workflow, .gitignore, .pre-commit-config.yaml**

Copy structure from agent-kernel's `.github/workflows/ci.yml`, `.gitignore`, `.pre-commit-config.yaml` — update package names from `agent_kernel` to `xpgraph`.

**Step 6: Create package __init__.py files**

```python
# src/xpgraph/__init__.py
"""Experience Graph — shared experience store for AI agents and teams."""
```

```python
# src/xpgraph_cli/__init__.py
"""Experience Graph CLI."""
```

```python
# src/xpgraph_workers/__init__.py
"""Experience Graph curation workers."""
```

**Step 7: Commit**

```bash
git add -A
git commit -m "feat: initial project scaffolding"
```

---

### Task 1.2: Core Utilities (IDs, timestamps, base models)

**Files:**
- Create: `src/xpgraph/core/__init__.py`
- Create: `src/xpgraph/core/ids.py`
- Create: `src/xpgraph/core/base.py`
- Create: `src/xpgraph/errors.py`
- Test: `tests/unit/core/__init__.py`
- Test: `tests/unit/core/test_ids.py`
- Test: `tests/unit/core/test_base.py`

**Step 1: Write failing tests for ID generation**

```python
# tests/unit/core/test_ids.py
from xpgraph.core.ids import generate_ulid, generate_prefixed_id, ulid_to_timestamp

def test_generate_ulid_returns_26_chars():
    uid = generate_ulid()
    assert len(uid) == 26
    assert isinstance(uid, str)

def test_generate_ulid_unique():
    ids = {generate_ulid() for _ in range(100)}
    assert len(ids) == 100

def test_generate_prefixed_id():
    pid = generate_prefixed_id("trace")
    assert pid.startswith("trace_")
    assert len(pid) > 7

def test_ulid_to_timestamp():
    uid = generate_ulid()
    ts = ulid_to_timestamp(uid)
    assert isinstance(ts, float)
    assert ts > 0

def test_ulid_to_timestamp_with_prefix():
    pid = generate_prefixed_id("ev")
    ts = ulid_to_timestamp(pid)
    assert isinstance(ts, float)
    assert ts > 0
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/core/test_ids.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'xpgraph'`

**Step 3: Implement IDs module**

Port directly from agent-kernel `src/agent_kernel/core/ids.py`:

```python
# src/xpgraph/core/ids.py
"""ULID-based ID generation utilities."""

from __future__ import annotations

import ulid


def generate_ulid() -> str:
    """Generate a new ULID string."""
    return str(ulid.new())


def generate_prefixed_id(prefix: str) -> str:
    """Generate a prefixed ULID (e.g., 'trace_01J...')."""
    return f"{prefix}_{ulid.new()}"


def ulid_to_timestamp(ulid_str: str) -> float:
    """Extract Unix timestamp from a ULID string.

    Handles prefixed IDs (e.g., 'trace_01J...') by stripping the prefix.
    """
    raw = ulid_str.split("_", 1)[-1] if "_" in ulid_str else ulid_str
    return ulid.from_str(raw).timestamp().timestamp
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/core/test_ids.py -v
```

Expected: PASS

**Step 5: Write failing tests for base models**

```python
# tests/unit/core/test_base.py
from datetime import datetime, timezone
from xpgraph.core.base import XPModel, VersionedModel, TimestampedModel, utc_now

def test_utc_now_returns_utc():
    now = utc_now()
    assert now.tzinfo == timezone.utc

def test_xp_model_forbids_extra():
    import pytest
    class M(XPModel):
        x: int = 1
    with pytest.raises(Exception):
        M(x=1, y=2)

def test_versioned_model_has_schema_version():
    m = VersionedModel()
    assert isinstance(m.schema_version, str)
    assert len(m.schema_version) > 0

def test_timestamped_model_has_timestamps():
    m = TimestampedModel()
    assert isinstance(m.created_at, datetime)
    assert isinstance(m.updated_at, datetime)
```

**Step 6: Implement base models**

```python
# src/xpgraph/core/base.py
"""Base Pydantic models for Experience Graph schemas."""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field


SCHEMA_VERSION = "0.1.0"


def utc_now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(tz=timezone.utc)


@lru_cache(maxsize=1)
def get_version() -> str:
    """Get the xpgraph package version."""
    try:
        from xpgraph._version import __version__
        return __version__
    except ImportError:
        return "0.0.0-dev"


class XPModel(BaseModel):
    """Base model for all Experience Graph schemas."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_default=True,
        populate_by_name=True,
    )


class VersionedModel(XPModel):
    """Model with schema and package version tracking."""

    schema_version: str = Field(default=SCHEMA_VERSION)


class TimestampedModel(XPModel):
    """Model with created_at and updated_at timestamps."""

    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
```

**Step 7: Run tests**

```bash
pytest tests/unit/core/ -v
```

Expected: PASS

**Step 8: Implement error hierarchy**

```python
# src/xpgraph/errors.py
"""Exception hierarchy for Experience Graph."""


class XPGraphError(Exception):
    """Base exception for all Experience Graph errors."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or "XPGRAPH_ERROR"


# Schema errors
class ValidationError(XPGraphError):
    """Raised when schema validation fails."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message, code="VALIDATION_ERROR")
        self.errors = errors or []


# Store errors
class StoreError(XPGraphError):
    """Raised when a store operation fails."""

    def __init__(self, message: str, store: str | None = None) -> None:
        super().__init__(message, code="STORE_ERROR")
        self.store = store


class NotFoundError(StoreError):
    """Raised when a requested entity is not found."""

    def __init__(self, entity_type: str, entity_id: str) -> None:
        super().__init__(
            f"{entity_type} not found: {entity_id}",
            store=entity_type,
        )
        self.entity_type = entity_type
        self.entity_id = entity_id


# Mutation pipeline errors
class MutationError(XPGraphError):
    """Raised when a mutation fails."""

    def __init__(self, message: str, command_id: str | None = None) -> None:
        super().__init__(message, code="MUTATION_ERROR")
        self.command_id = command_id


class PolicyViolationError(MutationError):
    """Raised when a mutation violates policy."""

    def __init__(self, message: str, policy_id: str | None = None) -> None:
        super().__init__(message)
        self.code = "POLICY_VIOLATION"
        self.policy_id = policy_id


class ApprovalRequiredError(MutationError):
    """Raised when a mutation requires approval."""

    def __init__(self, message: str, approval_id: str | None = None) -> None:
        super().__init__(message)
        self.code = "APPROVAL_REQUIRED"
        self.approval_id = approval_id


class IdempotencyError(MutationError):
    """Raised when a duplicate mutation is detected."""

    def __init__(self, idempotency_key: str) -> None:
        super().__init__(f"Duplicate command: {idempotency_key}")
        self.code = "DUPLICATE_COMMAND"
        self.idempotency_key = idempotency_key
```

**Step 9: Commit**

```bash
git add -A
git commit -m "feat: core utilities — IDs, base models, error hierarchy"
```

---

### Task 1.3: Core Entity Schemas — Trace + Entity

**Files:**
- Create: `src/xpgraph/schemas/__init__.py`
- Create: `src/xpgraph/schemas/trace.py`
- Create: `src/xpgraph/schemas/entity.py`
- Create: `src/xpgraph/schemas/enums.py`
- Test: `tests/unit/schemas/__init__.py`
- Test: `tests/unit/schemas/test_trace.py`
- Test: `tests/unit/schemas/test_entity.py`

**Step 1: Write failing tests for Trace schema**

```python
# tests/unit/schemas/test_trace.py
import pytest
from xpgraph.schemas.trace import (
    Trace, TraceStep, TraceSource, Outcome, OutcomeStatus,
    Feedback, TraceContext, EvidenceRef, ArtifactRef,
)

def test_trace_creates_with_defaults():
    t = Trace(
        source=TraceSource.AGENT,
        intent="deploy service",
        steps=[],
        context=TraceContext(agent_id="agent-1", domain="platform"),
    )
    assert t.trace_id
    assert t.source == TraceSource.AGENT
    assert t.steps == []
    assert t.outcome is None
    assert t.feedback == []

def test_trace_with_steps():
    step = TraceStep(
        step_type="tool_call",
        name="kubectl apply",
        args={"manifest": "deploy.yaml"},
        result={"status": "applied"},
        duration_ms=1200,
    )
    t = Trace(
        source=TraceSource.AGENT,
        intent="deploy",
        steps=[step],
        context=TraceContext(agent_id="a1"),
    )
    assert len(t.steps) == 1
    assert t.steps[0].name == "kubectl apply"

def test_trace_with_outcome():
    t = Trace(
        source=TraceSource.HUMAN,
        intent="review PR",
        steps=[],
        context=TraceContext(agent_id="human-1"),
        outcome=Outcome(status=OutcomeStatus.SUCCESS, metrics={"time_s": 30}),
    )
    assert t.outcome.status == OutcomeStatus.SUCCESS

def test_trace_with_evidence_refs():
    t = Trace(
        source=TraceSource.WORKFLOW,
        intent="enrich",
        steps=[],
        context=TraceContext(agent_id="enricher"),
        evidence_used=[EvidenceRef(evidence_id="ev_123", role="input")],
        artifacts_produced=[ArtifactRef(artifact_id="art_456", artifact_type="report")],
    )
    assert len(t.evidence_used) == 1
    assert len(t.artifacts_produced) == 1

def test_trace_forbids_extra_fields():
    with pytest.raises(Exception):
        Trace(
            source=TraceSource.AGENT,
            intent="x",
            steps=[],
            context=TraceContext(agent_id="a"),
            bogus_field="nope",
        )
```

**Step 2: Run tests — expect failure**

```bash
pytest tests/unit/schemas/test_trace.py -v
```

**Step 3: Implement enums and Trace schema**

```python
# src/xpgraph/schemas/enums.py
"""Shared enums for Experience Graph schemas."""

from enum import StrEnum


class TraceSource(StrEnum):
    """Source of a trace."""
    AGENT = "agent"
    HUMAN = "human"
    WORKFLOW = "workflow"
    SYSTEM = "system"


class OutcomeStatus(StrEnum):
    """Outcome status of a trace."""
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class EntityType(StrEnum):
    """Types of entities in the graph."""
    PERSON = "person"
    SYSTEM = "system"
    SERVICE = "service"
    TEAM = "team"
    DOCUMENT = "document"
    CONCEPT = "concept"
    DOMAIN = "domain"
    FILE = "file"
    PROJECT = "project"
    TOOL = "tool"


class EvidenceType(StrEnum):
    """Types of evidence."""
    DOCUMENT = "document"
    SNIPPET = "snippet"
    LINK = "link"
    CONFIG = "config"
    IMAGE = "image"
    FILE_POINTER = "file_pointer"


class PolicyType(StrEnum):
    """Types of policies."""
    MUTATION = "mutation"
    ACCESS = "access"
    RETENTION = "retention"
    REDACTION = "redaction"


class Enforcement(StrEnum):
    """Policy enforcement levels."""
    ENFORCE = "enforce"
    WARN = "warn"
    AUDIT_ONLY = "audit_only"


class EdgeKind(StrEnum):
    """Types of edges in the graph."""
    # Trace relationships
    TRACE_USED_EVIDENCE = "trace_used_evidence"
    TRACE_PRODUCED_ARTIFACT = "trace_produced_artifact"
    TRACE_TOUCHED_ENTITY = "trace_touched_entity"
    TRACE_PROMOTED_TO_PRECEDENT = "trace_promoted_to_precedent"

    # Entity relationships
    ENTITY_RELATED_TO = "entity_related_to"
    ENTITY_PART_OF = "entity_part_of"
    ENTITY_DEPENDS_ON = "entity_depends_on"

    # Evidence relationships
    EVIDENCE_ATTACHED_TO = "evidence_attached_to"
    EVIDENCE_SUPPORTS = "evidence_supports"

    # Precedent relationships
    PRECEDENT_APPLIES_TO = "precedent_applies_to"
    PRECEDENT_DERIVED_FROM = "precedent_derived_from"
```

```python
# src/xpgraph/schemas/trace.py
"""Trace schema — record of something that happened."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from xpgraph.core.base import VersionedModel, TimestampedModel, utc_now
from xpgraph.core.ids import generate_ulid
from xpgraph.schemas.enums import TraceSource, OutcomeStatus


class EvidenceRef(VersionedModel):
    """Reference to evidence used in a trace."""
    evidence_id: str
    role: str = "input"  # input, reference, context


class ArtifactRef(VersionedModel):
    """Reference to an artifact produced by a trace."""
    artifact_id: str
    artifact_type: str  # report, file, node, edge


class TraceStep(VersionedModel):
    """A single step within a trace."""
    step_type: str  # tool_call, llm_call, decision, observation
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration_ms: int | None = None
    started_at: datetime = Field(default_factory=utc_now)


class Outcome(VersionedModel):
    """Outcome of a trace."""
    status: OutcomeStatus = OutcomeStatus.UNKNOWN
    metrics: dict[str, Any] = Field(default_factory=dict)
    summary: str | None = None


class Feedback(VersionedModel):
    """Feedback on a trace (human or automated)."""
    feedback_id: str = Field(default_factory=generate_ulid)
    rating: float | None = None  # 0.0-1.0
    label: str | None = None  # good, bad, needs_review
    comment: str | None = None
    given_by: str = "unknown"
    given_at: datetime = Field(default_factory=utc_now)


class TraceContext(VersionedModel):
    """Context metadata for a trace."""
    agent_id: str | None = None
    team: str | None = None
    domain: str | None = None
    workflow_id: str | None = None
    parent_trace_id: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None


class Trace(TimestampedModel, VersionedModel):
    """A record of something that happened.

    Central entity of the experience graph. Represents an agent run,
    human action, workflow execution, or system event.
    """
    trace_id: str = Field(default_factory=generate_ulid)
    source: TraceSource
    intent: str
    steps: list[TraceStep] = Field(default_factory=list)
    evidence_used: list[EvidenceRef] = Field(default_factory=list)
    artifacts_produced: list[ArtifactRef] = Field(default_factory=list)
    outcome: Outcome | None = None
    feedback: list[Feedback] = Field(default_factory=list)
    context: TraceContext
    metadata: dict[str, Any] = Field(default_factory=dict)
```

**Step 4: Run trace tests**

```bash
pytest tests/unit/schemas/test_trace.py -v
```

Expected: PASS

**Step 5: Write failing tests for Entity schema**

```python
# tests/unit/schemas/test_entity.py
import pytest
from xpgraph.schemas.entity import Entity, EntitySource
from xpgraph.schemas.enums import EntityType

def test_entity_creates_with_defaults():
    e = Entity(
        entity_type=EntityType.SERVICE,
        name="auth-service",
    )
    assert e.entity_id
    assert e.entity_type == EntityType.SERVICE
    assert e.name == "auth-service"
    assert e.properties == {}

def test_entity_with_properties():
    e = Entity(
        entity_type=EntityType.PERSON,
        name="Alice",
        properties={"role": "SRE", "team": "platform"},
        source=EntitySource(origin="manual", detail="added by admin"),
    )
    assert e.properties["role"] == "SRE"
    assert e.source.origin == "manual"

def test_entity_forbids_extra():
    with pytest.raises(Exception):
        Entity(entity_type=EntityType.SYSTEM, name="x", nope="bad")
```

**Step 6: Implement Entity schema**

```python
# src/xpgraph/schemas/entity.py
"""Entity schema — a node in the experience graph."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from xpgraph.core.base import VersionedModel, TimestampedModel
from xpgraph.core.ids import generate_ulid
from xpgraph.schemas.enums import EntityType


class EntitySource(VersionedModel):
    """How an entity entered the graph."""
    origin: str  # manual, ingestion, trace, enrichment
    detail: str | None = None
    trace_id: str | None = None


class Entity(TimestampedModel, VersionedModel):
    """A node in the experience graph.

    Represents a person, system, concept, service, team, document,
    or any other thing that traces reference.
    """
    entity_id: str = Field(default_factory=generate_ulid)
    entity_type: EntityType
    name: str
    properties: dict[str, Any] = Field(default_factory=dict)
    source: EntitySource | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
```

**Step 7: Run all schema tests**

```bash
pytest tests/unit/schemas/ -v
```

Expected: PASS

**Step 8: Commit**

```bash
git add -A
git commit -m "feat: trace and entity schemas with enums"
```

---

### Task 1.4: Core Entity Schemas — Evidence, Precedent, Policy, Pack

**Files:**
- Create: `src/xpgraph/schemas/evidence.py`
- Create: `src/xpgraph/schemas/precedent.py`
- Create: `src/xpgraph/schemas/policy.py`
- Create: `src/xpgraph/schemas/pack.py`
- Create: `src/xpgraph/schemas/graph.py` (edge schema)
- Test: `tests/unit/schemas/test_evidence.py`
- Test: `tests/unit/schemas/test_precedent.py`
- Test: `tests/unit/schemas/test_policy.py`
- Test: `tests/unit/schemas/test_pack.py`

**Step 1: Write failing tests for Evidence**

```python
# tests/unit/schemas/test_evidence.py
from xpgraph.schemas.evidence import Evidence, AttachmentRef
from xpgraph.schemas.enums import EvidenceType

def test_evidence_inline():
    e = Evidence(
        evidence_type=EvidenceType.SNIPPET,
        content="SELECT * FROM users",
        source_origin="trace",
    )
    assert e.evidence_id
    assert e.content == "SELECT * FROM users"
    assert e.uri is None
    assert e.content_hash  # auto-computed

def test_evidence_pointer():
    e = Evidence(
        evidence_type=EvidenceType.FILE_POINTER,
        uri="s3://bucket/report.pdf",
        source_origin="ingestion",
    )
    assert e.uri == "s3://bucket/report.pdf"
    assert e.content is None

def test_evidence_with_attachments():
    e = Evidence(
        evidence_type=EvidenceType.DOCUMENT,
        content="Architecture doc",
        source_origin="manual",
        attached_to=[
            AttachmentRef(target_id="entity_123", target_type="entity"),
            AttachmentRef(target_id="trace_456", target_type="trace"),
        ],
    )
    assert len(e.attached_to) == 2
```

**Step 2: Implement Evidence schema**

```python
# src/xpgraph/schemas/evidence.py
"""Evidence schema — provenance-tracked artifacts."""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import Field, model_validator

from xpgraph.core.base import VersionedModel, TimestampedModel
from xpgraph.core.ids import generate_ulid
from xpgraph.schemas.enums import EvidenceType


class AttachmentRef(VersionedModel):
    """Reference to what this evidence is attached to."""
    target_id: str
    target_type: str  # trace, entity, precedent


class Evidence(TimestampedModel, VersionedModel):
    """A provenance-tracked artifact.

    Evidence is always connected to something (trace, entity, precedent).
    Can be inline (small content) or a pointer (URI to external resource).
    """
    evidence_id: str = Field(default_factory=generate_ulid)
    evidence_type: EvidenceType
    content: str | None = None
    uri: str | None = None
    content_hash: str = ""
    source_origin: str  # trace, manual, ingestion
    source_trace_id: str | None = None
    attached_to: list[AttachmentRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _compute_hash(self) -> Evidence:
        if self.content and not self.content_hash:
            self.content_hash = hashlib.sha256(
                self.content.encode()
            ).hexdigest()[:16]
        return self
```

**Step 3: Write failing tests for Precedent, implement, run**

```python
# tests/unit/schemas/test_precedent.py
from xpgraph.schemas.precedent import Precedent
from xpgraph.schemas.trace import Feedback

def test_precedent_creates():
    p = Precedent(
        source_trace_ids=["t1", "t2"],
        title="Always run migrations before deploy",
        description="Learned from 3 incidents where skipped migrations caused outages.",
        promoted_by="sre-agent",
    )
    assert p.precedent_id
    assert len(p.source_trace_ids) == 2
    assert p.confidence == 0.0  # default

def test_precedent_with_feedback():
    p = Precedent(
        source_trace_ids=["t1"],
        title="Check locks",
        description="Check advisory locks before DDL.",
        promoted_by="human",
        feedback=[Feedback(rating=0.9, given_by="alice")],
    )
    assert p.feedback[0].rating == 0.9
```

```python
# src/xpgraph/schemas/precedent.py
"""Precedent schema — curated institutional knowledge."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from xpgraph.core.base import VersionedModel, TimestampedModel
from xpgraph.core.ids import generate_ulid
from xpgraph.schemas.trace import Feedback


class Precedent(TimestampedModel, VersionedModel):
    """Promoted/curated institutional knowledge derived from traces.

    A precedent is "graduated" from raw traces — it has provenance
    back to the source data and represents a generalized lesson.
    """
    precedent_id: str = Field(default_factory=generate_ulid)
    source_trace_ids: list[str] = Field(default_factory=list)
    title: str
    description: str
    pattern: str | None = None
    applicability: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    promoted_by: str
    evidence_refs: list[str] = Field(default_factory=list)
    feedback: list[Feedback] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
```

**Step 4: Write failing tests for Policy, implement, run**

```python
# tests/unit/schemas/test_policy.py
from xpgraph.schemas.policy import Policy, PolicyRule, PolicyScope
from xpgraph.schemas.enums import PolicyType, Enforcement

def test_policy_creates():
    p = Policy(
        policy_type=PolicyType.MUTATION,
        scope=PolicyScope(level="domain", value="platform"),
        rules=[PolicyRule(
            operation="precedent.promote",
            condition="always",
            action="require_approval",
        )],
        enforcement=Enforcement.ENFORCE,
    )
    assert p.policy_id
    assert len(p.rules) == 1

def test_policy_audit_only():
    p = Policy(
        policy_type=PolicyType.RETENTION,
        scope=PolicyScope(level="global"),
        rules=[],
        enforcement=Enforcement.AUDIT_ONLY,
    )
    assert p.enforcement == Enforcement.AUDIT_ONLY
```

```python
# src/xpgraph/schemas/policy.py
"""Policy schema — governance rules for mutations."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from xpgraph.core.base import VersionedModel, TimestampedModel
from xpgraph.core.ids import generate_ulid
from xpgraph.schemas.enums import PolicyType, Enforcement


class PolicyScope(VersionedModel):
    """Scope of a policy."""
    level: str  # global, domain, team, entity_type
    value: str | None = None  # e.g., "platform" for domain-level


class PolicyRule(VersionedModel):
    """A single rule within a policy."""
    operation: str  # e.g., "precedent.promote", "entity.create", "*"
    condition: str = "always"  # always, if_external, if_high_risk
    action: str = "allow"  # allow, deny, require_approval, warn
    params: dict[str, Any] = Field(default_factory=dict)


class Policy(TimestampedModel, VersionedModel):
    """Governance rules for the write pipeline.

    Policies are matched by scope and applied during the
    Policy Check stage of the mutation pipeline.
    """
    policy_id: str = Field(default_factory=generate_ulid)
    policy_type: PolicyType
    scope: PolicyScope
    rules: list[PolicyRule] = Field(default_factory=list)
    enforcement: Enforcement = Enforcement.ENFORCE
    metadata: dict[str, Any] = Field(default_factory=dict)
```

**Step 5: Write failing tests for Pack, implement, run**

```python
# tests/unit/schemas/test_pack.py
from xpgraph.schemas.pack import Pack, PackItem, PackBudget, RetrievalReport

def test_pack_creates():
    p = Pack(
        intent="deploy checklist",
        items=[PackItem(
            item_id="trace_123",
            item_type="trace",
            excerpt="Ran deploy with --dry-run first",
            relevance_score=0.92,
        )],
    )
    assert p.pack_id
    assert len(p.items) == 1
    assert p.items[0].relevance_score == 0.92

def test_pack_with_budget():
    p = Pack(
        intent="incident response",
        items=[],
        budget=PackBudget(max_items=20, max_tokens=4000),
    )
    assert p.budget.max_items == 20

def test_pack_with_retrieval_report():
    p = Pack(
        intent="test",
        items=[],
        retrieval_report=RetrievalReport(
            queries_run=3,
            candidates_found=50,
            items_selected=5,
            duration_ms=120,
        ),
    )
    assert p.retrieval_report.queries_run == 3
```

```python
# src/xpgraph/schemas/pack.py
"""Pack schema — retrieval bundles for task-scoped context."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from xpgraph.core.base import VersionedModel, TimestampedModel, utc_now
from xpgraph.core.ids import generate_ulid


class PackItem(VersionedModel):
    """A single item in a retrieval pack."""
    item_id: str
    item_type: str  # trace, evidence, precedent, entity
    excerpt: str = ""
    relevance_score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class PackBudget(VersionedModel):
    """Budget constraints for pack assembly."""
    max_items: int = 50
    max_tokens: int = 8000


class RetrievalReport(VersionedModel):
    """Report on how a pack was assembled."""
    queries_run: int = 0
    candidates_found: int = 0
    items_selected: int = 0
    duration_ms: int = 0
    strategies_used: list[str] = Field(default_factory=list)


class Pack(TimestampedModel, VersionedModel):
    """A retrieval bundle assembled for a specific task/agent/domain.

    The output of the retrieval system. Contains traces, evidence,
    precedents, and entities relevant to a given intent.
    """
    pack_id: str = Field(default_factory=generate_ulid)
    intent: str
    items: list[PackItem] = Field(default_factory=list)
    retrieval_report: RetrievalReport = Field(default_factory=RetrievalReport)
    policies_applied: list[str] = Field(default_factory=list)
    budget: PackBudget = Field(default_factory=PackBudget)
    domain: str | None = None
    agent_id: str | None = None
    assembled_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)
```

**Step 6: Write graph edge schema**

```python
# src/xpgraph/schemas/graph.py
"""Graph edge schema for typed relationships."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from xpgraph.core.base import VersionedModel, TimestampedModel
from xpgraph.core.ids import generate_ulid
from xpgraph.schemas.enums import EdgeKind


class Edge(TimestampedModel, VersionedModel):
    """A typed edge in the experience graph."""
    edge_id: str = Field(default_factory=generate_ulid)
    source_id: str
    target_id: str
    edge_kind: EdgeKind
    properties: dict[str, Any] = Field(default_factory=dict)
```

**Step 7: Create schemas __init__.py with all exports**

```python
# src/xpgraph/schemas/__init__.py
"""Experience Graph schemas — the six core entities."""

from xpgraph.schemas.enums import (
    EdgeKind,
    Enforcement,
    EntityType,
    EvidenceType,
    OutcomeStatus,
    PolicyType,
    TraceSource,
)
from xpgraph.schemas.trace import (
    ArtifactRef,
    EvidenceRef,
    Feedback,
    Outcome,
    Trace,
    TraceContext,
    TraceStep,
)
from xpgraph.schemas.entity import Entity, EntitySource
from xpgraph.schemas.evidence import AttachmentRef, Evidence
from xpgraph.schemas.precedent import Precedent
from xpgraph.schemas.policy import Policy, PolicyRule, PolicyScope
from xpgraph.schemas.pack import Pack, PackBudget, PackItem, RetrievalReport
from xpgraph.schemas.graph import Edge

__all__ = [
    # Enums
    "EdgeKind", "Enforcement", "EntityType", "EvidenceType",
    "OutcomeStatus", "PolicyType", "TraceSource",
    # Trace
    "Trace", "TraceStep", "TraceContext", "Outcome", "Feedback",
    "EvidenceRef", "ArtifactRef",
    # Entity
    "Entity", "EntitySource",
    # Evidence
    "Evidence", "AttachmentRef",
    # Precedent
    "Precedent",
    # Policy
    "Policy", "PolicyRule", "PolicyScope",
    # Pack
    "Pack", "PackItem", "PackBudget", "RetrievalReport",
    # Graph
    "Edge",
]
```

**Step 8: Run all tests**

```bash
pytest tests/ -v
```

Expected: ALL PASS

**Step 9: Run lint + typecheck**

```bash
make lint
make typecheck
```

**Step 10: Commit**

```bash
git add -A
git commit -m "feat: complete core schema layer — all 6 entities + graph edges"
```

---

### Task 1.5: Push to GitHub

**Step 1: Create GitHub repo**

```bash
gh repo create experience-graph --public --description "A shared experience store: traces + provenance + curated precedent + retrieval packs"
```

**Step 2: Push**

```bash
git remote add origin <url>
git push -u origin main
```

**Step 3: Verify CI passes**

```bash
gh run list --limit 1
```

---

## Phase 2: Port Stores (Outline)

Port the SQLite store backends from agent-kernel, adapting interfaces to the new schemas. Each store gets its own task.

### Task 2.1: Document Store
- Port `SQLiteDocumentStore` → `xpgraph/stores/document.py`
- Keep ABC + SQLite impl pattern
- Update method signatures: `put/get/search/delete/list/count/close`
- Add: `get_by_hash(content_hash: str)` for dedup
- Tests: port from `tests/unit/memory/test_document_store.py`

### Task 2.2: Graph Store
- Port `SQLiteGraphStore` → `xpgraph/stores/graph.py`
- Keep ABC + SQLite impl
- Edge types use new `EdgeKind` enum
- Node types use new `EntityType` enum
- Tests: port from `tests/unit/memory/test_graph_store.py`

### Task 2.3: Vector Store
- Port `SQLiteVectorStore` → `xpgraph/stores/vector.py`
- Keep ABC + SQLite impl + optional LanceDB
- Tests: port from `tests/unit/memory/test_vector_store.py`

### Task 2.4: Event Log
- Port `SQLiteEventLog` → `xpgraph/stores/event_log.py`
- Update `EventType` enum for new domain (trace.ingested, entity.created, precedent.promoted, etc.)
- Tests: port from `tests/unit/memory/test_event_log.py`

### Task 2.5: Trace Store
- New store for immutable trace records
- `TraceStore` ABC with `append(trace: Trace)`, `get(trace_id: str)`, `query(...)`, `count()`
- SQLite implementation
- Tests: new

---

## Phase 3: Retrieval / Pack Builder (Outline)

### Task 3.1: Pack Builder
- Port `ContextAssembler` → `xpgraph/retrieve/pack_builder.py`
- Rename context → pack vocabulary throughout
- Keep: importance weighting, hybrid search, budget management
- Input: intent + domain + agent_id + budget
- Output: `Pack` schema
- Tests: port and adapt from `tests/unit/context/`

### Task 3.2: Search Strategies
- Port keyword search, semantic search, graph traversal strategies
- Each returns `list[PackItem]` ranked by relevance
- Importance weighting: `base_score * (1.0 + importance)`
- Tests: new

---

## Phase 4: Mutation Pipeline (Outline)

### Task 4.1: Command + Operation schemas
- `Command`, `CommandBatch`, `CommandResult` models
- `Operation` enum with all mutation verbs
- `OperationRegistry` — validates operation + args schema
- Tests: schema validation

### Task 4.2: MutationExecutor
- Pipeline: Validate → Policy Check → Execute → Trace → Emit
- Port idempotency from `tools/idempotency.py`
- Port circuit breaker pattern from `tools/retry.py`
- Tests: unit tests per pipeline stage

### Task 4.3: PolicyGate
- Port approval gate pattern from `executor/approval.py`
- Match policies by scope (global > domain > team > entity_type)
- Enforcement levels: enforce, warn, audit_only
- Tests: policy matching, approval flow

---

## Phase 5: CLI (Outline)

### Task 5.1: CLI scaffolding
- Typer app with 6 command groups
- `--format` (text/json), `--domain`, `--agent` global options
- `xpg admin init` — creates stores + config

### Task 5.2: Ingest commands
- `xpg ingest trace`, `xpg ingest evidence`, `xpg ingest evidence-dir`
- Read from file/stdin, validate schema, submit as Command

### Task 5.3: Curate commands
- `xpg curate promote`, `link`, `label`, `attach`, `merge`, `feedback`

### Task 5.4: Retrieve commands
- `xpg retrieve pack`, `search`, `entity`, `trace`, `precedents`

### Task 5.5: Admin commands
- `init`, `migrate`, `policy`, `retention`, `redact`, `health`, `export`

### Task 5.6: Analyze commands
- `paths`, `outcomes`, `drift`, `gaps`

---

## Phase 6: Port Workers (Outline)

### Task 6.1: Enrichment worker
- Port `EnrichmentService` → `xpgraph_workers/enrichment/`
- Importance scoring, auto-tagging, classification
- Works against new schemas

### Task 6.2: Learning worker
- Port `ExperienceMiner` patterns
- Outcome analysis, precedent extraction from traces

### Task 6.3: Maintenance workers
- Retention pruning, reconciliation, staleness detection
- Port file indexer for evidence ingestion

### Task 6.4: Engine adaptation
- Port `CustomEngine` + thinking policy for internal curation workflows
- Not exposed as public API — powers workers only

---

## Phase 7: Integrations + Archive (Outline)

### Task 7.1: Obsidian integration
- Port vault indexer → `integrations/obsidian/`
- Separate PyPI package: `xpgraph-obsidian`

### Task 7.2: Archive agent-kernel
- Update agent-kernel README to point to experience-graph
- Mark as archived on GitHub

---

## Execution Order

Phases 1-2 are the foundation and should be completed first, sequentially. Phases 3-5 can partially overlap. Phase 6 depends on 3+4. Phase 7 is last.

**Estimated tasks:** Phase 1 (5 tasks) → Phase 2 (5 tasks) → Phase 3 (2 tasks) → Phase 4 (3 tasks) → Phase 5 (6 tasks) → Phase 6 (4 tasks) → Phase 7 (2 tasks) = **27 tasks total**

Phase 1 is fully specified above with TDD steps. Remaining phases should get their own detailed plans (with full code) when execution reaches them, to avoid planning against assumptions that may change.
