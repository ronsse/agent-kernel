# Skills Portability Guide: Making Agent Kernel Packages Universally Usable

**Version:** 1.0
**Date:** 2026-01-24
**Purpose:** Explain why skills are the most effective portability strategy for making agent kernel packages usable by any AI agent system.

---

## Table of Contents

1. [The Portability Problem](#1-the-portability-problem)
2. [Why Skills Are Effective](#2-why-skills-are-effective)
3. [Skill Architecture](#3-skill-architecture)
4. [Skills for Each Package](#4-skills-for-each-package)
5. [Using Skills with AI Agents](#5-using-skills-with-ai-agents)
6. [Comparison with Other Approaches](#6-comparison-with-other-approaches)
7. [Best Practices](#7-best-practices)

---

## 1. The Portability Problem

### The Challenge

You have powerful, modular packages (`agent-kernel-core`, `agent-kernel-memory`, etc.) that you want:

1. **AI agents to discover and use** - Without manual integration
2. **Portable across agent frameworks** - Works with Claude, GPT, custom agents
3. **Self-documenting** - Clear interface without reading code
4. **Easy to compose** - Chain skills together
5. **Safe to execute** - Sandboxed, validated I/O

### Traditional Approaches (Limitations)

| Approach | Problem |
|----------|---------|
| **Python API** | Requires Python runtime, agent must understand Python |
| **REST API** | Requires running server, network dependency |
| **MCP Server** | Requires MCP protocol support in agent |
| **Documentation** | Agent must interpret prose, no standard interface |
| **Code examples** | Agent must adapt code, error-prone |

---

## 2. Why Skills Are Effective

### The Skills Pattern

**A skill is a self-contained executable that:**

1. **Reads JSON from stdin** - Standard input format
2. **Performs one clear operation** - Single responsibility
3. **Writes JSON to stdout** - Standard output format
4. **Includes help in header** - Self-documenting
5. **Handles errors gracefully** - Structured error messages

### Why This Works

#### ✅ **Universal Interface**

```bash
# Any agent system can do this:
echo '{"query": "AI agents", "top_k": 5}' | python skill.py
```

- No Python imports needed
- No REST endpoints
- No special protocols
- Just stdin/stdout (universal)

#### ✅ **Self-Documenting**

```python
"""
Skill: Vector Search

Input:  {"query": "...", "top_k": 10}
Output: {"results": [...], "count": 5}
"""
```

Agents can:
- Read the docstring to understand usage
- See input/output examples
- Try with test data

#### ✅ **Composable**

```bash
# Chain skills together
cat data.json \
  | python skills/memory/vector_search.py \
  | python skills/tools/execute_capability.py \
  | python skills/memory/store_result.py
```

Each skill is a Unix-style filter.

#### ✅ **Language Agnostic**

```bash
# Skills can be in any language
python skills/memory/vector_search.py      # Python
node skills/tools/http_tool.js             # JavaScript
./skills/executor/validate_plan            # Go binary
bash skills/utils/format_output.sh         # Bash
```

Agents don't care about implementation language.

#### ✅ **Sandboxed by Default**

```bash
# Skills run in separate processes
python skill.py < input.json > output.json

# Can further sandbox with:
# - Docker containers
# - Virtual environments
# - Resource limits (timeout, memory)
```

#### ✅ **Discoverable**

```bash
# Agents can discover skills by scanning directory
ls skills/**/*.py

# Each skill has JSON manifest
{
  "skill_id": "memory.vector_search@v1",
  "description": "Semantic similarity search",
  "input_schema": {...},
  "output_schema": {...},
  "examples": [...]
}
```

---

## 3. Skill Architecture

### Skill Structure

```
skill_name.py
├── Shebang (#!/usr/bin/env python3)
├── Docstring (help text with I/O examples)
├── Imports (package dependencies)
├── Input reading (JSON from stdin)
├── Business logic (using agent-kernel packages)
├── Output writing (JSON to stdout)
└── Error handling (structured errors to stdout)
```

### Example Skill Template

```python
#!/usr/bin/env python3
"""
Skill: [Name]

Description: [What it does]

Input (JSON):
{
    "param1": "description",
    "param2": 123
}

Output (JSON):
{
    "success": true,
    "result": "data"
}

Error (JSON):
{
    "success": false,
    "error": "error message",
    "error_type": "ErrorClass"
}
"""

import asyncio
import json
import sys

# Import agent-kernel package
from agent_kernel_[package] import Component


async def main():
    try:
        # 1. Read input
        input_data = json.loads(sys.stdin.read())

        # 2. Validate required params
        required = ["param1", "param2"]
        for param in required:
            if param not in input_data:
                raise ValueError(f"Missing required parameter: {param}")

        # 3. Execute using agent-kernel package
        component = Component(...)
        result = await component.operation(input_data["param1"])

        # 4. Success output
        output = {
            "success": True,
            "result": result,
            "metadata": {...},
        }
        print(json.dumps(output, indent=2))

    except Exception as e:
        # 5. Error output
        output = {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
        }
        print(json.dumps(output, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
```

### Skill Manifest (Optional)

```json
{
  "skill_id": "memory.vector_search@v1",
  "name": "Vector Semantic Search",
  "description": "Performs semantic similarity search using embeddings",
  "package": "agent-kernel-memory",
  "version": "1.0.0",
  "input_schema": {
    "type": "object",
    "required": ["query_vector", "top_k"],
    "properties": {
      "query_vector": {
        "type": "array",
        "items": {"type": "number"},
        "description": "Query embedding vector"
      },
      "top_k": {
        "type": "integer",
        "description": "Number of results to return",
        "default": 10
      }
    }
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "success": {"type": "boolean"},
      "results": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "item_id": {"type": "string"},
            "score": {"type": "number"}
          }
        }
      }
    }
  },
  "examples": [
    {
      "input": {"query_vector": [0.1, 0.2, ...], "top_k": 5},
      "output": {"success": true, "results": [...]}
    }
  ]
}
```

---

## 4. Skills for Each Package

### agent-kernel-core

**Skills:**
- `validate_schema.py` - Validate data against Pydantic schema
- `generate_ulid.py` - Generate unique IDs
- `parse_plan.py` - Parse and validate Plan JSON

**Example:**
```bash
echo '{"schema": "Plan", "data": {...}}' | python skills/core/validate_schema.py
# {"success": true, "valid": true, "errors": []}
```

---

### agent-kernel-memory

**Skills:**
- `vector_search.py` - Semantic similarity search
- `document_search.py` - Full-text search
- `graph_traverse.py` - Graph relationship traversal
- `store_document.py` - Add document to store
- `query_events.py` - Query event log

**Example:**
```bash
echo '{
  "query_text": "AI agents",
  "query_vector": [0.1, 0.2, ...],
  "top_k": 10
}' | python skills/memory/vector_search.py

# Output:
# {
#   "success": true,
#   "results": [
#     {"item_id": "doc_123", "score": 0.92},
#     {"item_id": "doc_456", "score": 0.88}
#   ],
#   "count": 2
# }
```

---

### agent-kernel-tools

**Skills:**
- `execute_capability.py` - Execute a tool capability
- `list_capabilities.py` - List available capabilities
- `validate_capability.py` - Validate capability definition

**Example:**
```bash
echo '{
  "capability_name": "market.get_quote@v1",
  "args": {"symbol": "AAPL"}
}' | python skills/tools/execute_capability.py

# Output:
# {
#   "success": true,
#   "output": {"price": 150.5, "volume": 1000000},
#   "duration_ms": 45
# }
```

---

### agent-kernel-engine

**Skills:**
- `generate_plan.py` - Generate plan from context
- `escalate_thinking.py` - Try higher reasoning tier
- `critique_plan.py` - Validate plan with critic

**Example:**
```bash
echo '{
  "intent": "Analyze market trends",
  "context_items": [...],
  "agent_profile_id": "analyst"
}' | python skills/engine/generate_plan.py

# Output:
# {
#   "success": true,
#   "plan": {
#     "reasoning": "Market shows...",
#     "actions": [...],
#     "confidence": 0.85
#   }
# }
```

---

### agent-kernel-executor

**Skills:**
- `execute_plan.py` - Execute a plan
- `validate_plan.py` - Run quality gates
- `dry_run.py` - Test plan without execution

**Example:**
```bash
echo '{
  "plan": {...},
  "context": {...},
  "dry_run": true
}' | python skills/executor/execute_plan.py

# Output:
# {
#   "success": true,
#   "trace_id": "01ARZ...",
#   "status": "DRY_RUN",
#   "would_execute": 3,
#   "would_require_approval": 1
# }
```

---

### agent-kernel-workflows

**Skills:**
- `run_workflow.py` - Execute workflow
- `list_workflows.py` - List available workflows
- `get_workflow_status.py` - Check workflow run status

**Example:**
```bash
echo '{
  "workflow_id": "daily_analysis",
  "intent": "Analyze today",
  "auto_approve_risk": "low"
}' | python skills/workflows/run_workflow.py

# Output:
# {
#   "success": true,
#   "trace_id": "01ARZ...",
#   "status": "COMPLETED",
#   "duration_ms": 2345
# }
```

---

## 5. Using Skills with AI Agents

### Discovery

**Agent discovers available skills:**

```python
# Agent code (any framework)
import os
import json

def discover_skills(skills_dir="skills"):
    """Discover all available skills."""
    skills = []

    for root, dirs, files in os.walk(skills_dir):
        for file in files:
            if file.endswith(".py") and file != "__init__.py":
                skill_path = os.path.join(root, file)

                # Read docstring
                with open(skill_path) as f:
                    first_lines = "".join([next(f) for _ in range(20)])

                    # Extract input/output from docstring
                    # Parse to understand skill capability

                skills.append({
                    "path": skill_path,
                    "name": file.replace(".py", ""),
                    "category": os.path.basename(root),
                    "help": first_lines,
                })

    return skills

# Agent now knows all available skills
skills = discover_skills()
```

### Execution

**Agent executes skill:**

```python
import subprocess
import json

def execute_skill(skill_path, input_data):
    """Execute a skill with JSON input."""
    # Convert input to JSON
    input_json = json.dumps(input_data)

    # Execute skill
    result = subprocess.run(
        ["python", skill_path],
        input=input_json,
        capture_output=True,
        text=True,
        timeout=30,  # Prevent runaway
    )

    # Parse output
    try:
        output = json.loads(result.stdout)
        return output
    except json.JSONDecodeError:
        return {
            "success": False,
            "error": "Skill output was not valid JSON",
            "raw_output": result.stdout,
            "stderr": result.stderr,
        }

# Agent executes skill
output = execute_skill(
    "skills/memory/vector_search.py",
    {"query_vector": [...], "top_k": 10},
)

if output["success"]:
    results = output["results"]
else:
    print(f"Error: {output['error']}")
```

### Chaining

**Agent chains skills together:**

```python
def chain_skills(skill_chain, initial_input):
    """Execute skills in sequence, passing output to next."""
    current_input = initial_input

    for skill_path in skill_chain:
        output = execute_skill(skill_path, current_input)

        if not output["success"]:
            return output  # Stop chain on error

        # Pass output to next skill
        current_input = output

    return current_input

# Example chain
result = chain_skills(
    [
        "skills/memory/vector_search.py",      # Find relevant docs
        "skills/engine/generate_plan.py",      # Generate plan
        "skills/executor/validate_plan.py",    # Validate
        "skills/executor/execute_plan.py",     # Execute
    ],
    initial_input={"query": "market analysis"},
)
```

---

## 6. Comparison with Other Approaches

| Aspect | Skills | Python API | REST API | MCP | Docs |
|--------|--------|-----------|----------|-----|------|
| **Universal** | ✅ Stdin/stdout | ❌ Python only | ✅ HTTP | ⚠️ MCP clients | ❌ No interface |
| **Self-doc** | ✅ Docstring | ⚠️ Docstrings | ⚠️ OpenAPI | ✅ Protocol | ✅ Prose |
| **Sandboxed** | ✅ Process | ❌ Same process | ✅ Network | ✅ Process | N/A |
| **Composable** | ✅ Pipes | ⚠️ Code | ⚠️ HTTP | ❌ Limited | ❌ No |
| **Language-agnostic** | ✅ Any | ❌ Python | ✅ Any | ✅ Any | ✅ Any |
| **Discoverable** | ✅ Scan dir | ❌ Import | ⚠️ Registry | ⚠️ Registry | ❌ Search |
| **Latency** | ~10ms | <1ms | 50-500ms | 10-100ms | N/A |
| **Setup** | None | `pip install` | Run server | Run server | N/A |

**Skills win on:**
- Portability (works everywhere)
- Discoverability (scan directory)
- Sandboxing (separate processes)
- Composability (Unix pipes)

**Trade-off:**
- Slightly higher latency than direct Python API
- But still faster than HTTP/MCP

---

## 7. Best Practices

### 1. One Skill = One Responsibility

```python
# Good: Focused skill
# skills/memory/vector_search.py - Just search

# Bad: Too many responsibilities
# skills/memory/search_and_store_and_update.py - Do 3 things
```

### 2. Always Validate Input

```python
# Check required parameters
required = ["query_vector", "top_k"]
for param in required:
    if param not in input_data:
        raise ValueError(f"Missing: {param}")

# Validate types
if not isinstance(input_data["top_k"], int):
    raise TypeError("top_k must be integer")
```

### 3. Structured Error Output

```python
# Good: Structured error
{
  "success": false,
  "error": "Vector dimension mismatch: got 512, expected 1536",
  "error_type": "DimensionMismatch",
  "help": "Ensure query_vector has 1536 dimensions"
}

# Bad: Unstructured error
"Error: something went wrong"
```

### 4. Include Examples in Docstring

```python
"""
Skill: Vector Search

Examples:

1. Basic search:
   Input:  {"query_vector": [...], "top_k": 5}
   Output: {"success": true, "results": [...]}

2. With filters:
   Input:  {"query_vector": [...], "filters": {"category": "tech"}}
   Output: {"success": true, "results": [...]}
"""
```

### 5. Make Skills Testable

```python
# skills/memory/vector_search_test.py
import subprocess
import json

def test_vector_search():
    input_data = {
        "query_vector": [0.1] * 1536,
        "top_k": 5,
    }

    result = subprocess.run(
        ["python", "skills/memory/vector_search.py"],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )

    output = json.loads(result.stdout)
    assert output["success"] == True
    assert len(output["results"]) <= 5
```

---

## Conclusion: Why Skills Are the Best Portability Strategy

### Summary of Benefits

✅ **Universal** - Works with any agent system (Claude, GPT, custom)
✅ **Self-Documenting** - Docstring explains usage
✅ **Composable** - Chain with Unix pipes
✅ **Sandboxed** - Run in separate processes
✅ **Discoverable** - Scan directory for skills
✅ **Language-Agnostic** - Python, JS, Go, Rust, etc.
✅ **No Dependencies** - Just stdin/stdout
✅ **Easy to Test** - Standard input/output
✅ **Portable** - Copy to any system and run

### Use Skills For

1. **AI Agent Integration** - Make packages usable by any agent
2. **Cross-Framework Portability** - Works with Claude, GPT, LangChain, etc.
3. **Documentation** - Living examples of package usage
4. **Testing** - Integration tests with real I/O
5. **Workflow Automation** - Chain skills in pipelines

### Recommendation

**Create skills for every package** to maximize portability and usability. Skills are:
- The easiest way for AI agents to use your packages
- The best documentation (executable examples)
- The most portable interface (works everywhere)

**Your agent kernel packages are now:**
- ✅ Independently versioned
- ✅ Selectively installable
- ✅ Comprehensively documented
- ✅ Portable via skills (works with any AI agent)

This makes them **production-ready for trading, analytics, and any other domain**.
