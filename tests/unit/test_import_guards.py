"""Tests for import guard utilities and optional dependency handling."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from agent_kernel._import_utils import require_extra


class TestRequireExtra:
    """Tests for the require_extra import guard helper."""

    def test_succeeds_when_package_installed(self) -> None:
        """require_extra should succeed silently for installed packages."""
        # structlog is a core dependency, always available
        require_extra("structlog", "dev")

    def test_raises_importerror_when_package_missing(self) -> None:
        """require_extra should raise ImportError for missing packages."""
        with pytest.raises(ImportError, match="not_a_real_package"):
            require_extra("not_a_real_package", "test")

    def test_error_message_contains_pip_install_command(self) -> None:
        """Error message should include pip install agentkernel[extra]."""
        with pytest.raises(ImportError, match=r"pip install agentkernel\[vectors\]"):
            require_extra("not_a_real_package", "vectors")

    def test_error_message_contains_feature_description(self) -> None:
        """Error message should include the feature description when provided."""
        with pytest.raises(ImportError, match="for vector search"):
            require_extra("not_a_real_package", "vectors", "vector search")

    def test_error_message_without_feature_description(self) -> None:
        """Error message should work without feature description."""
        with pytest.raises(ImportError, match="'not_a_real_package' is required but"):
            require_extra("not_a_real_package", "test")

    def test_does_not_propagate_original_traceback(self) -> None:
        """Error should use 'from None' to hide internal traceback."""
        with pytest.raises(ImportError) as exc_info:
            require_extra("not_a_real_package", "test")
        assert exc_info.value.__cause__ is None


class TestApiServerImportGuard:
    """Tests for the API server's import guard."""

    def test_api_server_import_guard_triggers_without_fastapi(self) -> None:
        """Importing api.server without fastapi should produce helpful error."""
        # Remove fastapi from sys.modules temporarily to simulate missing
        saved_modules = {}
        for mod_name in list(sys.modules):
            if mod_name.startswith("fastapi") or mod_name == "fastapi":
                saved_modules[mod_name] = sys.modules.pop(mod_name)

        # Also remove the cached api.server module
        for mod_name in list(sys.modules):
            if "agent_kernel.api.server" in mod_name:
                saved_modules[mod_name] = sys.modules.pop(mod_name)

        try:
            # Patch __import__ to make fastapi imports fail
            original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

            def mock_import(name, *args, **kwargs):
                if name == "fastapi" or name.startswith("fastapi."):
                    raise ImportError(f"No module named '{name}'")
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                with pytest.raises(ImportError, match=r"pip install agentkernel\[api\]"):
                    import importlib
                    importlib.import_module("agent_kernel.api.server")
        finally:
            # Restore saved modules
            sys.modules.update(saved_modules)


class TestCoreImportClean:
    """Tests that core import works without optional dependencies."""

    def test_agent_kernel_importable(self) -> None:
        """'import agent_kernel' should work with only core deps."""
        import agent_kernel

        assert hasattr(agent_kernel, "__version__")
        assert agent_kernel.__version__ != ""

    def test_version_is_string(self) -> None:
        """Version should be a non-empty string."""
        import agent_kernel

        assert isinstance(agent_kernel.__version__, str)
        assert len(agent_kernel.__version__) > 0

    def test_import_utils_importable(self) -> None:
        """_import_utils module should be importable."""
        from agent_kernel._import_utils import require_extra

        assert callable(require_extra)
