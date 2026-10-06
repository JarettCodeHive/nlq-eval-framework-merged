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
from collections.abc import Sequence
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
from qa_pairs.utils.release_bundle import active_release_version
from qa_pairs.utils.release_bundle import component_dir
from qa_pairs.utils.release_bundle import existing_component_path
from qa_pairs.utils.release_bundle import legacy_component_dir
from qa_pairs.utils.release_bundle import selected_release_version

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
    def partial_run(self) -> bool:
        """Absent on runs predating the flag; assume complete rather than invent
        a subset, which would silently withhold the §9.1 comparison."""

        return bool(self.summary.get("partial_run", False))

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


def _release_version(domain: str) -> str:
    version = selected_release_version()
    if not version:
        config_path = REPO_ROOT / "config" / "generation" / domain / "release.json"
        if config_path.is_file():
            version = active_release_version(
                json.loads(config_path.read_text(encoding="utf-8"))
            )
        else:
            # Isolated tests and imported historical run stores may not carry
            # generation config. The repository default remains deterministic.
            version = "v1.0.0"
    return version


def combined_release_version(domains: Sequence[str]) -> str:
    """The release version a combined scorecard belongs under.

    Each domain resolves its own version, so a combined report has no single
    version unless its inputs agree. `release_root` would otherwise answer with
    `default_domain="crm"`, which silently files a sales-only report under
    CRM's version — a directory whose name describes none of its contents, and
    which still answers when CRM is not among the combined domains at all.

    An explicit `--version` wins, because that is the operator stating which
    bundle this belongs to. Without one, disagreement is refused rather than
    guessed: picking either version would mislabel the other domain's runs.
    """

    selected = selected_release_version()
    if selected:
        return selected
    if not domains:
        raise CombineError("no domains to resolve a release version from")

    resolved = {domain: _release_version(domain) for domain in domains}
    distinct = sorted(set(resolved.values()))
    if len(distinct) > 1:
        detail = ", ".join(f"{d}={v}" for d, v in sorted(resolved.items()))
        raise CombineError(
            f"the combined domains are on different release versions ({detail}), "
            f"so this report has no single version to be filed under. Pass "
            f"--version to say which bundle it belongs to."
        )
    return distinct[0]


def eval_runs_root(domain: str) -> Path:
    """Canonical location where `judge` writes runs for one domain."""

    return component_dir(domain, "judge", _release_version(domain), repo_root=REPO_ROOT)


def _eval_run_roots(domain: str) -> tuple[Path, Path]:
    version = _release_version(domain)
    return (
        component_dir(domain, "judge", version, repo_root=REPO_ROOT),
        legacy_component_dir(domain, "judge", version, repo_root=REPO_ROOT),
    )


def discover_run_ids(domain: str) -> list[str]:
    """Run ids for a domain, oldest first. Only directories with results."""

    run_ids = {
        path.name
        for root in _eval_run_roots(domain)
        if root.is_dir()
        for path in root.iterdir()
        if path.is_dir() and (path / "results.json").is_file()
    }
    return sorted(run_ids)


