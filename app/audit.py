"""Immutable append-only JSONL audit log — the 'cold data' layer."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Final

_LOG_PATH: Final[Path] = Path("events.log")


def _write_record(record: dict[str, Any]) -> None:
    """Synchronous write helper — must only be called via asyncio.to_thread."""
    with _LOG_PATH.open("a") as fh:
        fh.write(json.dumps(record) + "\n")


async def log_event(event_type: str, payload: dict[str, Any]) -> None:
    """Append a single JSON-lines event to the audit log.

    File I/O is offloaded to a thread pool so the asyncio event loop is
    never blocked.  In a production system this would publish to
    Kafka/Kinesis instead.  The nanosecond timestamp enables idempotent
    replay.

    Args:
        event_type: Category label for the event (e.g. "MOVE", "MOVE_WS").
        payload: Arbitrary key-value data to include in the log record.
    """
    record = {
        "ts": time.time_ns(),  # nanosecond precision — unique ordering key
        "event": event_type,
        **payload,
    }
    await asyncio.to_thread(_write_record, record)
