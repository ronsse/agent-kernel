# =============================================================================
# Agent Kernel Makefile
# =============================================================================

.PHONY: help install install-dev lint format typecheck test test-unit test-cov clean init run

# Default target
help:
	@echo "Agent Kernel - Available Commands"
	@echo ""
	@echo "Setup:"
	@echo "  make install       Install production dependencies"
	@echo "  make install-dev   Install development dependencies"
	@echo "  make init          Initialize database and directories"
	@echo ""
	@echo "Development:"
	@echo "  make lint          Run linting (ruff)"
	@echo "  make format        Format code (ruff format)"
	@echo "  make typecheck     Run type checking (mypy)"
	@echo "  make test          Run all tests"
	@echo "  make test-unit     Run unit tests only"
	@echo "  make test-cov      Run tests with coverage"
	@echo ""
	@echo "Running:"
	@echo "  make run           Run the CLI (show help)"
	@echo "  make daily         Run daily check-in workflow"
	@echo ""
	@echo "Maintenance:"
	@echo "  make clean         Remove build artifacts"
	@echo "  make clean-data    Remove local data (traces, db)"

# =============================================================================
# Setup
# =============================================================================

install:
	uv pip install -e .

install-dev:
	uv pip install -e ".[dev]"
	pre-commit install || true

init:
	@echo "Initializing Agent Kernel..."
	@mkdir -p data/traces data/documents data/events data/chroma
	@if [ ! -f .env ]; then cp .env.example .env && echo "Created .env from template"; fi
	@echo "Running database migrations..."
	agent-kernel init || echo "CLI not yet available - skipping init command"
	@echo "Done! Edit .env with your credentials."

# =============================================================================
# Development
# =============================================================================

lint:
	ruff check src/ tests/

format:
	ruff format src/ tests/
	ruff check --fix src/ tests/

typecheck:
	mypy src/

test:
	pytest tests/ -v

test-unit:
	pytest tests/unit/ -v

test-cov:
	pytest tests/ --cov=src/agent_kernel --cov-report=html --cov-report=term
	@echo "Coverage report: htmlcov/index.html"

# =============================================================================
# Running
# =============================================================================

run:
	agent-kernel --help

daily:
	agent-kernel run-workflow daily_checkin

# =============================================================================
# Maintenance
# =============================================================================

clean:
	rm -rf build/ dist/ *.egg-info .pytest_cache .mypy_cache .ruff_cache htmlcov/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete

clean-data:
	@echo "Warning: This will delete all local data!"
	@read -p "Are you sure? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	rm -rf data/
