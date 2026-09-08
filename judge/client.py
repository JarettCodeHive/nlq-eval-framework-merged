"""JudgeClient interface every judge implementation satisfies."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Sequence

from judge.contracts import JudgeRequest, JudgeVerdict


class JudgeClient(ABC):
    """Async because judge runs are batched with rate-limit awareness."""

    @abstractmethod
    async def judge(self, req: JudgeRequest) -> JudgeVerdict: ...

    async def judge_many(
        self, requests: Sequence[JudgeRequest], *, concurrency: int = 4
    ) -> list[JudgeVerdict | Exception]:
        """Bounded-concurrency fan-out preserving input order.

        Exceptions are RETURNED, not raised. One bad response must not discard a
        whole run's worth of good scores — the runner records failures per row
        as judge_error and excludes them from the averages.
        """
        sem = asyncio.Semaphore(concurrency)

        async def _one(r: JudgeRequest) -> JudgeVerdict | Exception:
            async with sem:
                try:
                    return await self.judge(r)
                except (
                    Exception
                ) as exc:  # noqa: BLE001 — surfaced per-row by the runner
                    return exc

        return list(await asyncio.gather(*(_one(r) for r in requests)))

    async def aclose(self) -> None:
        """Release any transport resources. Safe to call on every implementation."""
        return None

    def cache_stats(self) -> dict[str, int | bool]:
        return {"enabled": False, "hits": 0, "misses": 0}
