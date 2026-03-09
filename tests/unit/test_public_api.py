"""Tests that the public API surface is well-defined.

Verifies:
- All non-empty __init__.py files define __all__
- All symbols listed in __all__ are actually importable
- The root __init__.py exports key public symbols
- py.typed marker exists for PEP 561
"""

from __future__ import annotations

import importlib
import pathlib


PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src" / "agent_kernel"


def test_py_typed_exists() -> None:
    """PEP 561 py.typed marker must exist in the package."""
    marker = PACKAGE_ROOT / "py.typed"
    assert marker.exists(), f"Missing py.typed at {marker}"


def test_all_init_files_define_all() -> None:
    """Every non-empty __init__.py must define __all__."""
    missing: list[str] = []
    for init in sorted(PACKAGE_ROOT.rglob("__init__.py")):
        text = init.read_text()
        if text.strip() and "__all__" not in text:
            rel = init.relative_to(PACKAGE_ROOT.parent)
            missing.append(str(rel))

    assert not missing, (
        f"The following __init__.py files are missing __all__: {missing}"
    )


def test_all_symbols_importable() -> None:
    """Every symbol in __all__ must be importable from its module."""
    failures: list[str] = []
    for init in sorted(PACKAGE_ROOT.rglob("__init__.py")):
        text = init.read_text()
        if not text.strip() or "__all__" not in text:
            continue

        # Derive module name from path
        rel = init.relative_to(PACKAGE_ROOT.parent)
        parts = list(rel.parts[:-1])  # drop __init__.py
        module_name = ".".join(parts)

        try:
            mod = importlib.import_module(module_name)
        except Exception:
            # Some modules may require optional deps (mcp, fastapi, etc.)
            continue

        all_list = getattr(mod, "__all__", None)
        if all_list is None:
            continue

        for symbol in all_list:
            if not hasattr(mod, symbol):
                failures.append(f"{module_name}.{symbol}")

    assert not failures, (
        f"Symbols in __all__ but not importable: {failures}"
    )


def test_root_exports_key_symbols() -> None:
    """The root agent_kernel module must export key public symbols."""
    import agent_kernel

    expected = [
        "Plan",
        "ContextPacket",
        "DecisionTrace",
        "ToolCallRecord",
        "AgentProfile",
        "ContextAssembler",
        "CustomEngine",
        "DeterministicExecutor",
        "ToolBroker",
        "WorkflowRunner",
        "Settings",
    ]
    missing = [s for s in expected if not hasattr(agent_kernel, s)]
    assert not missing, f"Root module missing key symbols: {missing}"


def test_root_all_matches_attributes() -> None:
    """Every item in root __all__ must be an attribute of the module."""
    import agent_kernel

    all_list = getattr(agent_kernel, "__all__", [])
    assert len(all_list) > 0, "Root __all__ is empty"

    missing = [s for s in all_list if not hasattr(agent_kernel, s)]
    assert not missing, f"Root __all__ lists non-existent: {missing}"
