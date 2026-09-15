"""Scorecard aggregation and the two contracted CSV outputs (§11.1, §11.2)."""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from judge.contracts import DIMENSIONS, JudgeVerdict
from judge.exact_match import ExactMatchResult
from scorecard.baseline import RegressionComparison, compare_to_baseline

# §11.1 dimension column names (short forms) mapped to the JudgeVerdict fields.
JUDGE_COLUMNS: dict[str, str] = {
    "judge_factual": "factual_correctness",
    "judge_completeness": "completeness",
    "judge_format": "format_adherence",
    "judge_sql": "sql_plausibility",
}

DOMAIN_TIER_LABEL = "ALL"  # the per-domain roll-up row's `tier` value

Result = tuple[dict, object, "JudgeVerdict | Exception", ExactMatchResult]

SUMMARY_FIELDNAMES: list[str] = [
    "run_id",
    "run_timestamp_iso",
    "platform_version",
    "dataset_version",
    "scorecard_mode",  # PREVIEW | RELEASE — provenance, not part of the raw §11.1 list
    "judge_temperature_enforced",  # provenance: determinism basis of the run
    "domain",
    "tier",
    "questions_total",
    "exact_match_pass",
    "exact_match_eligible",
    "exact_match_pct",
    *JUDGE_COLUMNS.keys(),
    "judge_overall",
    "judge_errors",
    "platform_errors",
    "baseline_exact_match_pct",
    "delta_pct",
    "regression_flag",
]

QUESTION_FIELDNAMES: list[str] = [
    "run_id",
    "question_id",
    "domain",
    "tier",
    "natural_language_question",
    "expected_answer",
    "actual_answer",
    "exact_match_result",
    "platform_generated_sql",
    *JUDGE_COLUMNS.keys(),
    "judge_overall",
    "judge_rationale",
    "judge_error",
    "platform_error",
    "rephrase_group_id",
]


@dataclass(frozen=True)
class RunContext:
    """Everything a scorecard row needs about the run that produced it."""

    run_id: str
    run_timestamp_iso: str
    platform_version: str
    dataset_version: str
    scorecard_mode: str = "PREVIEW"  # PREVIEW | RELEASE
    calibrated: bool = False
    # "off" = certified §HC-3 byte-identical; otherwise the OI-2 relaxation label.
    numeric_normalization: str = "off"
    # False when the judge model refused temperature 0 and the run continued on
    # the model default + fixed seed. Determinism then rests on the seed
    # (§10.1) — a reader of the scorecard has to be able to see that.
    judge_temperature_enforced: bool = True


@dataclass
class GroupStats:
    """Aggregated numbers for one (domain) or (domain, tier) bucket."""

    questions_total: int = 0
    exact_match_pass: int = 0
    exact_match_eligible: int = 0
    judge_errors: int = 0
    platform_errors: int = 0
    _dim_sums: dict[str, float] = field(
        default_factory=lambda: dict.fromkeys(DIMENSIONS, 0.0)
    )
    _judged: int = 0

    def add(self, verdict: object, em: ExactMatchResult) -> None:
        self.questions_total += 1
        if em == ExactMatchResult.ERROR:
            # Platform never answered — infra, not accuracy. Not judged, not in
            # the exact-match denominator.
            self.platform_errors += 1
            return
        if em == ExactMatchResult.PASS:
            self.exact_match_pass += 1
            self.exact_match_eligible += 1
        elif em == ExactMatchResult.FAIL:
            self.exact_match_eligible += 1
        if isinstance(verdict, JudgeVerdict):
            self._judged += 1
            for dim in DIMENSIONS:
                self._dim_sums[dim] += getattr(verdict, dim)
        elif isinstance(verdict, Exception):
            self.judge_errors += 1

    @property
    def exact_match_pct(self) -> float | None:
        if self.exact_match_eligible == 0:
            return None
        return round(self.exact_match_pass / self.exact_match_eligible * 100.0, 4)

    def dim_mean(self, dim: str) -> float | None:
        if self._judged == 0:
            return None
        return round(self._dim_sums[dim] / self._judged, 4)

    @property
    def judge_overall(self) -> float | None:
        if self._judged == 0:
            return None
        return round(
            sum(self._dim_sums[d] for d in DIMENSIONS)
            / (self._judged * len(DIMENSIONS)),
            4,
        )


@dataclass
class DomainStats:
    overall: GroupStats = field(default_factory=GroupStats)
    tiers: dict[str, GroupStats] = field(default_factory=dict)


def aggregate(results: list[Result]) -> dict[str, DomainStats]:
    """Group results into per-domain roll-ups and per-tier breakouts."""
    out: dict[str, DomainStats] = defaultdict(DomainStats)
    for pair, _req, verdict, em in results:
        domain = str(pair.get("domain") or "unknown")
        tier = str(pair.get("tier") or "unknown")
        ds = out[domain]
        ds.overall.add(verdict, em)
        ds.tiers.setdefault(tier, GroupStats()).add(verdict, em)
    return dict(out)


