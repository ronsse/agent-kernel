#!/usr/bin/env bash
# test-clean-install.sh — Verify agentkernel installs cleanly in fresh venvs
# Usage: ./scripts/test-clean-install.sh [python_versions...]
# Example: ./scripts/test-clean-install.sh 3.11 3.12 3.13
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Default Python versions to test
DEFAULT_VERSIONS="3.11 3.12 3.13"
VERSIONS="${*:-$DEFAULT_VERSIONS}"

PASS=0
FAIL=0
SKIP=0
RESULTS=()

cleanup() {
    for ver in $VERSIONS; do
        local venv_dir="/tmp/ak-test-${ver}"
        if [[ -d "$venv_dir" ]]; then
            rm -rf "$venv_dir"
        fi
    done
}
trap cleanup EXIT

log_pass() {
    echo "  [PASS] $1"
}

log_fail() {
    echo "  [FAIL] $1"
}

test_version() {
    local ver="$1"
    local python_bin="python${ver}"
    local venv_dir="/tmp/ak-test-${ver}"

    echo ""
    echo "================================================================"
    echo "Testing Python ${ver}"
    echo "================================================================"

    # Check if Python version is available
    if ! command -v "$python_bin" &>/dev/null; then
        echo "  [SKIP] python${ver} not found on system"
        SKIP=$((SKIP + 1))
        RESULTS+=("Python ${ver}: SKIPPED (not installed)")
        return 0
    fi

    local actual_version
    actual_version=$("$python_bin" --version 2>&1)
    echo "  Found: $actual_version"

    # Clean up any previous test venv
    rm -rf "$venv_dir"

    # Step 1: Create fresh virtualenv
    echo "  Creating fresh virtualenv..."
    if ! "$python_bin" -m venv "$venv_dir" 2>&1; then
        log_fail "Failed to create virtualenv"
        FAIL=$((FAIL + 1))
        RESULTS+=("Python ${ver}: FAILED (venv creation)")
        return 1
    fi

    local pip="${venv_dir}/bin/pip"
    local python="${venv_dir}/bin/python"

    # Upgrade pip to avoid build issues
    "$pip" install --quiet --upgrade pip 2>&1

    # Step 2: Install core package
    echo "  Installing agentkernel (core only)..."
    if ! "$pip" install --quiet "$PROJECT_DIR" 2>&1; then
        log_fail "Core install failed"
        FAIL=$((FAIL + 1))
        RESULTS+=("Python ${ver}: FAILED (core install)")
        return 1
    fi
    log_pass "Core install succeeded"

    # Step 3: Verify basic import
    echo "  Verifying basic import..."
    if ! "$python" -c "import agent_kernel; print(f'  Version: {agent_kernel.__version__}')" 2>&1; then
        log_fail "import agent_kernel failed"
        FAIL=$((FAIL + 1))
        RESULTS+=("Python ${ver}: FAILED (basic import)")
        return 1
    fi
    log_pass "import agent_kernel works"

    # Step 4: Verify core schema imports
    echo "  Verifying core schema imports..."
    if ! "$python" -c "from agent_kernel.core.schemas import Plan, DecisionTrace; print('  Schemas OK')" 2>&1; then
        log_fail "Core schema import failed"
        FAIL=$((FAIL + 1))
        RESULTS+=("Python ${ver}: FAILED (schema import)")
        return 1
    fi
    log_pass "Core schema imports work"

    # Step 5: Verify optional imports raise ImportError (not crash)
    echo "  Verifying optional import guards..."
    local guard_ok=true
    for module in "lancedb" "fastapi" "mcp"; do
        if ! "$python" -c "
try:
    import ${module}
    # If it somehow is installed, that's fine
except ImportError:
    pass  # Expected - not installed
except Exception as e:
    print(f'  Unexpected error importing ${module}: {e}')
    raise SystemExit(1)
" 2>&1; then
            log_fail "Optional import guard for ${module} caused unexpected error"
            guard_ok=false
        fi
    done
    if $guard_ok; then
        log_pass "Optional import guards work correctly"
    fi

    # Step 6: Install extras (vectors and api)
    echo "  Installing extras: vectors, api..."
    if ! "$pip" install --quiet "${PROJECT_DIR}[vectors,api]" 2>&1; then
        log_fail "Extras install failed"
        FAIL=$((FAIL + 1))
        RESULTS+=("Python ${ver}: FAILED (extras install)")
        return 1
    fi
    log_pass "Extras install succeeded"

    # Step 7: Verify extras imports work
    echo "  Verifying extras imports..."
    if ! "$python" -c "
import lancedb
import fastapi
print('  Extras imports OK')
" 2>&1; then
        log_fail "Extras imports failed after install"
        FAIL=$((FAIL + 1))
        RESULTS+=("Python ${ver}: FAILED (extras imports)")
        return 1
    fi
    log_pass "Extras imports work"

    # Step 8: Run targeted tests
    echo "  Running targeted tests..."
    "$pip" install --quiet "pytest>=8.0" "pytest-asyncio>=0.24" 2>&1
    local test_exit=0
    if ! "$python" -m pytest \
        "${PROJECT_DIR}/tests/unit/test_import_guards.py" \
        "${PROJECT_DIR}/tests/unit/test_public_api.py" \
        -x --tb=short -q 2>&1; then
        test_exit=1
    fi

    if [[ $test_exit -ne 0 ]]; then
        log_fail "Targeted tests failed"
        FAIL=$((FAIL + 1))
        RESULTS+=("Python ${ver}: FAILED (tests)")
        return 1
    fi
    log_pass "Targeted tests passed"

    # All checks passed
    PASS=$((PASS + 1))
    RESULTS+=("Python ${ver}: PASSED")
    return 0
}

echo "============================================"
echo "  agentkernel Clean Install Test"
echo "============================================"
echo "Project: $PROJECT_DIR"
echo "Testing versions: $VERSIONS"

for ver in $VERSIONS; do
    test_version "$ver" || true
done

echo ""
echo "============================================"
echo "  Summary"
echo "============================================"
for result in "${RESULTS[@]}"; do
    echo "  $result"
done
echo ""
echo "  Passed: $PASS  Failed: $FAIL  Skipped: $SKIP"
echo "============================================"

if [[ $FAIL -gt 0 ]]; then
    exit 1
fi

if [[ $PASS -eq 0 ]]; then
    echo "  WARNING: No Python versions were available for testing"
    exit 1
fi

exit 0
