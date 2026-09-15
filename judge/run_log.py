"""Structured, append-only run logging.

One ``run_log.jsonl`` per run: every event a post-mortem might need, written
the moment it happens so a run that dies mid-way still explains itself. This
exists because a live run once hung for 85 minutes and left behind an empty
directory — nothing recorded what it had been doing.

Events are one JSON object per line:

    {"ts": "...", "elapsed_s": 12.3, "event": "pulse.response",
     "question_id": "CRM-T2-01-05", "latency_s": 140.1, ...}

Never write a secret here. ``redact()`` is the only sanctioned way to put
settings into the log.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SECRET_HINTS = ("token", "key", "secret", "password", "authorization", "bearer")


def redact(value: Any, *, _key: str = "") -> Any:
    """Deep-copy ``value`` with anything that smells like a credential masked."""
    if isinstance(value, dict):
        return {k: redact(v, _key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, _key=_key) for v in value]
    if isinstance(value, str) and any(h in _key.lower() for h in _SECRET_HINTS):
        if not value:
            return ""
        return f"<redacted len={len(value)}>"
    return value


class RunLog:
    """Append-only JSONL event log. Safe to call from many threads."""

    def __init__(self, path: Path | None) -> None:
        self._path = Path(path) if path else None
        self._lock = threading.Lock()
        self._t0 = datetime.now(timezone.utc)
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path | None:
        return self._path

    def event(self, event: str, **fields: Any) -> None:
        if self._path is None:
            return
        now = datetime.now(timezone.utc)
        record = {
            "ts": now.isoformat(),
            "elapsed_s": round((now - self._t0).total_seconds(), 3),
            "event": event,
        }
        record.update(redact(fields))
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())  # survive a kill -9


class NullRunLog(RunLog):
    """No-op log, so callers never have to branch on ``if log is not None``."""

    def __init__(self) -> None:
        super().__init__(None)