def _summary_row(
    ctx: RunContext,
    domain: str,
    tier: str,
    stats: GroupStats,
    comparison: RegressionComparison | None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "run_id": ctx.run_id,
        "run_timestamp_iso": ctx.run_timestamp_iso,
        "platform_version": ctx.platform_version,
        "dataset_version": ctx.dataset_version,
        "scorecard_mode": ctx.scorecard_mode,
        "judge_temperature_enforced": ctx.judge_temperature_enforced,
        "domain": domain,
        "tier": tier,
        "questions_total": stats.questions_total,
        "exact_match_pass": stats.exact_match_pass,
        "exact_match_eligible": stats.exact_match_eligible,
        "exact_match_pct": stats.exact_match_pct,
        "judge_overall": stats.judge_overall,
        "judge_errors": stats.judge_errors,
        "platform_errors": stats.platform_errors,
        # Baseline comparison is domain-level only (§11.3) and only meaningful
        # once a baseline exists. Tier rows and pre-baseline runs leave it blank.
        "baseline_exact_match_pct": (
            comparison.baseline_exact_match_pct
            if comparison and comparison.has_baseline
            else None
        ),
        "delta_pct": (
            comparison.delta_pct if comparison and comparison.has_baseline else None
        ),
        "regression_flag": (
            comparison.regression_flag
            if comparison and comparison.has_baseline
            else None
        ),
    }
    for col, dim in JUDGE_COLUMNS.items():
        row[col] = stats.dim_mean(dim)
    return row


def build_summary_rows(
    results: list[Result],
    ctx: RunContext,
    *,
    baseline: dict | None = None,
) -> tuple[list[dict[str, object]], dict[str, RegressionComparison]]:
    """Return (summary rows, per-domain regression comparisons)."""
    agg = aggregate(results)
    per_domain_pct = {d: s.overall.exact_match_pct for d, s in agg.items()}
    comparisons = compare_to_baseline(baseline, per_domain_pct)

    rows: list[dict[str, object]] = []
    for domain in sorted(agg):
        ds = agg[domain]
        rows.append(
            _summary_row(
                ctx, domain, DOMAIN_TIER_LABEL, ds.overall, comparisons.get(domain)
            )
        )
        for tier in sorted(ds.tiers):
            rows.append(_summary_row(ctx, domain, tier, ds.tiers[tier], None))
    return rows, comparisons


def write_scorecard_summary_csv(
    out_dir: Path,
    results: list[Result],
    ctx: RunContext,
    *,
    baseline: dict | None = None,
) -> Path:
    """Write ``scorecard_summary.csv`` — one row per domain + per-tier breakout."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "scorecard_summary.csv"
    rows, _ = build_summary_rows(results, ctx, baseline=baseline)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _question_row(
    ctx: RunContext, pair: dict, req: object, verdict: object, em: ExactMatchResult
) -> dict[str, object]:
    row: dict[str, object] = {
        "run_id": ctx.run_id,
        "question_id": pair.get("question_id"),
        "domain": pair.get("domain"),
        "tier": pair.get("tier"),
        "natural_language_question": pair.get("natural_language_question")
        or getattr(req, "question", None),
        "expected_answer": getattr(req, "expected_answer", pair.get("expected_answer")),
        "actual_answer": getattr(req, "platform_answer", None),
        "exact_match_result": em.value,
        # §11.2 — logged for EVERY question, mandatory on failure.
        "platform_generated_sql": getattr(req, "generated_sql", None),
        "rephrase_group_id": pair.get("rephrase_group_id"),
        "judge_overall": None,
        "judge_rationale": None,
        "judge_error": None,
        "platform_error": None,
    }
    for col in JUDGE_COLUMNS:
        row[col] = None
    if isinstance(verdict, JudgeVerdict):
        for col, dim in JUDGE_COLUMNS.items():
            row[col] = getattr(verdict, dim)
        row["judge_overall"] = round(verdict.overall_score, 4)
        row["judge_rationale"] = verdict.rationale
    elif em == ExactMatchResult.ERROR:
        row["platform_error"] = str(verdict) if verdict is not None else "no answer"
    elif isinstance(verdict, Exception):
        row["judge_error"] = f"{type(verdict).__name__}: {verdict}"
    return row


def write_question_results_csv(
    out_dir: Path,
    results: list[Result],
    ctx: RunContext,
) -> Path:
    """Write ``question_results.csv`` — one row per evaluated question (§11.2)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "question_results.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=QUESTION_FIELDNAMES)
        writer.writeheader()
        for pair, req, verdict, em in results:
            writer.writerow(_question_row(ctx, pair, req, verdict, em))
    return path
