"""JudgeClient interface every judge implementation satisfies."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence

from judge.contracts import JudgeRequest, JudgeVerdict


class JudgeClient(ABC):
    """Async because judge runs are batched with rate-limit awareness."""

    @abstractmethod
    async def judge(self, req: JudgeRequest) -> JudgeVerdict: ...

    async def judge_many(
        self,
        requests: Sequence[JudgeRequest],
        *,
        concurrency: int = 4,
        on_done: (
            Callable[[int, JudgeRequest, JudgeVerdict | Exception], None] | None
        ) = None,
    ) -> list[JudgeVerdict | Exception]:
        """Bounded-concurrency fan-out preserving input order.

        Exceptions are RETURNED, not raised. One bad response must not discard a
        whole run's worth of good scores — the runner records failures per row
        as judge_error and excludes them from the averages.

        `on_done` fires as each verdict lands, so a caller can report progress on
        a long run. It is called under a lock in completion order, which is NOT
        input order — the returned list is still ordered.
        """
        sem = asyncio.Semaphore(concurrency)
        done_lock = asyncio.Lock()
        done = 0

        async def _one(r: JudgeRequest) -> JudgeVerdict | Exception:
            nonlocal done
            async with sem:
                try:
                    out: JudgeVerdict | Exception = await self.judge(r)
                except (
                    Exception
                ) as exc:  # noqa: BLE001 — surfaced per-row by the runner
                    out = exc
            if on_done is not None:
                async with done_lock:
                    done += 1
                    # A reporting callback must never take down a run that has
                    # already produced a valid verdict.
                    try:
                        on_done(done, r, out)
                    except Exception:  # noqa: BLE001
                        pass
            return out

        return list(await asyncio.gather(*(_one(r) for r in requests)))

    async def aclose(self) -> None:
        """Release any transport resources. Safe to call on every implementation."""
        return None

    def cache_stats(self) -> dict[str, int | bool]:
        return {"enabled": False, "hits": 0, "misses": 0}
