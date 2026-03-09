"""Tests for gws calendar import subprocess integration.

Verifies that _step_import_calendar_events uses gws CLI subprocess
instead of the deleted calendar.events.list@v1 capability.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_kernel.workflows.runner import (
    CalendarDerivationState,
    CalendarEventRecord,
    CalendarSourceConfig,
    CalendarSourceFilters,
    WorkflowRunner,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_GCAL_EVENT: dict[str, Any] = {
    "id": "evt_abc123",
    "summary": "Team Standup",
    "description": "Daily sync",
    "start": {"dateTime": "2026-03-07T09:00:00-07:00"},
    "end": {"dateTime": "2026-03-07T09:30:00-07:00"},
    "status": "confirmed",
    "attendees": [
        {"email": "alice@example.com"},
        {"email": "bob@example.com"},
    ],
    "location": "Room 42",
    "hangoutLink": "https://meet.google.com/abc-def-ghi",
    "updated": "2026-03-07T08:00:00Z",
    "etag": '"etag123"',
}

SAMPLE_GCAL_RESPONSE: dict[str, Any] = {
    "items": [SAMPLE_GCAL_EVENT],
}


def _make_runner() -> WorkflowRunner:
    """Create a minimal WorkflowRunner with mocked dependencies."""
    runner = WorkflowRunner(
        context_assembler=MagicMock(),
        executor=MagicMock(),
        event_log=None,
        configs_dir="configs",
    )
    return runner


def _make_source(
    source_id: str = "work",
    calendar_id: str = "primary",
    import_window_days: int = 7,
) -> CalendarSourceConfig:
    return CalendarSourceConfig(
        source_id=source_id,
        provider="google",
        calendar_id=calendar_id,
        purpose="work",
        import_window_days=import_window_days,
        filters=CalendarSourceFilters(),
    )


# ---------------------------------------------------------------------------
# Test 1: _call_gws_calendar calls subprocess with correct args
# ---------------------------------------------------------------------------


class TestCallGwsCalendar:
    """Tests for the _call_gws_calendar helper method."""

    def test_subprocess_called_with_correct_args(self) -> None:
        """_call_gws_calendar calls subprocess with correct gws command and params JSON."""
        runner = _make_runner()
        time_min = "2026-03-07T00:00:00+00:00"
        time_max = "2026-03-14T00:00:00+00:00"

        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = json.dumps(SAMPLE_GCAL_RESPONSE)
        fake_result.stderr = ""

        with patch("subprocess.run", return_value=fake_result) as mock_run:
            result = runner._call_gws_calendar(
                calendar_id="primary",
                time_min=time_min,
                time_max=time_max,
                max_results=50,
            )

        mock_run.assert_called_once()
        call_args = mock_run.call_args
        cmd = call_args[0][0]  # positional arg (the command list)
        assert cmd[0] == "gws"
        assert cmd[1] == "calendar"
        assert cmd[2] == "events"
        assert cmd[3] == "list"
        assert "--params" in cmd
        assert "--format" in cmd
        params_idx = cmd.index("--params")
        params = json.loads(cmd[params_idx + 1])
        assert params["calendarId"] == "primary"
        assert params["timeMin"] == time_min
        assert params["timeMax"] == time_max
        assert params["singleEvents"] is True
        assert params["orderBy"] == "startTime"
        assert params["maxResults"] == 50
        assert call_args[1].get("capture_output") is True
        assert call_args[1].get("text") is True
        assert call_args[1].get("timeout") == 30

    # -------------------------------------------------------------------
    # Test 2: Successful gws output parsed correctly
    # -------------------------------------------------------------------

    def test_successful_output_parsed(self) -> None:
        """Successful gws output with items list is parsed and returned."""
        runner = _make_runner()
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = json.dumps(SAMPLE_GCAL_RESPONSE)
        fake_result.stderr = ""

        with patch("subprocess.run", return_value=fake_result):
            result = runner._call_gws_calendar(
                calendar_id="primary",
                time_min="2026-03-07T00:00:00Z",
                time_max="2026-03-14T00:00:00Z",
            )

        assert isinstance(result, dict)
        assert "items" in result
        assert len(result["items"]) == 1
        assert result["items"][0]["id"] == "evt_abc123"

    # -------------------------------------------------------------------
    # Test 3: Non-zero exit code raises RuntimeError
    # -------------------------------------------------------------------

    def test_nonzero_exit_raises_runtime_error(self) -> None:
        """gws returning non-zero exit code raises RuntimeError with stderr."""
        runner = _make_runner()
        fake_result = MagicMock()
        fake_result.returncode = 1
        fake_result.stdout = ""
        fake_result.stderr = "OAuth token expired"

        with patch("subprocess.run", return_value=fake_result):
            with pytest.raises(RuntimeError, match="OAuth token expired"):
                runner._call_gws_calendar(
                    calendar_id="primary",
                    time_min="2026-03-07T00:00:00Z",
                    time_max="2026-03-14T00:00:00Z",
                )

    # -------------------------------------------------------------------
    # Test 4: Invalid JSON raises error
    # -------------------------------------------------------------------

    def test_invalid_json_raises_error(self) -> None:
        """gws returning invalid JSON raises appropriate error."""
        runner = _make_runner()
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "not valid json {{"
        fake_result.stderr = ""

        with patch("subprocess.run", return_value=fake_result):
            with pytest.raises(json.JSONDecodeError):
                runner._call_gws_calendar(
                    calendar_id="primary",
                    time_min="2026-03-07T00:00:00Z",
                    time_max="2026-03-14T00:00:00Z",
                )


# ---------------------------------------------------------------------------
# Test 5: _step_import_calendar_events produces CalendarDerivationState
# ---------------------------------------------------------------------------


class TestStepImportCalendarEvents:
    """Tests for the full _step_import_calendar_events step."""

    @pytest.mark.asyncio
    async def test_uses_gws_subprocess_and_produces_state(self) -> None:
        """_step_import_calendar_events uses _call_gws_calendar and produces correct state."""
        runner = _make_runner()
        source = _make_source()

        # Mock _load_calendar_sources to return our test source
        runner._load_calendar_sources = MagicMock(
            return_value={source.source_id: source}
        )

        # Mock _call_gws_calendar to return sample events
        runner._call_gws_calendar = MagicMock(return_value=SAMPLE_GCAL_RESPONSE)

        agent_profile = MagicMock()
        state = await runner._step_import_calendar_events(agent_profile)

        assert isinstance(state, CalendarDerivationState)
        assert "work" in state.events_by_source
        events = state.events_by_source["work"]
        assert len(events) == 1
        assert isinstance(events[0], CalendarEventRecord)
        assert events[0].event_id == "evt_abc123"
        assert events[0].title == "Team Standup"

        # Verify _call_gws_calendar was called (not ToolBroker)
        runner._call_gws_calendar.assert_called_once()
        call_kwargs = runner._call_gws_calendar.call_args
        assert call_kwargs[1]["calendar_id"] == "primary" or call_kwargs[0][0] == "primary"

    # -------------------------------------------------------------------
    # Test 6: Empty calendar produces empty state without error
    # -------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_empty_calendar_produces_empty_state(self) -> None:
        """Empty calendar (no events) produces empty CalendarDerivationState."""
        runner = _make_runner()
        source = _make_source()

        runner._load_calendar_sources = MagicMock(
            return_value={source.source_id: source}
        )

        # Return empty items list
        runner._call_gws_calendar = MagicMock(return_value={"items": []})

        agent_profile = MagicMock()
        state = await runner._step_import_calendar_events(agent_profile)

        assert isinstance(state, CalendarDerivationState)
        assert "work" in state.events_by_source
        assert state.events_by_source["work"] == []

    @pytest.mark.asyncio
    async def test_gws_failure_produces_empty_events_for_source(self) -> None:
        """When gws subprocess fails, the source gets empty events (no crash)."""
        runner = _make_runner()
        source = _make_source()

        runner._load_calendar_sources = MagicMock(
            return_value={source.source_id: source}
        )

        # Simulate gws failure
        runner._call_gws_calendar = MagicMock(
            side_effect=RuntimeError("gws calendar events list failed: auth error")
        )

        agent_profile = MagicMock()
        state = await runner._step_import_calendar_events(agent_profile)

        assert isinstance(state, CalendarDerivationState)
        # Source should have empty events (graceful degradation)
        assert state.events_by_source.get("work", []) == []
