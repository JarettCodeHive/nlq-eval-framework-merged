"""Checks the Execution Scope specifies but that neither exact-match nor the
judge performs: rephrase-group agreement (§9.5) and T3 NULL handling (§9.2).

Both are **diagnostics reported alongside** the two scores, never folded into
them. §11.3 forbids composites, and §22 puts per-stage pipeline evaluation out of
scope — so nothing here changes an `exact_match_result` or a judge dimension. A
finding here points at a platform behaviour a reader should look at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from judge.exact_match import ExactMatchOutcome, ExactMatchResult

__all__ = [
    "CheckStatus",
    "NullHandlingCheck",
    "RephraseGroupFinding",
    "check_null_handling",
    "check_rephrase_groups",
]


class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"  # the check applies but the evidence is missing


# --- §9.2 T3: "exact_match plus explicit NULL-handling check" ---------------

# An outer join, or any construct that reproduces its semantics. NOT EXISTS /
# NOT IN / a null-guarded filter all preserve the unmatched rows a LEFT JOIN
# preserves, so a platform that reaches the right answer another way passes.
_NULL_AWARE = re.compile(
    r"\bleft\s+(?:outer\s+)?join\b"
    r"|\bright\s+(?:outer\s+)?join\b"
    r"|\bfull\s+(?:outer\s+)?join\b"
    r"|\bis\s+(?:not\s+)?null\b"
    r"|\bcoalesce\s*\("
    r"|\bifnull\s*\("
    r"|\bnot\s+exists\b"
    r"|\bnot\s+in\s*\(",
    re.IGNORECASE,
)
_INNER_JOIN = re.compile(r"\b(?:inner\s+join|\bjoin)\b", re.IGNORECASE)
_OUTER_IN_REFERENCE = re.compile(
    r"\b(?:left|right|full)\s+(?:outer\s+)?join\b", re.IGNORECASE
)


@dataclass(frozen=True)
class NullHandlingCheck:
    status: CheckStatus
    detail: str = ""

    @property
    def is_finding(self) -> bool:
        return self.status is CheckStatus.FAIL


def check_null_handling(
    *,
    tier: str | None,
    reference_sql: str | None,
    platform_sql: str | None,
) -> NullHandlingCheck:
    """§9.2's T3 requirement, made concrete.

    The Scope names "an explicit NULL-handling check" for T3 without defining
    it. What T3 exists to test is whether the platform preserves the rows a LEFT
    OUTER JOIN preserves — the classic failure is answering an outer-join
    question with an inner join, which silently drops every unmatched row and
    returns a plausible, smaller, wrong number.

    So: applicable when the pair is T3 or its reference SQL uses an outer join.
    PASS when the platform's SQL contains an outer join or any equivalent
    null-aware construct. FAIL when it contains joins but all of them are inner.
    UNKNOWN when the platform returned no SQL — the check applies but there is
    nothing to inspect, and that is not the same as passing.
    """
    tier_is_t3 = (tier or "").strip().upper() == "T3"
    reference_is_outer = bool(_OUTER_IN_REFERENCE.search(reference_sql or ""))
    if not (tier_is_t3 or reference_is_outer):
        return NullHandlingCheck(
            CheckStatus.NOT_APPLICABLE, "not a T3 / outer-join pair"
        )

    sql = (platform_sql or "").strip()
    if not sql:
        return NullHandlingCheck(
            CheckStatus.UNKNOWN,
            "outer-join pair, but the platform returned no SQL to inspect",
        )
    if _NULL_AWARE.search(sql):
        return NullHandlingCheck(
            CheckStatus.PASS, "platform SQL preserves unmatched rows"
        )
    if _INNER_JOIN.search(sql):
        return NullHandlingCheck(
            CheckStatus.FAIL,
            "outer-join pair answered with inner joins only — unmatched rows are "
            "silently dropped",
        )
    return NullHandlingCheck(
        CheckStatus.UNKNOWN,
        "outer-join pair, but the platform SQL declares no join to classify",
    )


# --- §9.5 rephrase groups ---------------------------------------------------


@dataclass
class RephraseGroupFinding:
    """One `rephrase_group_id` and whether its variants agreed.

    §9.5: "every variant must return identical numerics. A group where variants
    disagree is a platform finding, reported in the scorecard drill-down — it is
    not a defect in our dataset."
    """

    group_id: str
    question_ids: list[str] = field(default_factory=list)
    # Variants disagreed on which ground-truth values they returned.
    numerics_disagree: bool = False
    # The variants do not share one expected_answer. That IS a dataset defect —
    # a rephrase group asks one underlying question (§9.5) — so it is reported
    # separately from a platform finding and never blamed on the platform.
    expected_answer_inconsistent: bool = False
    detail: str = ""

    @property
    def is_platform_finding(self) -> bool:
        return self.numerics_disagree and not self.expected_answer_inconsistent

    @property
    def is_dataset_finding(self) -> bool:
        return self.expected_answer_inconsistent


def check_rephrase_groups(
    rows: list[tuple[dict, str, ExactMatchOutcome]],
) -> list[RephraseGroupFinding]:
    """Group `(pair, expected_answer, outcome)` by `rephrase_group_id` and test
    agreement.

    Agreement is compared on the *satisfied requirement keys* rather than on
    every numeral in the prose: two variants of one question must resolve the
    same ground-truth values, and an answer's incidental figures (a category
    breakdown, a percentage) are not part of what §9.5 is asking about.

    Singleton groups are skipped — §9.5 defines a group as two or more variants.
    """
    grouped: dict[str, list[tuple[dict, str, ExactMatchOutcome]]] = {}
    for pair, expected, outcome in rows:
        gid = (pair.get("rephrase_group_id") or "").strip()
        if gid:
            grouped.setdefault(gid, []).append((pair, expected, outcome))

    findings: list[RephraseGroupFinding] = []
    for gid in sorted(grouped):
        members = grouped[gid]
        if len(members) < 2:
            continue

        qids = [str(p.get("question_id")) for p, _, _ in members]
        expected_forms = {e.strip() for _, e, _ in members}

        scorable = [
            (p, o)
            for p, _, o in members
            if o.result not in (ExactMatchResult.ERROR, ExactMatchResult.NOT_APPLICABLE)
        ]
        signatures = {o.satisfied for _, o in scorable}

        finding = RephraseGroupFinding(
            group_id=gid,
            question_ids=qids,
            numerics_disagree=len(signatures) > 1,
            expected_answer_inconsistent=len(expected_forms) > 1,
        )

        notes: list[str] = []
        if finding.expected_answer_inconsistent:
            notes.append(
                f"variants declare {len(expected_forms)} different expected_answers "
                "— a rephrase group must ask one underlying question (§9.5); this "
                "is a dataset defect, not a platform one"
            )
        if finding.numerics_disagree:
            per_variant = ", ".join(
                f"{p.get('question_id')}={'|'.join(o.satisfied) or '(none)'}"
                for p, o in scorable
            )
            notes.append(
                f"variants resolved different ground-truth values: {per_variant}"
            )
        if len(scorable) < len(members):
            notes.append(
                f"{len(members) - len(scorable)}/{len(members)} variants were not "
                "scorable (platform error or no deterministic core)"
            )
        finding.detail = "; ".join(notes) or "all variants agree"
        findings.append(finding)
    return findings
