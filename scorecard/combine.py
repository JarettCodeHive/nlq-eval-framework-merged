"""Combine per-domain evaluation runs into one scorecard — the §14.1 artefact.

WHY THIS IS A SEPARATE STEP
---------------------------
`establish_baseline` is keyed on `platform_version` alone and is write-once. So
if each domain's `judge` run established its own baseline, the first domain to
finish would write a baseline containing only itself, and every later domain
would find a baseline that does not mention it — `has_baseline` false, no
comparison, forever, because the file is then made read-only.

The fix is a division of labour: a `judge` run scores one domain and stays
PREVIEW; this step merges those runs and is the only thing that establishes or
compares a baseline. §14.1 wants "baseline established across all five
domains", which is a cross-domain object by definition.

WHAT IT DOES NOT DO
-------------------
It does not re-score anything. Each input run's `results.json` is evidence —
its manifest pins the commit, input hash and argv, and `pulse_raw/` holds the
untouched platform payloads. This reads those files and writes a new directory,
leaving every input untouched.

    python main.py score --domains crm,sales --release

Aggregation is the existing `scorecard.summary` machinery, which was already
per-domain; this supplies it with rows from several runs instead of one.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from judge.merge_runs import _rebuild
from scorecard.baseline import BaselineExists, establish_baseline, load_baseline
from scorecard.config import combined_report_dir
from scorecard.report import write_scorecard_md, write_scorecard_pdf
from scorecard.summary import (
    RunContext,
    build_summary_rows,
    write_question_results_csv,
    write_scorecard_summary_csv,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Answer sources that are not the platform. A combined RELEASE scorecard refuses
# them for the same reason a single run does (HC-4): a baseline built from our
# own reference SQL measures our own SQL, not the system under evaluation.
_STANDIN_PULSE = {"sql"}


class CombineError(RuntimeError):
    """The inputs cannot be combined, with the reason attached."""


@dataclass
class RunInput:
    """One domain's `judge` run, loaded from disk."""

    domain: str
    run_id: str
    path: Path
    payload: dict

    @property
    def rows(self) -> list[dict]:
        return self.payload.get("rows") or []

    @property
    def summary(self) -> dict:
        return self.payload.get("summary") or {}

    @property
    def platform_version(self) -> str:
        return (self.payload.get("platform_version") or "").strip()

    @property
    def dataset_version(self) -> str:
        return (self.payload.get("dataset_version") or "").strip()

    @property
    def pulse_mode(self) -> str:
        return (self.payload.get("pulse_mode") or "").strip()

    @property
    def judge_name(self) -> str:
        return (self.payload.get("judge") or "").strip()

    @property
    def calibrated(self) -> bool:
        return bool(self.summary.get("calibrated"))

    @property
    def comparison_is_default(self) -> bool:
        # Absent means an older run that predates the flag; assume the default
        # rather than inventing a blocker out of a missing field.
        return bool(self.summary.get("comparison_is_default", True))

    @property
    def comparison_policy(self) -> str:
        return str(self.summary.get("comparison_policy") or "")

    @property
    def temperature_enforced(self) -> bool:
        return bool(self.summary.get("judge_temperature_enforced", True))

    @property
    def seed_enforced(self) -> bool:
        return bool(self.summary.get("judge_seed_enforced", True))

    @property
    def domains_present(self) -> set[str]:
        """Domains the rows actually claim, which need not match the directory."""

        return {str(row.get("domain") or "unknown") for row in self.rows}


@dataclass
class CombineOutcome:
    run_id: str
    out_dir: Path
    mode: str
    inputs: list[RunInput] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    baseline_established: Path | None = None
    regressions: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)


def eval_runs_root(domain: str) -> Path:
    """Where `judge` writes its runs for one domain."""

    from judge.config import load_judge_config

    root = load_judge_config(domain).run_output_root.format(domain=domain)
    return REPO_ROOT / root


def discover_run_ids(domain: str) -> list[str]:
    """Run ids for a domain, oldest first. Only directories with results."""

    root = eval_runs_root(domain)
    if not root.is_dir():
        return []
    return sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir() and (path / "results.json").is_file()
    )


