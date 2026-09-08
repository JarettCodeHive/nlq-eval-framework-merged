"""Content-keyed judge cache — cost control that makes judging every response affordable.

On a regression re-run where the Platform's answer is unchanged, every judge
call is a cache hit and costs nothing. The baseline run pays once.

The key includes everything that can change a verdict. Leaving any of these out
serves a stale score under a new configuration, which is worse than no cache:
a confident wrong number with no signal that anything changed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from judge.contracts import JudgeRequest, JudgeVerdict


def cache_key(
    req: JudgeRequest, *, prompt_version: str, model_version: str, mode: str
) -> str:
    """Stable key over every input that can change the verdict.

    `mode` matters: a `combined` verdict must never be served to a
    `per_dimension` request, or the two modes become indistinguishable and the
    comparison between them is meaningless.
    """
    payload = {
        "prompt_version": prompt_version,
        "model_version": model_version,
        "mode": mode,
        "question": req.question,
        "expected_answer": req.expected_answer,
        "judge_reference": req.judge_reference,
        "platform_answer": req.platform_answer,
        "generated_sql": req.generated_sql or "",
        "domain": req.domain,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class JudgeCache:
    """One JSON file per verdict under `root/`.

    Deliberately not a database: inspectable with a text editor, diffable,
    trivially clearable by deleting files. At the volumes in play (thousands of
    entries) a directory of small JSON files is entirely adequate.
    """

    def __init__(self, root: Path, *, enabled: bool = True) -> None:
        self._root = root
        self._enabled = enabled
        self.hits = 0
        self.misses = 0
        if enabled:
            root.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _path(self, key: str) -> Path:
        # Shard by the first two hex chars to avoid one directory with 10k files.
        return self._root / key[:2] / f"{key}.json"

    def get(self, key: str) -> JudgeVerdict | None:
        if not self._enabled:
            return None
        path = self._path(key)
        if not path.is_file():
            self.misses += 1
            return None
        try:
            verdict = JudgeVerdict.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — a corrupt entry is a miss, not a crash
            self.misses += 1
            return None
        self.hits += 1
        return verdict.model_copy(update={"cached": True})

    def put(self, key: str, verdict: JudgeVerdict) -> None:
        if not self._enabled:
            return
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Store with cached=False; the flag describes how a verdict was
        # *served*, not how it was produced.
        payload = verdict.model_copy(update={"cached": False})
        tmp = path.with_suffix(".tmp")
        tmp.write_text(payload.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)  # atomic — a killed run never leaves a half-written entry

    def stats(self) -> dict[str, int | bool]:
        return {"enabled": self._enabled, "hits": self.hits, "misses": self.misses}
