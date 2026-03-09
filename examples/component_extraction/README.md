# Component Extraction Examples

This directory contains working examples of how to extract and use individual components from the agent kernel.

## Examples

### 1. Standalone Vector Store (`standalone_vector_store/`)

Demonstrates extracting just the vector store for use in a custom application.

- **What it uses:** VectorStore, core schemas
- **What it doesn't use:** Workflows, agents, executors
- **Use case:** Adding semantic search to any application

### 2. Trading System (`trading_system/`)

Complete trading system using selected components:
- TimescaleDB for market data
- LanceDB for pattern matching
- ToolBroker for order execution
- Custom strategy logic (no LLM)

### 3. Analytics Platform (`analytics_platform/`)

Data analytics platform using:
- Document store for reports
- Graph store for relationships
- Vector store for similarity search

## Quick Start

Each example is self-contained and can be run independently:

```bash
# Example 1: Standalone vector store
cd standalone_vector_store
pip install -r requirements.txt
python example.py

# Example 2: Trading system
cd trading_system
pip install -r requirements.txt
python main.py

# Example 3: Analytics platform
cd analytics_platform
pip install -r requirements.txt
python run.py
```

## Package Structure

All examples follow the same pattern:

```
example_name/
├── README.md              # Specific example documentation
├── requirements.txt       # Minimal dependencies
├── src/                   # Extracted components
│   └── <component>/       # Copy of agent-kernel component
├── example.py             # Working code
└── tests/                 # Tests for extracted component
```
