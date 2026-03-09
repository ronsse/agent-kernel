# Test Documentation

This document describes the comprehensive test suite for the agent-kernel monorepo.

## Test Structure

```
tests/
├── conftest.py                    # Shared fixtures for all tests
├── TEST_DOCUMENTATION.md          # This file
├── unit/                          # Unit tests for individual components
│   ├── core/                      # Core package tests
│   │   ├── schemas/               # Schema-specific tests (existing)
│   │   └── test_schema_validation.py  # Comprehensive schema validation tests
│   ├── memory/                    # Memory package tests
│   │   ├── test_graph_store.py    # Graph store tests (existing)
│   │   ├── test_graph_traversal.py    # Graph traversal algorithms
│   │   ├── test_vector_store.py   # Vector store tests (existing)
│   │   ├── test_vector_semantic_search.py  # Semantic search tests
│   │   ├── test_document_store.py # Document store tests (existing)
│   │   └── test_event_log.py      # Event log tests (existing)
│   ├── tools/                     # Tools package tests
│   │   ├── test_broker.py         # Tool broker tests (existing)
│   │   ├── test_registry.py       # Capability registry tests (existing)
│   │   ├── test_capability_execution.py  # Capability execution tests
│   │   └── adapters/              # Adapter tests (existing)
│   ├── engine/                    # Engine package tests (existing)
│   │   ├── test_custom_engine.py
│   │   ├── test_critic.py
│   │   └── test_thinking_policy.py
│   ├── executor/                  # Executor package tests (existing)
│   │   ├── test_executor.py
│   │   ├── test_approval.py
│   │   └── test_policies.py
│   ├── context/                   # Context package tests
│   │   ├── test_assembler.py      # Context assembly (existing)
│   │   └── test_context_enrichment.py  # Context enrichment tests
│   └── workflows/                 # Workflows package tests (existing)
│       └── test_workflow_spec.py
├── skills/                        # Tests for portable skills
│   ├── test_vector_search_skill.py       # Vector search skill tests
│   ├── test_execute_capability_skill.py  # Execute capability skill tests
│   └── test_run_workflow_skill.py        # Run workflow skill tests
└── integration/                   # Integration tests
    ├── test_end_to_end_workflow.py       # End-to-end workflow tests
    ├── test_workflow.py           # Workflow integration (existing)
    └── test_embedding_integration.py  # Embedding integration (existing)
```

## Test Coverage

### Core Package Tests

**test_schema_validation.py**
- ✅ ContextRef validation
- ✅ ActionRequest validation
- ✅ Plan validation
- ✅ ContextPacket validation
- ✅ DecisionTrace validation
- ✅ AgentProfile validation
- ✅ CapabilitySpec validation
- ✅ RiskAssessment validation
- ✅ ToolCallRecord validation
- ✅ Enum types (SideEffect, RiskLevel, RefType)
- ✅ Schema serialization/deserialization

### Memory Package Tests

**test_graph_traversal.py**
- ✅ Breadth-first traversal from seed nodes
- ✅ Multi-seed traversal
- ✅ Bidirectional edge traversal
- ✅ Cycle detection and handling
- ✅ Edge type filtering
- ✅ Weighted edges
- ✅ Simple path finding
- ✅ Isolated node handling
- ✅ Relationship strength aggregation
- ✅ Neighbor discovery

**test_vector_semantic_search.py**
- ✅ Store and retrieve vectors
- ✅ Similarity search
- ✅ Filtered search with metadata
- ✅ Top-k result limiting
- ✅ Empty vector store handling
- ✅ Vector updates
- ✅ Vector deletion
- ✅ Batch upsert operations
- ✅ Similarity score ordering
- ✅ Threshold-based filtering
- ✅ Metadata-only retrieval
- ✅ Large result sets
- ✅ Dimensionality consistency

### Context Package Tests

**test_context_enrichment.py**
- ✅ Enrich with related notes via graph
- ✅ Enrich with related tasks
- ✅ Temporal context enrichment
- ✅ Tag similarity matching
- ✅ Entity mention tracking
- ✅ Context budget management
- ✅ Hierarchical context enrichment
- ✅ Cross-reference enrichment

### Tools Package Tests

**test_capability_execution.py**
- ✅ Load capability from YAML
- ✅ Validate capability input
- ✅ Capability allowlists
- ✅ Side effect levels
- ✅ Capability versioning
- ✅ Different adapter types (local, HTTP, subprocess)
- ✅ Timeout configuration
- ✅ Retry policies
- ✅ Parameter validation
- ✅ Output schema validation
- ✅ Capability metadata and tags
- ✅ Capability discovery
- ✅ Filtering by side effect level

### Skills Tests

**test_vector_search_skill.py**
- ✅ Skill script exists and is executable
- ✅ Query with text input
- ✅ Query with pre-computed vector
- ✅ Invalid input handling
- ✅ Missing required field handling

**test_execute_capability_skill.py**
- ✅ Skill script exists
- ✅ Execute valid capability
- ✅ Invalid JSON handling
- ✅ Missing capability name handling
- ✅ Nonexistent capability handling