def load_run(domain: str, run_id: str) -> RunInput:
    path = existing_component_path(
        domain,
        "judge",
        run_id,
        "results.json",
        version=_release_version(domain),
        repo_root=REPO_ROOT,
    )
    if not path.is_file():
        raise CombineError(f"no results.json for {domain}/{run_id} at {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise CombineError(f"{path} is not valid JSON: {exc}") from exc
    return RunInput(domain=domain, run_id=run_id, path=path, payload=payload)


def collect(domains: list[str], pinned: dict[str, str] | None = None) -> list[RunInput]:
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


def _requires_calibration_for_release(domain: str) -> bool:
    """Whether a RELEASE card for this domain needs a calibration marker.

    Config decides (`calibration.require_calibration_for_release`), defaulting to
    false by project decision. The run is still labelled `calibrated: false`
    either way — the label is what keeps an uncalibrated baseline honest, and it
    is not configurable.
    """

    try:
        from judge.config import load_judge_config

        return load_judge_config(domain).calibration.require_calibration_for_release
    except Exception:
        return False


def release_blockers(inputs: list[RunInput], platform_version: str = "") -> list[str]:
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
                f"{run.judge_name!r}, which is not release-eligible. Runs "
                "recorded before the test double was removed still carry it, so "
                "this is checked against the artifact, not the current CLI."
            )
        if run.pulse_mode in _STANDIN_PULSE:
            blockers.append(
                f"{run.domain}/{run.run_id} used --pulse {run.pulse_mode!r}, which "
                "does not reach the platform (HC-4)"
            )
        if not run.calibrated and _requires_calibration_for_release(run.domain):
            blockers.append(
                f"{run.domain}/{run.run_id} was scored by an uncalibrated judge "
                "(§10.2)"
            )
        if not run.comparison_is_default:
            blockers.append(
                f"{run.domain}/{run.run_id} relaxed the exact-match comparison "
                "policy, so it cannot be a baseline (OI-2 / OI-3)"
            )
        if not run.platform_version and not platform_version:
            blockers.append(
                f"{run.domain}/{run.run_id} carries no platform_version; the "
                "baseline is keyed on it (§11.1). Pass --platform-version to "
                "tag these runs at combine time."
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
    platform_version: str = "",
) -> CombineOutcome:
    """Write one scorecard across several domains' runs.

    `platform_version` tags runs that did not record one themselves. The baseline
    is keyed on it (§11.1), so without it there is nothing to key — and a run
    scored before `--platform-version` was passed is otherwise unusable as a
    baseline forever, which is a poor reason to re-spend two hours of platform
    time. Where a run DID record one, the recorded value wins and a conflicting
    override is refused; the provenance note says which way round it was.
    """

    if not inputs:
        raise CombineError("no runs to combine")

    supplied = (platform_version or "").strip()
    recorded = {r.platform_version for r in inputs if r.platform_version}
    if supplied and recorded and recorded != {supplied}:
        raise CombineError(
            f"--platform-version {supplied!r} contradicts what the runs recorded "
            f"({', '.join(sorted(recorded))}). The run's own value is the "
            f"evidence; drop the flag or pin different runs."
        )

    moment = now or datetime.now(timezone.utc)
    run_id = f"combined-{moment.strftime('%Y%m%dT%H%M%SZ')}"
    # The version comes from the domains actually combined, not from whichever
    # domain `release_root` happens to default to.
    target = out_dir or combined_report_dir(
        run_id, combined_release_version([r.domain for r in inputs])
    )

    blockers = release_blockers(inputs, supplied) if release else []
    mode = "RELEASE" if release and not blockers else "PREVIEW"

    versions = {r.platform_version for r in inputs if r.platform_version}
    # A value the runs recorded wins over one supplied now: the run is the
    # evidence. `supplied` only fills the gap where no run captured one.
    platform_version = versions.pop() if len(versions) == 1 else (supplied or "")
    tagged_at_combine = bool(supplied) and not versions and not recorded
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
        # One subset input makes the whole card a subset for quota purposes.
        partial_run=any(r.partial_run for r in inputs),
        comparison_policy=(inputs[0].comparison_policy or RunContext.comparison_policy),
        comparison_is_default=all(r.comparison_is_default for r in inputs),
        judge_temperature_enforced=all(r.temperature_enforced for r in inputs),
        judge_seed_enforced=all(r.seed_enforced for r in inputs),
        provenance_note=(
            "combined from "
            + ", ".join(f"{r.domain}/{r.run_id}" for r in inputs)
            # Said plainly on the artifact when the tag was asserted by the
            # operator rather than captured by the run. A baseline is permanent,
            # so how its key was obtained must be visible on it.
            + (
                f"; platform_version {platform_version!r} supplied at combine "
                "time — the runs did not record one"
                if tagged_at_combine
                else ""
            )
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
                        # Calibration state is deliberately NOT recorded here.
                        # The scorecard directory carries no trace of it, by
                        # project decision; `results_json` above points at the
                        # run, whose summary still holds `calibrated` for the
                        # release gate and for an audit that goes looking.
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
            f"pulse={run.pulse_mode or '?'}"
            + ("  calibrated" if run.calibrated else "")
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
        "--platform-version",
        default="",
        help="tag these runs with the platform version the baseline is keyed on "
        "(§11.1). Only needed for runs scored before --platform-version was "
        "passed to `judge`; a version the run recorded itself always wins.",
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
        help="write here instead of release/<version>/scorecard/<run_id>/.",
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
            inputs,
            release=args.release,
            out_dir=Path(args.out) if args.out else None,
            platform_version=args.platform_version,
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
