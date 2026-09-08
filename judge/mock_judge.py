"""Deterministic judge — no network, no model, no randomness.

For tests, CI, and end-to-end demo before the real provider is wired up.
MUST stay deterministic: gates rely on it, and a gate that returns different
scores on different runs is not a gate.

Heuristic stand-in, not a semantic evaluator. Never runs in production and its
scores mean nothing about answer quality — they mean whether the response
strings overlap enough to be plausibly matching.
"""

from __future__ import annotations

import re

from judge.client import JudgeClient
from judge.contracts import JudgeRequest, JudgeVerdict
from judge.prompts import prompt_version

_NUM = re.compile(r"-?\d+(?:\.\d+)?")
_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


class MockJudge(JudgeClient):
    """Fixed rules over string overlap. Same inputs → same verdict, always."""

    model_version = "mock-judge-v1"

    async def judge(self, req: JudgeRequest) -> JudgeVerdict:
        expected_nums = _NUM.findall(req.expected_answer)
        answer_nums = _NUM.findall(req.platform_answer)

        # Factual: do the ground-truth numbers appear in the answer?
        if expected_nums:
            hit = sum(1 for n in expected_nums if n in answer_nums)
            factual = {0: 1, 1: 3}.get(hit, 5) if hit < len(expected_nums) else 5
        else:
            overlap = _tokens(req.expected_answer) & _tokens(req.platform_answer)
            factual = 5 if len(overlap) >= 2 else 3 if overlap else 1

        # Completeness: token coverage of the ideal full response.
        ref_tokens = _tokens(req.judge_reference)
        covered = len(ref_tokens & _tokens(req.platform_answer)) / max(
            len(ref_tokens), 1
        )
        completeness = (
            5 if covered >= 0.6 else 4 if covered >= 0.4 else 3 if covered >= 0.2 else 1
        )

        # Format: non-empty and not absurdly long relative to the reference.
        answer = req.platform_answer.strip()
        if not answer:
            format_adherence = 1
        elif len(answer) > max(400, 6 * len(req.judge_reference)):
            format_adherence = 3
        else:
            format_adherence = 5

        # SQL plausibility: the mock sees only Platform-generated SQL, matching
        # the production judge's no-reference-SQL boundary. This remains a
        # syntax-shaped CI heuristic, not a semantic judgment.
        sql = (req.generated_sql or "").strip()
        if not sql:
            sql_plausibility = 1  # no SQL → 1, per _rubric.jinja
        elif not sql.lower().startswith(("select", "with")):
            sql_plausibility = 2
        else:
            sql_plausibility = 5 if _looks_balanced(sql) else 3

        dimension_rationales = {
            "factual_correctness": (
                f"Mock numeric comparison matched {len(answer_nums)} of "
                f"{len(expected_nums)} expected numeric values."
            ),
            "completeness": f"Mock reference token coverage was {covered:.2f}.",
            "format_adherence": "Mock format heuristic checked presence and excessive length.",
            "sql_plausibility": (
                "Platform-generated SQL was present and structurally plausible."
                if sql_plausibility >= 3
                else "Platform-generated SQL was missing or structurally implausible."
            ),
        }

        return JudgeVerdict(
            dimension_rationales=dimension_rationales,
            factual_correctness=factual,
            completeness=completeness,
            format_adherence=format_adherence,
            sql_plausibility=sql_plausibility,
            prompt_version=prompt_version(),
            model_version=self.model_version,
        )


def _looks_balanced(sql: str) -> bool:
    return sql.count("(") == sql.count(")") and bool(sql.rstrip(" ;"))
