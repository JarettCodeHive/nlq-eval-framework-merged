"""A deterministic `JudgeClient` for tests. Not a judge, and not shippable.

The suite used to lean on `HeuristicJudge` for this. That was a production
module — selectable as `--judge heuristic`, warned about in three places, and
guarded against in two more — kept alive largely because tests needed something
cheap to subclass. Once Floodgate became the only real backend it was removed,
and the test-only need moved here, where it cannot be selected by a run.

Fixed scores, no scoring logic: a test that wants a particular verdict says so
via `scores=`, rather than reverse-engineering what string overlap would produce.
MUST stay deterministic — a fixture that varies between runs tests nothing.
"""

from __future__ import annotations

from judge.client import JudgeClient
from judge.contracts import DIMENSIONS, JudgeRequest, JudgeVerdict

MODEL_VERSION = "stub-judge-v1"


class StubJudge(JudgeClient):
    """Returns the same verdict for every request."""

    model_version = MODEL_VERSION

    def __init__(self, scores: dict[str, int] | None = None) -> None:
        self._scores = {dimension: 4 for dimension in DIMENSIONS}
        if scores:
            self._scores.update(scores)
        # Test assertions on call counts read this instead of patching.
        self.calls = 0

    async def judge(self, req: JudgeRequest) -> JudgeVerdict:
        self.calls += 1
        return JudgeVerdict(
            dimension_rationales={
                dimension: f"stub rationale for {dimension}"
                for dimension in DIMENSIONS
            },
            prompt_version="stub",
            model_version=self.model_version,
            **self._scores,
        )
