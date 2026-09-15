"""Calibration protocol — §10.2.

This module ships:
  1. Anchor loader
  2. An anchor-set strength test — §10.2 asks for anchors "spanning the score
     range — not 10 easy passes", and that phrase has to be enforceable
  3. The acceptance test (within ±1 of the human score on ≥90% of anchors per
     dimension, never a directional flip)
  4. A marker-file API used by the CLI to enforce §10.2's last line —
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

WHAT THE MARKER IS BOUND TO
---------------------------
A pass is a pass *of a specific judge*: this model, this prompt revision, this
scoring mode. §10.2 says calibration must complete before any production scoring
run, which is only meaningful if the run being gated is the thing that was
calibrated — otherwise a marker earned on one model silently licenses another.
So `record_passed` stamps a :class:`JudgeFingerprint` and `check_calibrated`
refuses a run whose fingerprint differs.
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

ACCEPTANCE_MIN_ANCHORS = 10  # §10.2 "≥10 anchors per domain"
ACCEPTANCE_AGREEMENT_PCT = 90.0  # §10.2 "within ±1 on ≥90% of anchors"
DIRECTIONAL_FLIP_ALLOWED = False  # §10.2 "never disagrees on direction"

# §10.2 "spanning the score range — not 10 easy passes", operationalised. Both
# conditions must hold per dimension:
#   1. at least this many distinct human scores appear, and
#   2. no constant judge passes the acceptance test on that dimension.
# (2) is the one that bites. A dimension graded 5,5,5,5,5,5,5,5,3,2 is "spread"
# by eye, yet a judge that emits a flat 4 and reads nothing scores 90% within ±1
# with no directional flip — it passes while measuring nothing at all.
ANCHOR_MIN_DISTINCT_SCORES = 3


class AnchorSetTooWeak(ValueError):
    """The anchor set cannot distinguish a working judge from a constant one."""


@dataclass(frozen=True)
class JudgeFingerprint:
    """The identity of the judge a calibration result belongs to."""

    model_version: str
    prompt_version: str
    mode: str

    def as_dict(self) -> dict[str, str]:
        return {
            "model_version": self.model_version,
            "prompt_version": self.prompt_version,
            "mode": self.mode,
        }

    def differences(self, other: "JudgeFingerprint") -> list[str]:
        out = []
        for label, mine, theirs in (
            ("model_version", self.model_version, other.model_version),
            ("prompt_version", self.prompt_version, other.prompt_version),
            ("mode", self.mode, other.mode),
        ):
            if mine != theirs:
                out.append(f"{label}: calibrated {theirs!r}, this run {mine!r}")
        return out


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
    """A directional disagreement, exactly as §10.2 defines it:

        "never disagrees on direction (a human 5 scored as a 1 or 2, or the
         reverse) on any anchor"

    So: human 5 → judge 1 or 2, and human 1 or 2 → judge 5. Nothing wider —
    a human 4 scored 2 is caught by the ±1 agreement rule, not by this one, and
    treating it as a flip would make calibration harder to pass than the
    contract requires.
    """
    return (human == 5 and judge in {1, 2}) or (human in {1, 2} and judge == 5)


def load_anchors(domain: str, anchors_dir: Path | None = None) -> list[dict]:
    path = (anchors_dir or ANCHORS_DIR) / f"{domain}.json"
    if not path.is_file():
        available = sorted(p.stem for p in (anchors_dir or ANCHORS_DIR).glob("*.json"))
        raise FileNotFoundError(
            f"No calibration anchors for domain={domain!r} at {path}.\n"
            "Populate this file from the human calibration session (§10.2). "
            f"Present: {available or 'none'}."
        )
    anchors = json.loads(path.read_text(encoding="utf-8"))
    for a in anchors:
        for d in DIMENSIONS:
            if d not in a["human_scores"]:
                raise ValueError(
                    f"anchor {a['question_id']!r}: missing human score for {d!r}"
                )
    return anchors


# --------------------------------------------------------- anchor strength --


@dataclass(frozen=True)
class AnchorDimensionStrength:
    dimension: str
    distinct_scores: tuple[int, ...]
    # Constant scores k that would pass the acceptance test on this dimension.
    passing_constants: tuple[int, ...]

    @property
    def passes(self) -> bool:
        return (
            len(self.distinct_scores) >= ANCHOR_MIN_DISTINCT_SCORES
            and not self.passing_constants
        )

    @property
    def reason(self) -> str:
        problems = []
        if len(self.distinct_scores) < ANCHOR_MIN_DISTINCT_SCORES:
            problems.append(
                f"only {len(self.distinct_scores)} distinct human score(s) "
                f"{list(self.distinct_scores)}; §10.2 wants the range spanned"
            )
        if self.passing_constants:
            problems.append(
                "a judge that ignores its input and always answers "
                f"{'/'.join(str(k) for k in self.passing_constants)} would PASS "
                "this dimension"
            )
        return "; ".join(problems)


def anchor_strength(anchors: list[dict]) -> dict[str, AnchorDimensionStrength]:
    """Per-dimension discriminating power of an anchor set."""
    out: dict[str, AnchorDimensionStrength] = {}
    for dimension in DIMENSIONS:
        humans = [a["human_scores"][dimension] for a in anchors]
        distinct = tuple(sorted(set(humans)))
        passing = []
        for k in range(1, 6):
            within = sum(1 for h in humans if abs(h - k) <= 1)
            pct = (within / len(humans) * 100.0) if humans else 0.0
            flips = sum(1 for h in humans if _is_directional_flip(h, k))
            if pct >= ACCEPTANCE_AGREEMENT_PCT and flips == 0:
                passing.append(k)
        out[dimension] = AnchorDimensionStrength(
            dimension=dimension,
            distinct_scores=distinct,
            passing_constants=tuple(passing),
        )
    return out


def assert_anchor_set_usable(domain: str, anchors: list[dict]) -> None:
    """Refuse to calibrate against a set that cannot detect a broken judge."""
    if len(anchors) < ACCEPTANCE_MIN_ANCHORS:
        raise ValueError(
            f"§10.2 requires ≥{ACCEPTANCE_MIN_ANCHORS} anchors per domain; "
            f"got {len(anchors)}."
        )
    strength = anchor_strength(anchors)
    weak = {d: s for d, s in strength.items() if not s.passes}
    if weak:
        lines = "\n".join(f"  - {d}: {s.reason}" for d, s in sorted(weak.items()))
        raise AnchorSetTooWeak(
            f"the {domain!r} anchor set cannot certify a judge (§10.2 "
            '"spanning the score range — not 10 easy passes"):\n'
            f"{lines}\n"
            "Add anchors that a wrong judge would get wrong — above all a case "
            "where the platform returns an incorrect NUMBER and the human score "
            "for factual_correctness is 1. That is the case §HC-3 exists to "
            "catch, and an anchor set without it cannot detect a judge that "
            "never scores 1."
        )


def evaluate(
    domain: str, judge_verdicts: dict[str, JudgeVerdict], anchors: list[dict]
) -> CalibrationResult:
    """Run the acceptance test given anchors + judge verdicts keyed by question_id.

    Missing verdicts raise — an anchor without a verdict is not a scoring
    ambiguity, it is an incomplete run.
    """
    assert_anchor_set_usable(domain, anchors)
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


@dataclass(frozen=True)
class CalibrationState:
    """Whether a run may score, and why not when it may not."""

    calibrated: bool
    reason: str = ""
    marker_fingerprint: JudgeFingerprint | None = None

    @property
    def stale(self) -> bool:
        """A marker exists but was earned by a different judge."""
        return not self.calibrated and self.marker_fingerprint is not None


def _marker_path(domain: str, calibration_dir: Path | None = None) -> Path:
    return (calibration_dir or CALIBRATION_DIR) / f"{domain}.passed.json"


def check_calibrated(
    domain: str,
    fingerprint: JudgeFingerprint | None = None,
    calibration_dir: Path | None = None,
) -> CalibrationState:
    """§10.2 last line — 'uncalibrated scores never enter the scorecard.'

    With a `fingerprint`, the marker must also match the judge about to run.
    Passing `None` answers only "does a marker exist", which is what a report
    reprinting an old run needs.
    """
    path = _marker_path(domain, calibration_dir)
    if not path.is_file():
        return CalibrationState(False, f"no calibration marker at {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return CalibrationState(False, f"calibration marker unreadable: {exc}")

    raw = payload.get("judge_fingerprint")
    if not isinstance(raw, dict):
        # A marker written before fingerprints existed cannot vouch for any
        # specific judge, so it cannot license one.
        return CalibrationState(
            False,
            f"calibration marker at {path} predates judge fingerprinting and "
            "records no model/prompt/mode — re-run calibration to reissue it",
        )
    marker_fp = JudgeFingerprint(
        model_version=str(raw.get("model_version", "")),
        prompt_version=str(raw.get("prompt_version", "")),
        mode=str(raw.get("mode", "")),
    )
    if fingerprint is None:
        return CalibrationState(True, "marker present", marker_fp)
    diffs = fingerprint.differences(marker_fp)
    if diffs:
        return CalibrationState(
            False,
            "calibration marker was earned by a different judge — " + "; ".join(diffs),
            marker_fp,
        )
    return CalibrationState(True, "marker matches this judge", marker_fp)


def is_calibrated(
    domain: str,
    fingerprint: JudgeFingerprint | None = None,
    calibration_dir: Path | None = None,
) -> bool:
    """Boolean form of :func:`check_calibrated`, for call sites that only gate."""
    return check_calibrated(domain, fingerprint, calibration_dir).calibrated


def record_passed(
    domain: str,
    result: CalibrationResult,
    *,
    fingerprint: JudgeFingerprint,
    calibration_dir: Path | None = None,
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
        # A pass belongs to one judge, not to the domain in the abstract.
        "judge_fingerprint": fingerprint.as_dict(),
        "result": result.as_dict(),
    }
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p
