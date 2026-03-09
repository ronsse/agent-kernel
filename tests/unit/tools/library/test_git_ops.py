"""Tests for git operations tool library."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_kernel.tools.library.git_ops import (
    _validate_ref,
    _validate_repo_path,
    git_branches,
    git_commit,
    git_create_branch,
    git_diff,
    git_log,
    git_push,
    git_status,
)


# ---------------------------------------------------------------------------
# _validate_repo_path
# ---------------------------------------------------------------------------


class TestValidateRepoPath:
    """Tests for _validate_repo_path."""

    def test_nonexistent_path(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="does not exist"):
            _validate_repo_path(str(tmp_path / "nope"))

    def test_not_git_repo(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Not a git repository"):
            _validate_repo_path(str(tmp_path))

    def test_valid_git_repo(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        result = _validate_repo_path(str(tmp_path))
        assert result == tmp_path.resolve()

    def test_allowed_roots_blocks(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        with patch.dict(
            "os.environ",
            {"CODE_TOOLS_ALLOWED_REPO_ROOTS": "/some/other/root"},
        ):
            with pytest.raises(ValueError, match="not within allowed roots"):
                _validate_repo_path(str(tmp_path))

    def test_allowed_roots_permits(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        with patch.dict(
            "os.environ",
            {"CODE_TOOLS_ALLOWED_REPO_ROOTS": str(tmp_path.parent)},
        ):
            result = _validate_repo_path(str(tmp_path))
            assert result == tmp_path.resolve()

    def test_allowed_roots_empty_permits_all(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        with patch.dict("os.environ", {"CODE_TOOLS_ALLOWED_REPO_ROOTS": ""}):
            result = _validate_repo_path(str(tmp_path))
            assert result == tmp_path.resolve()


# ---------------------------------------------------------------------------
# _validate_ref
# ---------------------------------------------------------------------------


class TestValidateRef:
    """Tests for _validate_ref."""

    @pytest.mark.parametrize(
        "ref",
        ["main", "feature/foo", "v1.0.0", "HEAD~3", "HEAD^", "origin/main"],
    )
    def test_valid_refs(self, ref: str) -> None:
        assert _validate_ref(ref) == ref

    @pytest.mark.parametrize("ref", ["$(cmd)", "ref; rm -rf", "ref && echo"])
    def test_invalid_refs(self, ref: str) -> None:
        with pytest.raises(ValueError, match="Invalid git ref"):
            _validate_ref(ref)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_repo(tmp_path: Path) -> Path:
    """Create a minimal fake git repo directory."""
    (tmp_path / ".git").mkdir()
    return tmp_path


def _make_completed_process(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["git"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


# ---------------------------------------------------------------------------
# git_status
# ---------------------------------------------------------------------------


class TestGitStatus:
    """Tests for git_status."""

    @patch("agent_kernel.tools.library.git_ops._run_git")
    def test_clean_repo(
        self, mock_run: MagicMock, fake_repo: Path
    ) -> None:
        mock_run.side_effect = [
            _make_completed_process(stdout=""),  # status
            _make_completed_process(stdout="main\n"),  # branch
        ]
        result = git_status(repo_path=str(fake_repo))
        assert result["clean"] is True
        assert result["file_count"] == 0
        assert result["branch"] == "main"

    @patch("agent_kernel.tools.library.git_ops._run_git")
    def test_dirty_repo(
        self, mock_run: MagicMock, fake_repo: Path
    ) -> None:
        mock_run.side_effect = [
            _make_completed_process(stdout=" M src/foo.py\n?? new.txt\n"),
            _make_completed_process(stdout="feature/bar\n"),
        ]
        result = git_status(repo_path=str(fake_repo))
        assert result["clean"] is False
        assert result["file_count"] == 2
        assert result["branch"] == "feature/bar"
        assert result["files"][0]["path"] == "src/foo.py"

    @patch("agent_kernel.tools.library.git_ops._run_git")
    def test_error_returns_error_dict(
        self, mock_run: MagicMock, fake_repo: Path
    ) -> None:
        mock_run.return_value = _make_completed_process(
            stderr="fatal: something", returncode=128
        )
        result = git_status(repo_path=str(fake_repo))
        assert "error" in result
        assert result["returncode"] == 128


# ---------------------------------------------------------------------------
# git_diff
# ---------------------------------------------------------------------------


class TestGitDiff:
    """Tests for git_diff."""

    @patch("agent_kernel.tools.library.git_ops._run_git")
    def test_basic_diff(
        self, mock_run: MagicMock, fake_repo: Path
    ) -> None:
        diff_output = "diff --git a/foo.py b/foo.py\n+new line\n"
        mock_run.side_effect = [
            _make_completed_process(stdout=diff_output),  # diff
            _make_completed_process(stdout="1 file changed"),  # stat
        ]
        result = git_diff(repo_path=str(fake_repo))
        assert result["diff"] == diff_output
        assert result["truncated"] is False

    @patch("agent_kernel.tools.library.git_ops._run_git")
    def test_truncation(
        self, mock_run: MagicMock, fake_repo: Path
    ) -> None:
        big_diff = "x" * 20_000
        mock_run.side_effect = [
            _make_completed_process(stdout=big_diff),
            _make_completed_process(stdout=""),
        ]
        result = git_diff(repo_path=str(fake_repo))
        assert result["truncated"] is True
        assert len(result["diff"]) == 10_000


# ---------------------------------------------------------------------------
# git_log
# ---------------------------------------------------------------------------


class TestGitLog:
    """Tests for git_log."""

    @patch("agent_kernel.tools.library.git_ops._run_git")
    def test_oneline_log(
        self, mock_run: MagicMock, fake_repo: Path
    ) -> None:
        log_output = "abc1234 Initial commit\ndef5678 Second commit\n"
        mock_run.return_value = _make_completed_process(stdout=log_output)
        result = git_log(repo_path=str(fake_repo))
        assert result["count"] == 2
        assert result["commits"][0]["hash"] == "abc1234"
        assert result["commits"][0]["message"] == "Initial commit"

    @patch("agent_kernel.tools.library.git_ops._run_git")
    def test_max_count_capped(
        self, mock_run: MagicMock, fake_repo: Path
    ) -> None:
        mock_run.return_value = _make_completed_process(stdout="")
        git_log(repo_path=str(fake_repo), max_count=500)
        call_args = mock_run.call_args[0][1]
        assert "--max-count=100" in call_args


# ---------------------------------------------------------------------------
# git_branches
# ---------------------------------------------------------------------------


class TestGitBranches:
    """Tests for git_branches."""

    @patch("agent_kernel.tools.library.git_ops._run_git")
    def test_list_branches(
        self, mock_run: MagicMock, fake_repo: Path
    ) -> None:
        mock_run.side_effect = [
            _make_completed_process(
                stdout="main|abc1234|origin/main\nfeature/x|def5678|\n"
            ),
            _make_completed_process(stdout="main\n"),
        ]
        result = git_branches(repo_path=str(fake_repo))
        assert result["count"] == 2
        assert result["current"] == "main"
        assert result["branches"][0]["current"] is True
        assert result["branches"][1]["upstream"] is None


# ---------------------------------------------------------------------------
# git_commit
# ---------------------------------------------------------------------------


class TestGitCommit:
    """Tests for git_commit."""

    @patch("agent_kernel.tools.library.git_ops._run_git")
    def test_commit_with_files(
        self, mock_run: MagicMock, fake_repo: Path
    ) -> None:
        mock_run.side_effect = [
            _make_completed_process(),  # git add
            _make_completed_process(stdout="[main abc1234] fix bug\n"),
            _make_completed_process(stdout="abc1234full\n"),  # rev-parse
        ]
        result = git_commit(
            repo_path=str(fake_repo),
            message="fix bug",
            files=["src/foo.py"],
        )
        assert result["success"] is True
        assert result["commit_hash"] == "abc1234full"

    def test_empty_message_raises(self, fake_repo: Path) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            git_commit(repo_path=str(fake_repo), message="  ", files=["x"])

    def test_no_files_or_all_tracked_raises(self, fake_repo: Path) -> None:
        with pytest.raises(ValueError, match="Either"):
            git_commit(repo_path=str(fake_repo), message="msg")


# ---------------------------------------------------------------------------
# git_create_branch
# ---------------------------------------------------------------------------


class TestGitCreateBranch:
    """Tests for git_create_branch."""

    @patch("agent_kernel.tools.library.git_ops._run_git")
    def test_create_and_checkout(
        self, mock_run: MagicMock, fake_repo: Path
    ) -> None:
        mock_run.return_value = _make_completed_process()
        result = git_create_branch(
            repo_path=str(fake_repo), branch_name="feature/new"
        )
        assert result["success"] is True
        assert result["checked_out"] is True

    def test_invalid_branch_name(self, fake_repo: Path) -> None:
        with pytest.raises(ValueError, match="Invalid git ref"):
            git_create_branch(
                repo_path=str(fake_repo), branch_name="$(evil)"
            )


# ---------------------------------------------------------------------------
# git_push
# ---------------------------------------------------------------------------


class TestGitPush:
    """Tests for git_push."""

    @patch("agent_kernel.tools.library.git_ops._run_git")
    def test_push_success(
        self, mock_run: MagicMock, fake_repo: Path
    ) -> None:
        mock_run.return_value = _make_completed_process(
            stderr="Everything up-to-date"
        )
        result = git_push(repo_path=str(fake_repo))
        assert result["success"] is True
        assert result["remote"] == "origin"

    @patch("agent_kernel.tools.library.git_ops._run_git")
    def test_push_failure(
        self, mock_run: MagicMock, fake_repo: Path
    ) -> None:
        mock_run.return_value = _make_completed_process(
            stderr="rejected", returncode=1
        )
        result = git_push(repo_path=str(fake_repo))
        assert "error" in result
