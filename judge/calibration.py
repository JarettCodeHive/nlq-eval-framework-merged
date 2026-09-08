"""Calibration protocol — §10.3.

The full loop is Wk 5 work (needs Platform Owner joint session for CRM anchors).
This module ships:
  1. Anchor loader
  2. Acceptance test (§10.3: within ±1 of human on ≥90% of anchors per dimension,
     never a directional flip)
  3. A tiny marker-file API used by the CLI to enforce §10.3 last line —
     "Uncalibrated scores never enter the scorecard."

Anchor file shape (per domain, e.g. anchors/crm.json):

    [
      {
        "question_id": "crm-anchor-001",
        "natural_language_question": "...",
        "expected_answer": "...",
        "judge_reference": "...",
        "reference_sql": "...",
        "platform_answer": "...",
        "generated_sql": "...",
        "human_scores": {
          "factual_correctness": 5,
          "completeness": 5,
          "format_adherence": 5,
          "sql_plausibility": 5
        }
      },
      ...
    ]

Anchor grades come from the human calibration session. Grades are RECONCILED
across the two humans BEFORE they enter this file — this module has no notion
of per-human votes, only the reconciled ground-truth score per anchor.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from judge.contracts import DIMENSIONS, JudgeVerdict

MODULE_ROOT = Path(__file__).resolve().parent
ANCHORS_DIR = MODULE_ROOT / "anchors"
CALIBRATION_DIR = MODULE_ROOT / ".calibration"

ACCEPTANCE_MIN_ANCHORS = 10  # §10.3 "≥10 anchors per domain"
ACCEPTANCE_AGREEMENT_PCT = 90.0  # §10.3 "within ±1 on ≥90% of anchors"
DIRECTIONAL_FLIP_ALLOWED = False  # §10.3 "never disagrees on direction"


@dataclass(frozen=True)
class DimensionResult:
    dimension: str
    within_pm1_count: int
    total: int
    directional_flip_count: int

    @property
    def within_pm1_pct(self) -> float:
        return (self.within_pm1_count / self.total * 100.0) if self.total else 0.0

    @property
    def passes(self) -> bool:
        return (
            self.within_pm1_pct >= ACCEPTANCE_AGREEMENT_PCT
            and self.directional_flip_count == 0
        )


@dataclass(frozen=True)
class CalibrationResult:
    domain: str
    anchor_count: int
    per_dimension: dict[str, DimensionResult]
    passed: bool

    def as_dict(self) -> dict:
        return {
            "domain": self.domain,
            "anchor_count": self.anchor_count,
            "passed": self.passed,
            "per_dimension": {
                d: {
                    "within_pm1_pct": r.within_pm1_pct,
                    "within_pm1_count": r.within_pm1_count,
                    "total": r.total,
                    "directional_flip_count": r.directional_flip_count,
                    "passes": r.passes,
                }
                for d, r in self.per_dimension.items()
            },
        }


def _is_directional_flip(human: int, judge: int) -> bool:
    """Human 5 → judge 1/2 (or reverse) is a directional flip.

    A 5 that the judge says is a 2 is a categorically different judgment than
    a 5 that the judge says is a 4, even though numerically both are two off.
    """
    high = {4, 5}
    low = {1, 2}
    return (human in high and judge in low) or (human in low and judge in high)


def load_anchors(domain: str, anchors_dir: Path | None = None) -> list[dict]:
    path = (anchors_dir or ANCHORS_DIR) / f"{domain}.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"No calibration anchors for domain={domain!r} at {path}.\n"
            "Populate this file from the human calibration session (§10.3)."
        )
    anchors = json.loads(path.read_text(encoding="utf-8"))
    for a in anchors:
        for d in DIMENSIONS:
            if d not in a["human_scores"]:
                raise ValueError(
                    f"anchor {a['question_id']!r}: missing human score for {d!r}"
                )
    return anchors


def evaluate(
    domain: str, judge_verdicts: dict[str, JudgeVerdict], anchors: list[dict]
) -> CalibrationResult:
    """Run acceptance test given anchors + judge verdicts keyed by question_id.

    Missing verdicts raise — an anchor without a verdict is not a scoring
    ambiguity, it is an incomplete run.
    """
    if len(anchors) < ACCEPTANCE_MIN_ANCHORS:
        raise ValueError(
            f"§10.3 requires ≥{ACCEPTANCE_MIN_ANCHORS} anchors per domain; got {len(anchors)}."
        )
    per_dim: dict[str, DimensionResult] = {}
    for d in DIMENSIONS:
        within = 0
        flips = 0
        for a in anchors:
            qid = a["question_id"]
            if qid not in judge_verdicts:
                raise KeyError(f"anchor {qid!r} has no judge verdict")
            h = a["human_scores"][d]
            j = getattr(judge_verdicts[qid], d)
            if abs(h - j) <= 1:
                within += 1
            if _is_directional_flip(h, j):
                flips += 1
        per_dim[d] = DimensionResult(
            dimension=d,
            within_pm1_count=within,
            total=len(anchors),
            directional_flip_count=flips,
        )
    passed = all(r.passes for r in per_dim.values())
    return CalibrationResult(
        domain=domain, anchor_count=len(anchors), per_dimension=per_dim, passed=passed
    )


# --------------------------------------------------------------- marker file --


def _marker_path(domain: str, calibration_dir: Path | None = None) -> Path:
    return (calibration_dir or CALIBRATION_DIR) / f"{domain}.passed.json"


def is_calibrated(domain: str, calibration_dir: Path | None = None) -> bool:
    """§10.3 last line — 'uncalibrated scores never enter the scorecard.'"""
    return _marker_path(domain, calibration_dir).is_file()


def record_passed(
    domain: str, result: CalibrationResult, calibration_dir: Path | None = None
) -> Path:
    """Write the marker file that unlocks scorecard writes for this domain."""
    if not result.passed:
        raise ValueError(f"cannot record a failed calibration for {domain!r}")
    p = _marker_path(domain, calibration_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "domain": domain,
        "passed_at_utc": datetime.now(timezone.utc).isoformat(),
        "acceptance_threshold_pct": ACCEPTANCE_AGREEMENT_PCT,
        "min_anchors": ACCEPTANCE_MIN_ANCHORS,
        "result": result.as_dict(),
    }
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p