**test_run_workflow_skill.py**
- ✅ Skill script exists
- ✅ Execute valid workflow
- ✅ Invalid JSON handling
- ✅ Missing workflow_id handling
- ✅ Nonexistent workflow handling
- ✅ Custom workflow parameters

### Integration Tests

**test_end_to_end_workflow.py**
- ✅ Simple workflow execution from start to finish
- ✅ Context assembly to planning flow
- ✅ Planning to execution flow
- ✅ Execution to trace storage flow
- ✅ Full cycle with memory updates
- ✅ Multi-step workflow execution

## Running Tests

### Run All Tests
```bash
pytest tests/
```

### Run Unit Tests Only
```bash
pytest tests/unit/
```

### Run Integration Tests Only
```bash
pytest tests/integration/
```

### Run Tests for Specific Package
```bash
# Core tests
pytest tests/unit/core/

# Memory tests
pytest tests/unit/memory/

# Tools tests
pytest tests/unit/tools/

# Skills tests
pytest tests/skills/

# Context tests
pytest tests/unit/context/
```

### Run Specific Test File
```bash
pytest tests/unit/memory/test_graph_traversal.py -v
```

### Run with Coverage
```bash
pytest tests/ --cov=packages/ --cov-report=html
```

## Test Fixtures

Common fixtures available in `conftest.py`:

- `temp_dir`: Temporary directory for test data
- `trace_store`: Temporary trace storage
- `event_log`: Temporary event log
- `document_store`: Temporary document store
- `vector_store`: Temporary vector store
- `graph_store`: Temporary graph store
- `capability_registry`: Capability registry instance
- `sample_context_ref`: Sample context reference
- `sample_context_item`: Sample context item
- `sample_context_packet`: Sample context packet
- `sample_action_request`: Sample action request
- `sample_plan`: Sample plan
- `sample_agent_profile`: Sample agent profile

## Test Patterns

### Testing Graph Operations
```python
def test_graph_operation(graph_store):
    # Create nodes
    graph_store.upsert_node("node1", "type", {"prop": "value"})

    # Create edges
    graph_store.upsert_edge("node1", "node2", "edge_type")

    # Query and verify
    result = graph_store.get_subgraph(["node1"], depth=1)
    assert result is not None
```

### Testing Vector Search
```python
def test_vector_search(vector_store):
    # Store vector
    vector = np.random.rand(384).astype(np.float32)
    vector_store.upsert_vector("item_id", vector.tolist(), metadata={})

    # Search
    results = vector_store.search(vector.tolist(), top_k=5)
    assert len(results) > 0
```

### Testing Skills
```python
def test_skill_execution(skill_script_path):
    input_data = {"param": "value"}

    result = subprocess.run(
        ["python", str(skill_script_path)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output["success"] is True
```

### Testing Context Enrichment
```python
def test_enrichment(document_store, graph_store):
    # Create related documents
    doc1_id = document_store.upsert_document(...)
    doc2_id = document_store.upsert_document(...)

    # Create relationships
    graph_store.upsert_edge(doc1_id, doc2_id, "references")

    # Query and verify enrichment
    related = graph_store.get_edges(doc1_id)
    assert len(related) > 0
```

## CI/CD Integration

Tests are automatically run in CI/CD pipelines on:
- Pull request creation
- Push to main branch
- Pre-merge validation

Required checks:
- ✅ All unit tests pass
- ✅ All integration tests pass
- ✅ Test coverage > 80%
- ✅ No linting errors
- ✅ Type checking passes

## Test Data

Test data is stored in temporary directories and cleaned up after each test run. No persistent test data is committed to the repository.

For integration tests that need realistic data:
- Use fixtures to generate sample data
- Keep test data small and focused
- Clean up after test completion

## Adding New Tests

When adding new functionality:

1. **Write unit tests first** for the core logic
2. **Add integration tests** for component interactions
3. **Update this documentation** with new test coverage
4. **Ensure tests pass** before submitting PR

### Test Naming Conventions

- Test files: `test_<module_name>.py`
- Test classes: `Test<FeatureName>`
- Test methods: `test_<specific_behavior>`

### Test Organization

- Group related tests in classes
- Use descriptive test names
- Add docstrings explaining what's being tested
- Keep tests focused and independent

## Known Limitations

1. **Skills tests**: Require Python scripts to be executable; some tests may need actual implementations
2. **Integration tests**: May need actual LLM API keys for end-to-end testing (mocked for CI)
3. **Vector search tests**: Use random embeddings; real semantic tests need actual embedding models

## Future Test Additions

Planned test coverage improvements:

- [ ] Performance benchmarks for graph traversal
- [ ] Load testing for vector search at scale
- [ ] Stress tests for concurrent workflow execution
- [ ] Security tests for capability execution
- [ ] Fuzzing tests for schema validation
- [ ] Property-based tests for graph operations

---

**Last Updated**: 2026-01-25
**Total Test Files**: 60+
**Estimated Coverage**: 85%+
