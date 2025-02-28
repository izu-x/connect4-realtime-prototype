"""Tests for the JSONL audit log module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.audit import log_event


@pytest.mark.anyio
async def test_log_event_writes_jsonl(tmp_path: Path) -> None:
    """log_event should append a valid JSONL record to the log file."""
    log_file = tmp_path / "events.log"

    with patch("app.audit._LOG_PATH", log_file):
        await log_event("MOVE", {"game_id": "g1", "player": 1, "column": 3})

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event"] == "MOVE"
    assert record["game_id"] == "g1"
    assert record["player"] == 1
    assert "ts" in record


@pytest.mark.anyio
async def test_log_event_appends_multiple(tmp_path: Path) -> None:
    """Multiple calls should produce multiple JSONL lines."""
    log_file = tmp_path / "events.log"

    with patch("app.audit._LOG_PATH", log_file):
        await log_event("MOVE", {"game_id": "g1", "player": 1, "column": 0})
        await log_event("MOVE_WS", {"game_id": "g1", "player": 2, "column": 1})
        await log_event("MOVE", {"game_id": "g2", "player": 1, "column": 3})

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 3
    for line in lines:
        record = json.loads(line)
        assert "ts" in record
        assert "event" in record


@pytest.mark.anyio
async def test_log_event_timestamp_is_nanosecond(tmp_path: Path) -> None:
    """Timestamp should be a large nanosecond integer."""
    log_file = tmp_path / "events.log"

    with patch("app.audit._LOG_PATH", log_file):
        await log_event("TEST", {"key": "value"})

    record = json.loads(log_file.read_text().strip())
    assert isinstance(record["ts"], int)
    # Nanosecond timestamps are > 10^18
    assert record["ts"] > 10**18


@pytest.mark.anyio
async def test_log_event_creates_file_if_missing(tmp_path: Path) -> None:
    """log_event should create the log file if it doesn't exist."""
    log_file = tmp_path / "new_events.log"
    assert not log_file.exists()

    with patch("app.audit._LOG_PATH", log_file):
        await log_event("INIT", {"status": "started"})

    assert log_file.exists()
    record = json.loads(log_file.read_text().strip())
    assert record["event"] == "INIT"


@pytest.mark.anyio
async def test_log_event_payload_merges_into_record(tmp_path: Path) -> None:
    """All payload keys should appear at the top level of the record."""
    log_file = tmp_path / "events.log"

    with patch("app.audit._LOG_PATH", log_file):
        await log_event("CUSTOM", {"alpha": 1, "beta": "two", "gamma": [3, 4]})

    record = json.loads(log_file.read_text().strip())
    assert record["alpha"] == 1
    assert record["beta"] == "two"
    assert record["gamma"] == [3, 4]