def load_run(domain: str, run_id: str) -> RunInput:
    path = eval_runs_root(domain) / run_id / "results.json"
    if not path.is_file():
        raise CombineError(f"no results.json for {domain}/{run_id} at {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise CombineError(f"{path} is not valid JSON: {exc}") from exc
    return RunInput(domain=domain, run_id=run_id, path=path, payload=payload)


def collect(
    domains: list[str], pinned: dict[str, str] | None = None
) -> list[RunInput]:
    """Load one run per domain — the pinned id, else the most recent.

    "Most recent" is the lexically largest run id, which is a UTC timestamp, so
    it is also chronological.
    """

    pinned = pinned or {}
    inputs: list[RunInput] = []
    for domain in domains:
        run_id = pinned.get(domain)
        if run_id is None:
            available = discover_run_ids(domain)
            if not available:
                raise CombineError(
                    f"no evaluation runs found for {domain!r} under "
                    f"{eval_runs_root(domain)}. Run: python main.py judge "
                    f"--domain {domain} --profile full"
                )
            run_id = available[-1]
        inputs.append(load_run(domain, run_id))
    return inputs


def domains_with_runs() -> list[str]:
    """Every domain that has at least one evaluation run on disk."""

    from generators.domain_registry import DATASET_DOMAINS

    # Judge configs cover more domains than the generators do, so consider both.
    candidates = set(DATASET_DOMAINS)
    config_dir = REPO_ROOT / "config" / "judge"
    if config_dir.is_dir():
        candidates |= {
            path.stem for path in config_dir.glob("*.json") if path.stem != "default"
        }
    return sorted(d for d in candidates if discover_run_ids(d))


def release_blockers(inputs: list[RunInput]) -> list[str]:
    """Why these runs may not establish or be compared to a baseline.

    A combined RELEASE scorecard asserts that several runs describe ONE platform
    state. Every condition here is part of that assertion.
    """

    blockers: list[str] = []

    duplicates = sorted(
        {r.domain for r in inputs if [i.domain for i in inputs].count(r.domain) > 1}
    )
    if duplicates:
        blockers.append(
            f"more than one run supplied for {', '.join(duplicates)} — a domain "
            "would be counted twice"
        )

    for run in inputs:
        if run.judge_name != "llm":
            blockers.append(
                f"{run.domain}/{run.run_id} was scored by --judge "
                f"{run.judge_name!r}; the heuristic test double is never "
                "release-eligible"
            )
        if run.pulse_mode in _STANDIN_PULSE:
            blockers.append(
                f"{run.domain}/{run.run_id} used --pulse {run.pulse_mode!r}, which "
                "does not reach the platform (HC-4)"
            )
        if not run.calibrated:
            blockers.append(
                f"{run.domain}/{run.run_id} was scored by an uncalibrated judge "
                "(§10.2)"
            )
        if not run.comparison_is_default:
            blockers.append(
                f"{run.domain}/{run.run_id} relaxed the exact-match comparison "
                "policy, so it cannot be a baseline (OI-2 / OI-3)"
            )
        if not run.platform_version:
            blockers.append(
                f"{run.domain}/{run.run_id} carries no platform_version; the "
                "baseline is keyed on it (§11.1)"
            )
        if not run.dataset_version:
            blockers.append(
                f"{run.domain}/{run.run_id} carries no dataset_version (§11.1)"
            )
        stray = run.domains_present - {run.domain}
        if stray:
            blockers.append(
                f"{run.domain}/{run.run_id} contains rows for {', '.join(sorted(stray))} "
                "— a run's rows must belong to the domain it was run for"
            )

    versions = {r.platform_version for r in inputs if r.platform_version}
    if len(versions) > 1:
        blockers.append(
            "the runs describe different platform versions "
            f"({', '.join(sorted(versions))}); a combined scorecard must cover one "
            "platform state"
        )
    return blockers


def _results_from(inputs: list[RunInput]) -> list:
    """Rehydrate every run's rows into the shape the scorecard writers expect."""

    results = []
    for run in inputs:
        for row in run.rows:
            # judge_reference is not persisted in results.json; the judge's own
            # scores are already recorded, and the writers only need it to build
            # a JudgeRequest they never re-score. Falling back to
            # expected_answer keeps the contract satisfied without inventing one.
            results.append(_rebuild(row, row.get("expected_answer", "")))
    return results


def _shared_version(versions: set[str]) -> str:
    """Collapse a set of per-run version strings into one reportable value.

    Three distinct cases, and conflating any two of them misreports provenance:
    one agreed value is reported as itself; several are reported as a pointer to
    the per-domain rows; none is reported as absent, because "multiple" would
    claim versions that were never recorded in the first place.
    """

    if len(versions) == 1:
        return next(iter(versions))
    if not versions:
        return ""
    return "multiple — see per-domain rows"


def combine(
    inputs: list[RunInput],
    *,
    release: bool = False,
    out_dir: Path | None = None,
    now: datetime | None = None,
) -> CombineOutcome:
    """Write one scorecard across several domains' runs."""

    if not inputs:
        raise CombineError("no runs to combine")

    moment = now or datetime.now(timezone.utc)
    run_id = f"combined-{moment.strftime('%Y%m%dT%H%M%SZ')}"
    target = out_dir or combined_report_dir(run_id)

    blockers = release_blockers(inputs) if release else []
    mode = "RELEASE" if release and not blockers else "PREVIEW"

    versions = {r.platform_version for r in inputs if r.platform_version}
    platform_version = versions.pop() if len(versions) == 1 else ""
    dataset_versions = {r.dataset_version for r in inputs if r.dataset_version}

    ctx = RunContext(
        run_id=run_id,
        run_timestamp_iso=moment.isoformat(),
        platform_version=platform_version,
        # One shared value only when every run agrees; when they disagree the
        # per-domain map carries the truth and this stays deliberately vague
        # rather than claiming one domain's version applies to all. An EMPTY set
        # is a third case: no run recorded a version at all, and saying
        # "multiple" there would invent versions that were never captured.
        dataset_version=_shared_version(dataset_versions),
        per_domain_dataset_version={
            r.domain: r.dataset_version for r in inputs if r.dataset_version
        },
        scorecard_mode=mode,
        calibrated=all(r.calibrated for r in inputs),
        comparison_policy=(
            inputs[0].comparison_policy or RunContext.comparison_policy
        ),
        comparison_is_default=all(r.comparison_is_default for r in inputs),
        judge_temperature_enforced=all(r.temperature_enforced for r in inputs),
        judge_seed_enforced=all(r.seed_enforced for r in inputs),
        provenance_note=(
            "combined from "
            + ", ".join(f"{r.domain}/{r.run_id}" for r in inputs)
        ),
    )

    results = _results_from(inputs)
    baseline = load_baseline(platform_version) if platform_version else None
    rows, comparisons = build_summary_rows(results, ctx, baseline=baseline)

    target.mkdir(parents=True, exist_ok=True)
    write_scorecard_summary_csv(target, results, ctx, baseline=baseline)
    write_question_results_csv(target, results, ctx)
    write_scorecard_md(target, results, ctx, baseline=baseline)
    try:
        write_scorecard_pdf(target, results, ctx, baseline=baseline)
    except ImportError as exc:
        # §11.3 requires the PDF; a missing reportlab must not pass silently.
        print(f"[score] WARNING: PDF skipped — {exc}")

    outcome = CombineOutcome(
        run_id=run_id,
        out_dir=target,
        mode=mode,
        inputs=inputs,
        rows=rows,
        blockers=blockers,
    )

    if mode == "RELEASE":
        if baseline is None:
            per_domain = {
                domain: comparison.current_exact_match_pct
                for domain, comparison in comparisons.items()
                if comparison.current_exact_match_pct is not None
            }
            try:
                outcome.baseline_established = establish_baseline(
                    platform_version,
                    per_domain,
                    run_id=run_id,
                    run_timestamp_iso=ctx.run_timestamp_iso,
                    dataset_version=ctx.dataset_version,
                )
            except BaselineExists as exc:  # racy re-check
                outcome.blockers.append(str(exc))
        else:
            outcome.regressions = sorted(
                domain
                for domain, comparison in comparisons.items()
                if comparison.regression_flag
            )

    # Manifest of what went in, so the scorecard is traceable to its evidence.
    (target / "inputs.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "scorecard_mode": mode,
                "platform_version": platform_version,
                "release_blockers": outcome.blockers,
                "inputs": [
                    {
                        "domain": r.domain,
                        "run_id": r.run_id,
                        "results_json": str(r.path),
                        "rows": len(r.rows),
                        "pulse_mode": r.pulse_mode,
                        "judge": r.judge_name,
                        "calibrated": r.calibrated,
                        "platform_version": r.platform_version,
                        "dataset_version": r.dataset_version,
                    }
                    for r in inputs
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return outcome


def summarise(outcome: CombineOutcome) -> str:
    """What happened, for the console."""

    lines = [
        f"Combined scorecard — {outcome.run_id}  [{outcome.mode}]",
        f"output: {outcome.out_dir}",
        "inputs:",
    ]
    for run in outcome.inputs:
        lines.append(
            f"  {run.domain:20} {run.run_id}  rows={len(run.rows):<5} "
            f"pulse={run.pulse_mode or '?'}  "
            f"calibrated={'yes' if run.calibrated else 'NO'}"
        )
    lines.append("per domain:")
    for row in outcome.rows:
        if row["tier"] != "ALL":
            continue
        pct = row.get("exact_match_pct")
        lines.append(
            f"  {row['domain']:20} exact_match="
            f"{'—' if pct is None else f'{pct:.2f}%'}  "
            f"({row.get('exact_match_pass')}/{row.get('questions_total')})  "
            f"judge_overall={row.get('judge_overall')}"
        )
    if outcome.baseline_established:
        lines.append(f"BASELINE ESTABLISHED → {outcome.baseline_established}")
    if outcome.regressions:
        lines.append(
            f"REGRESSION FLAG: {', '.join(outcome.regressions)} dropped >= 5 pp "
            "against baseline (§11.3)"
        )
    if outcome.blockers:
        lines.append("release blocked:")
        lines.extend(f"  - {blocker}" for blocker in outcome.blockers)
    return "\n".join(lines)


def build_argparser(prog: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Combine per-domain judge runs into one regression scorecard.",
    )
    parser.add_argument(
        "--domains",
        default="",
        help="comma-separated domains to combine. Default: every domain that "
        "has an evaluation run on disk.",
    )
    parser.add_argument(
        "--run",
        action="append",
        default=[],
        metavar="DOMAIN=RUN_ID",
        help="pin one domain to a specific run id instead of its most recent. "
        "Repeatable.",
    )
    parser.add_argument(
        "--release",
        action="store_true",
        help="produce an official RELEASE scorecard: establishes the baseline on "
        "the first run for this platform_version and compares against it "
        "afterwards. Requires every input to be a calibrated --judge llm run "
        "against --pulse live, all on one platform_version.",
    )
    parser.add_argument(
        "--out",
        default="",
        help="write here instead of release/scorecards/<run_id>/.",
    )
    return parser


def run_from_args(args: argparse.Namespace) -> int:
    pinned: dict[str, str] = {}
    for item in args.run:
        if "=" not in item:
            print(f"[score] --run needs DOMAIN=RUN_ID, got {item!r}")
            return 2
        domain, run_id = item.split("=", 1)
        pinned[domain.strip()] = run_id.strip()

    domains = [d.strip() for d in args.domains.split(",") if d.strip()]
    if not domains:
        domains = sorted(set(domains_with_runs()) | set(pinned))
    if not domains:
        print(
            "[score] no evaluation runs found for any domain. Run "
            "`python main.py judge --domain <domain> --profile full` first."
        )
        return 2

    try:
        inputs = collect(domains, pinned)
        outcome = combine(
            inputs, release=args.release, out_dir=Path(args.out) if args.out else None
        )
    except CombineError as exc:
        print(f"[score] {exc}")
        return 2

    print(summarise(outcome))
    if outcome.regressions:
        return 4  # same convention as a judge run flagging a regression
    if args.release and outcome.blockers:
        return 3
    return 0


def main() -> None:
    raise SystemExit(run_from_args(build_argparser().parse_args()))


if __name__ == "__main__":
    main()
