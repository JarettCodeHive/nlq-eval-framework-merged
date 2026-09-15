"""Evaluation runner: submit Q&A pairs to the platform, score, write the scorecard.

Deterministic exact-match (§HC-3) and the LLM-judge scores are reported SIDE BY
SIDE, never combined (§11.3). Output rows match §11.2 exactly, so a downstream
scorecard consumer can read the JSON without an adapter.

See `docs/judge_runbook.md` for the full flag surface and a live-run walkthrough.

  python -m judge.cli --input-csv pairs.csv                # live platform + LLM judge
  python -m judge.cli --pulse regressed                   # regression signal against fixtures
  python -m judge.cli --judge llm --pulse live \\
      --input-csv <pairs.csv> --domain crm                # real platform API (HC-4)
  python -m judge.cli --mode per_dimension                # 4 judge prompts, no halo effect

Writes judge/runs/<UTC-timestamp>/ — results.json + the §11 scorecard files.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

from judge.calibration import JudgeFingerprint, check_calibrated
from judge.checks import CheckStatus, check_null_handling, check_rephrase_groups
from judge.client import JudgeClient
from judge.config import (
    AzureSettings,
    FloodgateNarrativeSettings,
    FloodgateOIDCSettings,
    MissingCredentials,
    load_judge_config,
    load_llm_settings,
)
from judge.contracts import DIMENSIONS, JudgeRequest, JudgeVerdict
from judge.exact_match import (
    ComparisonPolicy,
    ExactMatchOutcome,
    ExactMatchResult,
    evaluate_answer,
)
from judge.input_contract import InputRow, load_input_csv
from judge.heuristic_judge import HeuristicJudge
from judge.sql_pulse import SQLPulse
from judge.openai_judge import OpenAIJudge
from judge.prompts import prompt_version
from scorecard.baseline import BaselineExists, establish_baseline, load_baseline
from scorecard.report import write_scorecard_md, write_scorecard_pdf
from scorecard.summary import (
    RunContext,
    build_summary_rows,
    write_question_results_csv,
    write_scorecard_summary_csv,
)

# Non-live Pulse sources (fixtures / offline stand-ins). A --release run against
# one of these still produces artifacts, but its baseline is marked provisional
# — only `--pulse live` goes through the real platform API (HC-4).
# Sources that are not the platform. A release run refuses these outright.
_STANDIN_PULSE = {"sql"}

MODULE_ROOT = Path(__file__).resolve().parent
RUNS_DIR = MODULE_ROOT / "runs"
# Measured against org 4104 on the crm_dataset_v2 full profile.
SECONDS_PER_QUESTION = 140.0


def _model_version(settings, cfg) -> str:
    """The string identifying this judge in verdicts, cache keys and manifests.

    It has to distinguish deployments and proxies, not just models: Azure routes
    on a deployment name, and the same Anthropic model through Floodgate is not
    an interchangeable measurement with the same model elsewhere.
    """
    if isinstance(settings, AzureSettings):
        return f"azure/{settings.deployment}"
    if isinstance(settings, (FloodgateOIDCSettings, FloodgateNarrativeSettings)):
        return f"floodgate/{cfg.model}"
    return cfg.model


def _judge_fingerprint(args: argparse.Namespace) -> JudgeFingerprint | None:
    """Identify the judge this run would use, without building it.

    Needed before the §10.2 gate: a calibration marker vouches for one model,
    one prompt revision and one scoring mode, so the gate has to know which
    judge is about to run. Credential loading and config merging are local and
    cheap — no network call happens here.
    """
    if args.judge != "llm":
        return None
    settings = load_llm_settings()
    cfg = load_judge_config(args.domain).model_copy(update={"mode": args.mode})
    return JudgeFingerprint(
        model_version=_model_version(settings, cfg),
        prompt_version=prompt_version(),
        mode=args.mode,
    )


def _resolve_policy(args: argparse.Namespace) -> ComparisonPolicy:
    """Build the exact-match comparison policy this run declares.

    Defaults settle OI-2 and OI-3 rather than leaving the scorer unusable while
    they are open — see `judge/exact_match.py`. Both are one flag to reverse.
    """
    if getattr(args, "normalize_numerics", False):
        print(
            "[judge] NOTE: --normalize-numerics is deprecated and now a no-op — "
            "value-based numeric comparison is the default (--numeric-form value). "
            "Pass --numeric-form string for the old byte-identical behaviour.",
            file=sys.stderr,
        )
    return ComparisonPolicy(
        numeric_form=args.numeric_form,
        require_labels=not args.ignore_entity_labels,
    )


def _make_llm_judge(
    settings,
    cfg,
    *,
    cache_dir: Path,
    prompt_log_path: Path | None,
    judge_run_id: str,
) -> JudgeClient:
    """Pick the transport for the detected provider. Everything downstream —
    caching, audit records, scoring modes — is identical either way."""
    if isinstance(settings, (FloodgateOIDCSettings, FloodgateNarrativeSettings)):
        # Imported here so the `anthropic` SDK is only required by runs that
        # actually go through Floodgate.
        from judge.floodgate_judge import FloodgateJudge

        return FloodgateJudge(
            settings,
            cfg,
            cache_dir=cache_dir,
            prompt_log_path=prompt_log_path,
            judge_run_id=judge_run_id,
        )
    return OpenAIJudge(
        settings,
        cfg,
        cache_dir=cache_dir,
        prompt_log_path=prompt_log_path,
        judge_run_id=judge_run_id,
    )


def _build_judge(
    name: str,
    mode: str,
    *,
    domain: str,
    prompt_log_path: Path | None,
    judge_run_id: str,
) -> tuple[JudgeClient, dict]:
    """Return (judge, provenance) — provenance goes into the run manifest."""
    if name == "heuristic":
        return HeuristicJudge(), {
            "provider": "heuristic",
            "model": "heuristic-test-double-v1",
        }
    if name == "llm":
        settings = load_llm_settings()
        cfg = load_judge_config(domain).model_copy(
            update={"mode": mode, "cache_enabled": True}
        )
        # Cache dir keyed on the model_version string so runs against different
        # providers/deployments don't share a cache line.
        model_version = _model_version(settings, cfg)
        cache_dir = MODULE_ROOT / ".judge_cache" / model_version.replace("/", "_")
        provenance = {
            **settings.redacted,
            "model": model_version,
            "temperature": cfg.temperature,
            # Anthropic has no seed; the Floodgate judge warns and the manifest
            # must not claim one was applied.
            "seed": None if model_version.startswith("floodgate/") else cfg.seed,
        }
        return (
            _make_llm_judge(
                settings,
                cfg,
                cache_dir=cache_dir,
                prompt_log_path=prompt_log_path,
                judge_run_id=judge_run_id,
            ),
            provenance,
        )
    raise ValueError(f"unknown judge: {name!r}")


def _write_run_manifest(out_dir: Path, run_id: str, ts_iso: str, args) -> None:
    """Everything needed to reproduce or audit this run, captured up front.

    Written before any network call so it exists even if the run dies.
    """
    import hashlib
    import platform
    import subprocess

    def _git(*a: str) -> str | None:
        try:
            return subprocess.run(
                ["git", *a], capture_output=True, text=True, timeout=5, check=True
            ).stdout.strip()
        except Exception:
            return None

    inputs = {}
    csv_path = getattr(args, "input_csv", None)
    if csv_path and Path(csv_path).is_file():
        raw = Path(csv_path).read_bytes()
        inputs["input_csv"] = {
            "path": str(Path(csv_path).resolve()),
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }

    manifest = {
        "run_id": run_id,
        "run_timestamp_iso": ts_iso,
        "argv": sys.argv[1:],
        "args": vars(args),
        "inputs": inputs,
        "git": {
            "commit": _git("rev-parse", "HEAD"),
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(_git("status", "--porcelain")),
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "cwd": str(Path.cwd()),
        },
    }
    (out_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str), encoding="utf-8"
    )


class PlatformError(RuntimeError):
    """The platform (Pulse) failed to answer one question.

    Recorded as an ERROR row — NOT scored, and NOT counted in the exact-match
    denominator. A run with many of these is an infra failure to re-run, not a
    low accuracy score.
    """


async def _collect_requests(
    pairs: list[dict],
    pulse: object,
    *,
    concurrency: int,
) -> list[tuple[dict, JudgeRequest, str | None]]:
    """Query the platform for every pair, concurrently, isolating failures.

    Returns (pair, JudgeRequest, platform_error) per pair — one slow or failing
    question never aborts the batch. `pulse.query` is sync, so it runs in a
    worker thread under a bounded semaphore.
    """
    limit = max(1, concurrency)
    if getattr(pulse, "mode", "").startswith("sql"):
        limit = 1  # SQLPulse shares one DuckDB connection — not thread-safe
    sem = asyncio.Semaphore(limit)
    total = len(pairs)
    progress = {"done": 0}
    progress_lock = asyncio.Lock()

    async def one(pair: dict) -> tuple[dict, JudgeRequest, str | None]:
        async with sem:
            try:
                resp = await asyncio.to_thread(pulse.query, pair["question_id"])
                answer, sql, err = resp.answer_text, resp.generated_sql, None
            except Exception as exc:  # noqa: BLE001 — isolate, record, continue
                answer, sql, err = "", None, f"{type(exc).__name__}: {exc}"
            async with progress_lock:
                progress["done"] += 1
                n = progress["done"]
                if total >= 25 and (n % 10 == 0 or n == total):
                    print(f"[judge] platform {n}/{total}", file=sys.stderr)
        req = JudgeRequest(
            question=pair["natural_language_question"],
            expected_answer=pair["expected_answer"],
            judge_reference=pair["judge_reference"],
            platform_answer=answer,
            generated_sql=sql,
            domain=pair["domain"],  # resolved in _run, never silently "crm"
            question_id=pair["question_id"],
        )
        return pair, req, err

    return await asyncio.gather(*(one(p) for p in pairs))


def _rows_from_input(rows: list[InputRow]) -> list[dict]:
    return [row.as_pair() for row in rows]


def _print_row(
    pair: dict,
    req: JudgeRequest,
    verdict: JudgeVerdict | Exception,
    em: ExactMatchOutcome,
) -> None:
    print(f"\n--- {pair['question_id']}  |  {pair['tier']}  ------------------")
    print(f"Q:  {req.question}")
    print(f"expected_answer : {req.expected_answer}")
    print(f"actual_answer   : {req.platform_answer[:200]}")
    print(f"exact_match     : {em.result.value.upper()}")
    if em.result is ExactMatchResult.FAIL:
        print(f"  why           : {em.detail}")
    if pair.get("null_handling") in ("fail", "unknown"):
        print(
            f"null_handling   : {pair['null_handling'].upper()} — "
            f"{pair.get('null_handling_detail')}"
        )
    if isinstance(verdict, PlatformError):
        print(f"PLATFORM ERROR: {verdict}  (not scored)")
        return
    if isinstance(verdict, Exception):
        print(f"JUDGE ERROR: {type(verdict).__name__}: {verdict}")
        return
    scores = "  ".join(f"{d[:8]:>8s}={getattr(verdict, d)}" for d in DIMENSIONS)
    tag = " [cached]" if verdict.cached else ""
    print(f"judge_scores    : {scores}  overall={verdict.overall_score:.2f}{tag}")
    print(f"judge_rationale : {verdict.rationale[:220]}")


def _summarise_null_handling(results: list) -> dict:
    """Roll up the §9.2 T3 check. Diagnostic, never part of a score."""
    counts: dict[str, int] = {}
    findings: list[str] = []
    for pair, _req, _v, _em in results:
        status = pair.get("null_handling") or CheckStatus.NOT_APPLICABLE.value
        counts[status] = counts.get(status, 0) + 1
        if status == CheckStatus.FAIL.value:
            findings.append(str(pair.get("question_id")))
    return {
        "counts": counts,
        "applicable": sum(
            n
            for status, n in counts.items()
            if status != CheckStatus.NOT_APPLICABLE.value
        ),
        "failed_question_ids": sorted(findings),
    }


def _report_checks(results: list, rephrase_findings: list) -> None:
    """Print the §9.2 / §9.5 diagnostics that are not part of either score."""
    null_failures = [
        pair
        for pair, _req, _v, _em in results
        if pair.get("null_handling") == CheckStatus.FAIL.value
    ]
    if null_failures:
        print(
            f"\n[judge] ⚠ NULL-handling findings ({len(null_failures)}) — §9.2 T3: "
            "an outer-join question answered with inner joins only, so unmatched "
            "rows are silently dropped:",
            file=sys.stderr,
        )
        for pair in null_failures[:10]:
            print(f"[judge]   {pair.get('question_id')}", file=sys.stderr)

    platform_findings = [f for f in rephrase_findings if f.is_platform_finding]
    dataset_findings = [f for f in rephrase_findings if f.is_dataset_finding]
    if platform_findings:
        print(
            f"\n[judge] ⚠ REPHRASE-GROUP findings ({len(platform_findings)}) — §9.5: "
            "variants of one question returned different values. This is a "
            "platform finding, not a dataset defect:",
            file=sys.stderr,
        )
        for f in platform_findings[:10]:
            print(f"[judge]   {f.group_id}: {f.detail}", file=sys.stderr)
    if dataset_findings:
        print(
            f"\n[judge] ⚠ REPHRASE-GROUP dataset defects ({len(dataset_findings)}) — "
            "variants of one group declare different expected_answers (§9.5):",
            file=sys.stderr,
        )
        for f in dataset_findings[:10]:
            print(f"[judge]   {f.group_id}: {f.detail}", file=sys.stderr)


def _row_dict(
    pair: dict,
    req: JudgeRequest,
    verdict: JudgeVerdict | Exception,
    em: ExactMatchOutcome,
) -> dict:
    """Row shape matches Execution Spec §11.2 (question-level drill-down)."""
    row: dict = {
        "question_id": pair["question_id"],
        "domain": pair["domain"],
        "tier": pair["tier"],
        "natural_language_question": req.question,
        "expected_answer": req.expected_answer,
        "actual_answer": req.platform_answer,
        "exact_match_result": em.result.value,  # §HC-3, §11.2
        "exact_match_detail": em.detail,  # which requirement went unmet
        "expected_answer_off_contract": em.off_contract,  # §9.3 shape violation
        "platform_generated_sql": req.generated_sql,  # §HC-5 — mandatory on failure
        "null_handling": pair.get("null_handling"),  # §9.2 T3 diagnostic
        "null_handling_detail": pair.get("null_handling_detail"),
        "rephrase_group_id": pair.get("rephrase_group_id"),  # §11.2 optional
    }
    if isinstance(verdict, JudgeVerdict):
        row["judge_scores"] = {d: getattr(verdict, d) for d in DIMENSIONS}
        row["judge_rationales"] = verdict.dimension_rationales
        row["judge_rationale"] = verdict.rationale
        row["judge_overall"] = verdict.overall_score
        row["prompt_version"] = verdict.prompt_version
        row["model_version"] = verdict.model_version
        row["cached"] = verdict.cached
    elif isinstance(verdict, PlatformError):
        row["platform_error"] = str(verdict)
    else:
        row["judge_error"] = f"{type(verdict).__name__}: {verdict}"
    return row


def _summarise(
    results: list[
        tuple[dict, JudgeRequest, JudgeVerdict | Exception, ExactMatchOutcome]
    ],
) -> dict:
    verdicts = [v for _, _, v, _ in results if isinstance(v, JudgeVerdict)]
    platform_errors = [v for _, _, v, _ in results if isinstance(v, PlatformError)]
    judge_errors = [
        v
        for _, _, v, _ in results
        if isinstance(v, Exception) and not isinstance(v, PlatformError)
    ]
    em = [outcome.result for _, _, _, outcome in results]
    off_contract = sum(1 for _, _, _, o in results if o.off_contract)
    _excluded = {ExactMatchResult.NOT_APPLICABLE, ExactMatchResult.ERROR}
    em_eligible = [e for e in em if e not in _excluded]
    per_dim = {
        d: statistics.mean(getattr(v, d) for v in verdicts) if verdicts else None
        for d in DIMENSIONS
    }
    return {
        "pairs_total": len(results),
        "judged": len(verdicts),
        "judge_errors": len(judge_errors),
        "platform_errors": len(platform_errors),
        # Deterministic side (§11.3): exact-match reported separately, NEVER blended.
        "exact_match": {
            "eligible": len(em_eligible),
            "pass": sum(1 for e in em_eligible if e == ExactMatchResult.PASS),
            "fail": sum(1 for e in em_eligible if e == ExactMatchResult.FAIL),
            # §14.2 condition 4 is about exclusions that quietly raise the
            # percentage. NOT_APPLICABLE is one, so it is named, not derived.
            "not_applicable": sum(
                1 for e in em if e == ExactMatchResult.NOT_APPLICABLE
            ),
            "platform_error": sum(1 for e in em if e == ExactMatchResult.ERROR),
            "expected_answer_off_contract": off_contract,
            "pass_pct": (
                sum(1 for e in em_eligible if e == ExactMatchResult.PASS)
                / len(em_eligible)
                * 100
                if em_eligible
                else None
            ),
        },
        # Judge side (§11.3): mean scores per dimension, never averaged with exact-match.
        "judge": {
            "mean_per_dimension": per_dim,
            "mean_overall": (
                statistics.mean(v.overall_score for v in verdicts) if verdicts else None
            ),
        },
    }


def _resolve_scorecard_mode(
    args: argparse.Namespace, *, calibrated: bool, calibration_reason: str = ""
) -> tuple[str, list[str]]:
    """Decide PREVIEW vs RELEASE. Returns (mode, blocking reasons).

    A RELEASE scorecard is the only kind that establishes or is compared to a
    baseline and that can certify a release (§10.2, §11.3). Everything else is a
    clearly-labelled PREVIEW that never touches the baseline.
    """
    if not getattr(args, "release", False):
        return "PREVIEW", []
    blockers: list[str] = []
    if args.judge != "llm":
        blockers.append(
            "a release run requires --judge llm (the heuristic test double never scores for real)"
        )
    if args.pulse in _STANDIN_PULSE:
        # HC-4: all evaluation goes through the platform. A baseline built from
        # anything else measures our own reference SQL, not the system under
        # evaluation — it would look like an accuracy number and be worthless.
        blockers.append(
            f"a release run requires --pulse live; {args.pulse!r} does not reach "
            "the platform (HC-4)"
        )
    if not calibrated:
        blockers.append(
            f"domain {args.domain!r} is not calibrated for this judge (§10.2)"
            + (f": {calibration_reason}" if calibration_reason else "")
        )
    if args.numeric_form != "value" or args.ignore_entity_labels:
        # A baseline is only comparable to later runs scored the same way, and a
        # relaxed comparison must never become the reference point silently.
        blockers.append(
            "a release run must use the declared default comparison "
            "(--numeric-form value, entity labels required); this run relaxes it"
        )
    if not args.platform_version:
        blockers.append("--platform-version is required for a release run (§11.1)")
    if not args.dataset_version:
        blockers.append("--dataset-version is required for a release run (§11.1)")
    return "RELEASE", blockers


def _write_run(
    run_id: str,
    judge_name: str,
    mode: str,
    pulse_mode: str,
    ctx: RunContext,
    results: list[
        tuple[dict, JudgeRequest, JudgeVerdict | Exception, ExactMatchOutcome]
    ],
    summary: dict,
) -> Path:
    out_dir = RUNS_DIR / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "run_timestamp_iso": ctx.run_timestamp_iso,
        "platform_version": ctx.platform_version,
        "dataset_version": ctx.dataset_version,
        "scorecard_mode": ctx.scorecard_mode,
        "judge": judge_name,
        "mode": mode,
        "pulse_mode": pulse_mode,
        "summary": summary,
        "rows": [_row_dict(pair, req, v, em) for pair, req, v, em in results],
    }
    (out_dir / "results.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    return out_dir


async def _run(args: argparse.Namespace) -> int:
    # §10.2 gate: uncalibrated scores never enter the scorecard. The gate
    # applies to LLM judges only — the test double is a heuristic, plainly
    # labelled, and never used for real scoring.
    #
    # The marker must vouch for THIS judge, not for the domain in the abstract:
    # a pass earned on one model, prompt revision or scoring mode says nothing
    # about another. `_judge_fingerprint` resolves that identity up front.
    try:
        fingerprint = _judge_fingerprint(args)
    except MissingCredentials as exc:
        print(f"[judge] {exc}", file=sys.stderr)
        return 2
    calibration = check_calibrated(args.domain, fingerprint)
    calibrated = calibration.calibrated
    if args.judge == "llm" and not calibrated and not args.allow_uncalibrated:
        if calibration.stale:
            remedy = (
                "Re-run calibration for this configuration: the marker cannot "
                "vouch for a judge it was not earned against."
            )
        else:
            remedy = (
                "Options: (1) run the calibration protocol against "
                "anchors/<domain>.json and record the pass with "
                "`python main.py calibrate --domain <domain>`; or (2) pass "
                "--allow-uncalibrated for a smoke test (scores are then labelled "
                "uncalibrated and must not feed a scorecard)."
            )
        print(
            f"[judge] REFUSING TO RUN: {calibration.reason} "
            "(§10.2 last line — uncalibrated scores never enter the scorecard).\n"
            f"{remedy}",
            file=sys.stderr,
        )
        return 3

    policy = _resolve_policy(args)
    scorecard_mode, blockers = _resolve_scorecard_mode(
        args, calibrated=calibrated, calibration_reason=calibration.reason
    )
    if blockers:
        print(
            "[judge] REFUSING RELEASE RUN — a release scorecard must be trustworthy:\n"
            + "\n".join(f"  - {b}" for b in blockers)
            + "\nDrop --release for a PREVIEW run, or resolve the above.",
            file=sys.stderr,
        )
        return 3

    now = datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%dT%H%M%SZ")
    run_timestamp_iso = now.isoformat()
    out_dir = RUNS_DIR / run_id
    # Created up front, not at write-time: a run that dies mid-way must keep
    # whatever it already fetched, and must leave a log saying what it was doing.
    out_dir.mkdir(parents=True, exist_ok=True)
    prompt_log_path = out_dir / "prompts_log.jsonl"

    from judge.run_log import RunLog

    run_log = RunLog(out_dir / "run_log.jsonl")
    run_log.event(
        "run.start",
        run_id=run_id,
        argv=sys.argv[1:],
        args=vars(args),
        cwd=str(Path.cwd()),
        python=sys.version.split()[0],
    )
    _write_run_manifest(out_dir, run_id, run_timestamp_iso, args)

    try:
        judge, provenance = _build_judge(
            args.judge,
            args.mode,
            domain=args.domain,
            prompt_log_path=prompt_log_path,
            judge_run_id=run_id,
        )
    except MissingCredentials as exc:
        print(f"[judge] {exc}", file=sys.stderr)
        return 2

    if not args.input_csv:
        # Both remaining sources need authored pairs. There is no demo set to
        # fall back to — a run without real pairs scores nothing meaningful.
        print(
            f"[judge] --pulse {args.pulse} requires --input-csv <Q&A pair CSV>.",
            file=sys.stderr,
        )
        return 2

    if args.pulse == "live" and not args.input_csv:
        # The bundled data/sample_pairs.json is a canned demo set, not the real
        # Q&A pairs — sending it to the live platform produces misleading FAILs.
        print(
            "[judge] --pulse live requires --input-csv <Q&A pair CSV>. "
            "The bundled sample pairs are a fixture demo only.",
            file=sys.stderr,
        )
        return 2

    # The run's --domain is the fallback, not a hardcoded "crm" — otherwise a
    # Sales pair set with no `domain` column is silently scored, cached and
    # reported as CRM.
    pairs = _rows_from_input(load_input_csv(args.input_csv, default_domain=args.domain))
    if args.limit:
        pairs = pairs[: args.limit]

    # Every pair carries an explicit domain from here on.
    undeclared = 0
    for pair in pairs:
        if not (pair.get("domain") or "").strip():
            pair["domain"] = args.domain
            undeclared += 1
    if undeclared:
        print(
            f"[judge] NOTE: {undeclared}/{len(pairs)} pairs declared no domain — "
            f"assigning --domain={args.domain!r}.",
            file=sys.stderr,
        )

    # Pulse source selection:
    #   sql  -> executes each pair's reference_sql in DuckDB (§14.2 ground-truth
    #           verification; PREVIEW only, never scores the platform)
    #   live -> the real platform API (HC-4) — the only release-eligible source
    if args.pulse == "sql":
        if not args.input_csv:
            print("[judge] --pulse sql requires --input-csv", file=sys.stderr)
            return 2
        if not args.pulse_data:
            print(
                "[judge] --pulse sql requires --pulse-data <dir of CSVs>",
                file=sys.stderr,
            )
            return 2
        pulse = SQLPulse(pairs, Path(args.pulse_data))
        pulse_label = (
            f"sql[{Path(args.pulse_data).name}] (offline reference_sql execution)"
        )
    elif args.pulse == "live":
        # HC-4 — submit every question through the real platform API.
        from judge.pulse_client import (
            MissingPulseCredentials,
            PulseClient,
            check_token_headroom,
            load_pulse_settings,
        )

        try:
            pulse_settings = load_pulse_settings()
        except MissingPulseCredentials as exc:
            print(f"[judge] {exc}", file=sys.stderr)
            return 2
        # A Pulse token lives ~1 hour and a question costs ~2 minutes. Say so
        # before spending the run, not after it 401s halfway through.
        est_s = (len(pairs) / max(1, args.pulse_concurrency)) * SECONDS_PER_QUESTION
        warning = check_token_headroom(pulse_settings, est_s)
        if warning:
            print(f"[judge] ⚠ {warning}", file=sys.stderr)
            run_log.event(
                "pulse.token_warning", warning=warning, est_run_s=round(est_s)
            )
            if not args.ignore_token_expiry:
                print(
                    "[judge] Refusing to start. Pass --ignore-token-expiry to run "
                    "anyway (partial results are still logged).",
                    file=sys.stderr,
                )
                return 2

        pulse = PulseClient(
            pulse_settings,
            pairs,
            raw_dir=out_dir / "pulse_raw",
            run_log=run_log,
        )
        pulse_label = f"live (org_id={pulse_settings.org_id})"
        run_log.event("pulse.settings", **pulse_settings.redacted)
    else:  # pragma: no cover — argparse constrains --pulse to {sql, live}
        print(f"[judge] unknown --pulse source {args.pulse!r}", file=sys.stderr)
        return 2

    if args.judge == "heuristic" and args.mode == "per_dimension":
        print(
            "[judge] NOTE: --mode per_dimension has no effect with --judge "
            "heuristic — the test double never renders a prompt. Use --judge llm.",
            file=sys.stderr,
        )

    provisional_pulse = args.pulse in _STANDIN_PULSE
    calibration_state = "CALIBRATED" if calibrated else "UNCALIBRATED"
    print(
        f"[judge] judge={args.judge}  mode={args.mode}  "
        f"pulse={pulse_label}  pairs={len(pairs)}  "
        f"domain={args.domain} [{calibration_state}]  scorecard={scorecard_mode}"
    )
    print(f"[judge] provider provenance: {json.dumps(provenance)}")

    try:
        collected = await _collect_requests(
            pairs, pulse, concurrency=args.pulse_concurrency
        )
    finally:
        if hasattr(pulse, "close"):
            pulse.close()

    platform_errors = {pair["question_id"]: err for pair, _, err in collected if err}
    run_log.event(
        "pulse.phase_complete",
        collected=len(collected),
        answered=len(collected) - len(platform_errors),
        errors=len(platform_errors),
        error_ids=sorted(platform_errors),
    )
    if platform_errors:
        print(
            f"[judge] ⚠ {len(platform_errors)}/{len(collected)} questions got no "
            "platform answer — recorded as ERROR, excluded from exact-match, not "
            "scored. Re-run if this is more than a stray flake.",
            file=sys.stderr,
        )
        for qid, err in list(platform_errors.items())[:5]:
            print(f"[judge]   {qid}: {err}", file=sys.stderr)

    # Only score the questions the platform actually answered.
    to_judge = [(pair, req) for pair, req, err in collected if not err]
    run_log.event(
        "judge.phase_start",
        to_judge=len(to_judge),
        concurrency=args.concurrency,
        mode=args.mode,
    )
    verdicts = await judge.judge_many(
        [req for _, req in to_judge], concurrency=args.concurrency
    )
    judge_errors = sum(1 for v in verdicts if isinstance(v, Exception))
    run_log.event(
        "judge.phase_complete", verdicts=len(verdicts), judge_errors=judge_errors
    )
    await judge.aclose()
    verdict_by_qid = {
        pair["question_id"]: v for (pair, _), v in zip(to_judge, verdicts, strict=True)
    }

    # Deterministic exact-match runs alongside the judge, not through it.
    results: list[
        tuple[dict, JudgeRequest, JudgeVerdict | Exception, ExactMatchOutcome]
    ] = []
    for pair, req, err in collected:
        if err:
            outcome = ExactMatchOutcome(ExactMatchResult.ERROR, str(err))
            verdict: JudgeVerdict | Exception = PlatformError(err)
        else:
            verdict = verdict_by_qid[pair["question_id"]]
            outcome = evaluate_answer(
                req.expected_answer, req.platform_answer, policy=policy
            )
        # §9.2's T3 "explicit NULL-handling check" — a diagnostic reported beside
        # the scores, never folded into them (§11.3, §22).
        null_check = check_null_handling(
            tier=pair.get("tier"),
            reference_sql=pair.get("reference_sql"),
            platform_sql=req.generated_sql,
        )
        pair["null_handling"] = null_check.status.value
        pair["null_handling_detail"] = null_check.detail
        results.append((pair, req, verdict, outcome))

    # §9.5 — every variant of a rephrase group must resolve the same values.
    rephrase_findings = check_rephrase_groups(
        [(pair, req.expected_answer, outcome) for pair, req, _, outcome in results]
    )

    if len(results) <= 30:
        for pair, req, v, em in results:
            _print_row(pair, req, v, em)

    summary = _summarise(results)
    summary["calibrated"] = calibrated
    summary["calibration"] = {
        "state": "calibrated" if calibrated else "uncalibrated",
        "reason": calibration.reason,
        "judge_fingerprint": fingerprint.as_dict() if fingerprint else None,
        "marker_fingerprint": (
            calibration.marker_fingerprint.as_dict()
            if calibration.marker_fingerprint
            else None
        ),
    }
    summary["provider"] = provenance
    summary["comparison_policy"] = policy.label
    summary["comparison_is_default"] = policy.is_certified_default
    summary["null_handling"] = _summarise_null_handling(results)
    summary["rephrase_groups"] = {
        "groups": len(rephrase_findings),
        "platform_findings": [
            {
                "group_id": f.group_id,
                "question_ids": f.question_ids,
                "detail": f.detail,
            }
            for f in rephrase_findings
            if f.is_platform_finding
        ],
        "dataset_findings": [
            {
                "group_id": f.group_id,
                "question_ids": f.question_ids,
                "detail": f.detail,
            }
            for f in rephrase_findings
            if f.is_dataset_finding
        ],
    }
    _report_checks(results, rephrase_findings)
    if not policy.is_certified_default:
        print(
            f"[judge] NOTE: exact-match ran under a NON-DEFAULT comparison "
            f"[{policy.label}]. The scorecard is marked accordingly and this run "
            "cannot establish a baseline.",
            file=sys.stderr,
        )
    # Read off the VERDICTS, not the judge — a cache-served run never talks to
    # the provider, so the judge object would wrongly report "enforced".
    scored = [v for _, _, v, _ in results if isinstance(v, JudgeVerdict)]
    temp_enforced = all(v.temperature_enforced for v in scored) if scored else True
    seed_enforced = all(v.seed_enforced for v in scored) if scored else True
    summary["judge_temperature_enforced"] = temp_enforced
    summary["judge_seed_enforced"] = seed_enforced
    if not seed_enforced:
        # The manifest must not advertise a determinism control that never went
        # on the wire; keep what was ASKED for, separately from what applied.
        provenance["seed_requested"] = provenance.get("seed")
        provenance["seed"] = None
    if not temp_enforced:
        print(
            "[judge] ⚠ the judge model rejected temperature=0 — this run ran on "
            "the model default. Recorded as judge_temperature_enforced=false.",
            file=sys.stderr,
        )
    if not seed_enforced:
        print(
            "[judge] ⚠ the configured seed never reached the provider — recorded "
            "as judge_seed_enforced=false and cleared from the provenance block "
            "(§10.1 'where supported').",
            file=sys.stderr,
        )

    ctx = RunContext(
        run_id=run_id,
        run_timestamp_iso=run_timestamp_iso,
        platform_version=args.platform_version,
        dataset_version=args.dataset_version,
        scorecard_mode=scorecard_mode,
        calibrated=calibrated,
        comparison_policy=policy.label,
        comparison_is_default=policy.is_certified_default,
        judge_temperature_enforced=temp_enforced,
        judge_seed_enforced=seed_enforced,
        rephrase_findings=[
            f"{f.group_id}: {f.detail}"
            for f in rephrase_findings
            if f.is_platform_finding or f.is_dataset_finding
        ],
    )
    out_dir = _write_run(
        run_id, args.judge, args.mode, args.pulse, ctx, results, summary
    )

    # A PREVIEW run still shows a delta if a baseline happens to exist — it just
    # never establishes or updates one, and its flag is advisory.
    baseline = load_baseline(args.platform_version) if args.platform_version else None

    regression_detected = _finalise_scorecard(
        out_dir, results, ctx, baseline=baseline, provisional_pulse=provisional_pulse
    )

    print("\n=== SUMMARY =========================================")
    print(json.dumps(summary, indent=2))
    print(f"[judge] written: {out_dir / 'results.json'}")
    if prompt_log_path.is_file() and prompt_log_path.stat().st_size > 0:
        print(f"[judge] full prompt/response log: {prompt_log_path}")
    raw_dir = out_dir / "pulse_raw"
    if raw_dir.is_dir():
        n = len(list(raw_dir.glob("*.json")))
        print(f"[judge] raw platform responses: {raw_dir} ({n} file(s))")
    if run_log.path:
        print(f"[judge] run event log: {run_log.path}")

    run_log.event(
        "run.complete",
        summary=summary,
        exit_reason="ok" if not summary["judge_errors"] else "judge_errors",
    )

    if args.judge == "heuristic":
        print(
            "\n[judge] NOTE: the heuristic judge is a test double — scores are NOT "
            "semantic. Use --judge llm for real evaluation."
        )
    if not summary["calibrated"] and args.judge == "llm":
        print(
            "\n[judge] NOTE: judge is uncalibrated (§10.2). These scores are "
            "diagnostic only and are labelled `calibrated=false` in the summary. "
            "They must NOT be fed to a scorecard until calibration passes."
        )
    if hasattr(judge, "cache_stats"):
        stats = judge.cache_stats()
        if stats.get("enabled"):
            print(f"[judge] cache: hits={stats['hits']} misses={stats['misses']}")
    if summary["judge_errors"]:
        return 1
    if regression_detected:
        return 4  # release run with a domain-level regression flag
    return 0


def _finalise_scorecard(
    out_dir: Path,
    results: list[
        tuple[dict, JudgeRequest, JudgeVerdict | Exception, ExactMatchOutcome]
    ],
    ctx: RunContext,
    *,
    baseline: dict | None,
    provisional_pulse: bool,
) -> bool:
    """Write the §11 scorecard artifacts and run the baseline/regression logic.

    Returns True iff this is a RELEASE run and at least one domain tripped the
    regression flag.
    """
    summary_csv = write_scorecard_summary_csv(out_dir, results, ctx, baseline=baseline)
    question_csv = write_question_results_csv(out_dir, results, ctx)
    md_path = write_scorecard_md(out_dir, results, ctx, baseline=baseline)
    try:
        pdf_path: Path | None = write_scorecard_pdf(
            out_dir, results, ctx, baseline=baseline
        )
    except ImportError as exc:
        # §11.3 requires the PDF — log loudly so a release can't pass silently.
        print(f"[judge] WARNING: PDF scorecard skipped — {exc}", file=sys.stderr)
        pdf_path = None

    print(f"[judge] scorecard summary : {summary_csv}")
    print(f"[judge] question results  : {question_csv}")
    print(f"[judge] scorecard (md)    : {md_path}")
    if pdf_path is not None:
        print(f"[judge] scorecard (pdf)   : {pdf_path}")

    _, comparisons = build_summary_rows(results, ctx, baseline=baseline)

    if ctx.scorecard_mode != "RELEASE":
        if baseline is not None:
            for domain, cmp in sorted(comparisons.items()):
                if cmp.has_baseline:
                    print(
                        f"[judge] preview delta  domain={domain}  "
                        f"current={cmp.current_exact_match_pct}  "
                        f"baseline={cmp.baseline_exact_match_pct}  "
                        f"delta={cmp.delta_pct}"
                        + ("  [would flag]" if cmp.regression_flag else "")
                    )
        print(
            "[judge] PREVIEW scorecard — not an official run. No baseline was "
            "established or updated (§11.3)."
        )
        return False

    if baseline is None:
        per_domain = {
            d: c.current_exact_match_pct
            for d, c in comparisons.items()
            if c.current_exact_match_pct is not None
        }
        try:
            path = establish_baseline(
                ctx.platform_version,
                per_domain,
                run_id=ctx.run_id,
                run_timestamp_iso=ctx.run_timestamp_iso,
                dataset_version=ctx.dataset_version,
                provisional=provisional_pulse,
            )
        except BaselineExists as exc:  # racy re-check
            print(f"[judge] {exc}", file=sys.stderr)
            return False
        print(
            f"[judge] BASELINE ESTABLISHED for platform_version="
            f"{ctx.platform_version!r} → {path}"
            + ("  (provisional)" if provisional_pulse else "")
        )
        return False

    flagged = sorted(d for d, c in comparisons.items() if c.regression_flag)
    for domain, cmp in sorted(comparisons.items()):
        if cmp.has_baseline:
            tag = "  ⚠ REGRESSION" if cmp.regression_flag else ""
            print(
                f"[judge] domain={domain}  exact_match={cmp.current_exact_match_pct}  "
                f"baseline={cmp.baseline_exact_match_pct}  delta={cmp.delta_pct}{tag}"
            )
    if flagged:
        print(
            f"[judge] ⚠ REGRESSION FLAG: {', '.join(flagged)} dropped ≥ 5 pp vs "
            f"baseline (§11.3).",
            file=sys.stderr,
        )
    return bool(flagged)


async def _calibrate(domain: str, concurrency: int = 4) -> int:
    """Run the §10.2 calibration protocol against judge/anchors/<domain>.json.

    - Scores every anchor with the live LLM judge (combined mode, cache on).
    - Compares each dimension against the human anchor score.
    - Writes .calibration/<domain>.passed.json iff every dimension has
      ≥90% agreement within ±1 (per calibration.ACCEPTANCE_AGREEMENT_PCT)
      and ≥10 anchors are present.
    """
    from judge.calibration import (
        AnchorSetTooWeak,
        JudgeFingerprint,
        anchor_strength,
        assert_anchor_set_usable,
        evaluate,
        load_anchors,
        record_passed,
    )

    anchors = load_anchors(domain)
    # §10.2 wants anchors "spanning the score range — not 10 easy passes".
    # Check that BEFORE spending a provider budget on a set that cannot certify
    # anything, and show exactly which dimension is undiscriminating.
    try:
        assert_anchor_set_usable(domain, anchors)
    except (AnchorSetTooWeak, ValueError) as exc:
        print(f"[calibrate] REFUSING: {exc}", file=sys.stderr)
        for dim, strength in anchor_strength(anchors).items():
            mark = "ok  " if strength.passes else "WEAK"
            print(
                f"[calibrate]   {mark} {dim:22s} human scores="
                f"{list(strength.distinct_scores)}"
                + (
                    f"  constant-{'/'.join(str(k) for k in strength.passing_constants)}"
                    "-would-pass"
                    if strength.passing_constants
                    else ""
                ),
                file=sys.stderr,
            )
        return 2

    settings = load_llm_settings()
    # per_dimension: 4 scoped prompts per anchor, prevents the halo effect where
    # a low score on one dimension pulls the others down (§10.2 note).
    mode = "per_dimension"
    cfg = load_judge_config(domain).model_copy(
        update={"mode": mode, "cache_enabled": True}
    )
    model_version = _model_version(settings, cfg)
    # The marker records this, and a scoring run whose fingerprint differs is
    # treated as uncalibrated — a pass on one model/prompt/mode does not vouch
    # for another.
    fingerprint = JudgeFingerprint(
        model_version=model_version,
        prompt_version=prompt_version(),
        mode=mode,
    )
    cache_dir = MODULE_ROOT / ".judge_cache" / model_version.replace("/", "_")
    judge_run_id = (
        f"calibration-{domain}-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )
    calibration_log = RUNS_DIR / judge_run_id / "prompts_log.jsonl"
    judge = _make_llm_judge(
        settings,
        cfg,
        cache_dir=cache_dir,
        prompt_log_path=calibration_log,
        judge_run_id=judge_run_id,
    )

    requests = [
        JudgeRequest(
            question=a["natural_language_question"],
            expected_answer=a["expected_answer"],
            judge_reference=a["judge_reference"],
            platform_answer=a["platform_answer"],
            generated_sql=a.get("generated_sql"),
            domain=domain,
            question_id=a["question_id"],
        )
        for a in anchors
    ]
    print(f"[calibrate] scoring {len(anchors)} anchors for domain={domain!r} ...")
    verdicts = await judge.judge_many(requests, concurrency=concurrency)
    await judge.aclose()

    failures = [
        (a["question_id"], v)
        for a, v in zip(anchors, verdicts)
        if isinstance(v, Exception)
    ]
    if failures:
        for qid, exc in failures:
            print(
                f"[calibrate] anchor {qid!r} judge error: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
        return 2

    verdicts_by_qid = {
        a["question_id"]: v for a, v in zip(anchors, verdicts, strict=True)
    }
    result = evaluate(domain, verdicts_by_qid, anchors)

    print(f"[calibrate] domain={domain}  anchors={result.anchor_count}")
    for dim, dim_result in result.per_dimension.items():
        tag = "PASS" if dim_result.passes else "FAIL"
        print(
            f"  {tag}  {dim:22s}  within ±1: {dim_result.within_pm1_pct:5.1f}%  "
            f"directional flips: {dim_result.directional_flip_count}"
        )

    if result.passed:
        marker = record_passed(domain, result, fingerprint=fingerprint)
        print(f"[calibrate] PASSED — wrote {marker}")
        print(
            f"[calibrate] --judge llm now allowed against domain={domain!r} for "
            f"model={fingerprint.model_version} prompt={fingerprint.prompt_version} "
            f"mode={fingerprint.mode}. Changing any of the three re-opens the gate."
        )
        return 0
    print(
        f"[calibrate] FAILED — no marker written. --judge llm will refuse to run "
        f"against domain={domain!r} until agreement passes.",
        file=sys.stderr,
    )
    return 1


def run_calibrate_from_domain(domain: str, concurrency: int = 4) -> int:
    _trust_os_ca_store()
    return asyncio.run(_calibrate(domain, concurrency=concurrency))


def build_argparser(prog: str | None = None) -> argparse.ArgumentParser:
    """Judge argparser, callable from standalone `main()` or from main.py's `score` command."""
    ap = argparse.ArgumentParser(prog=prog, description="judge end-to-end demo runner")
    ap.add_argument(
        "--judge",
        choices=["llm", "heuristic"],
        default="llm",
        help="llm = the real LLM judge (default). heuristic = deterministic "
        "test double for CI/dev — string overlap, NOT semantic scoring. Never "
        "release-eligible.",
    )
    ap.add_argument(
        "--mode",
        choices=["combined", "per_dimension"],
        default="combined",
        help="combined = 1 prompt for all 4 dims. per_dimension = 4 prompts, no halo effect.",
    )
    ap.add_argument(
        "--pulse",
        choices=["sql", "live"],
        default="live",
        help="Answer source. live = the real platform API (HC-4) — the default, "
        "and the ONLY source a release run accepts; needs the PULSE_* creds in "
        "judge/.env and --input-csv. sql = executes each pair's reference_sql in "
        "DuckDB (§14.2 ground-truth verification of pairs against the dataset; "
        "requires --input-csv AND --pulse-data). sql never scores the platform "
        "and its runs are PREVIEW-only.",
    )
    ap.add_argument(
        "--pulse-data",
        default="",
        help="Directory of CSV tables for --pulse sql. Every *.csv is loaded as a table "
        "named by its basename. Ignored for other --pulse modes.",
    )
    ap.add_argument(
        "--input-csv",
        default="",
        help="optional CSV input contract with question_id, domain, tier, nlq, expected_answer, judge_reference, reference_sql",
    )
    ap.add_argument(
        "--domain", default="crm", help="Q&A domain (used for calibration gate)"
    )
    ap.add_argument(
        "--allow-uncalibrated",
        action="store_true",
        help="§10.2 override: run the LLM judge without a calibration marker. "
        "Scores are labelled uncalibrated and MUST NOT feed a scorecard.",
    )
    ap.add_argument(
        "--release",
        action="store_true",
        help="Produce an official RELEASE scorecard (§11.3): establishes the "
        "baseline on first run for --platform-version, compares against it "
        "afterward, and flags any domain that drops ≥5 pp. Requires --judge llm, "
        "a calibration marker, --platform-version and --dataset-version. Without "
        "this flag every run is a clearly-labelled PREVIEW that never touches "
        "the baseline.",
    )
    ap.add_argument(
        "--platform-version",
        default="",
        help="Version tag for the platform under evaluation (§11.1). Keys the "
        "immutable baseline store.",
    )
    ap.add_argument(
        "--dataset-version",
        default="",
        help="Version tag for the Q&A dataset used this run (§11.1).",
    )
    ap.add_argument(
        "--numeric-form",
        choices=["value", "string"],
        default="value",
        help="How a numeric answer is compared (OI-2). value = identical VALUE, "
        "so '28,731' == '28731' == '28731.00' while '4,182,000' still fails "
        "'4,182,650.00' — the default, on the reading that §HC-3 protects "
        "rendering and fails only numeric variance. string = byte-identical "
        "rendering, the pre-OI-2 reading. A release run requires the default.",
    )
    ap.add_argument(
        "--ignore-entity-labels",
        action="store_true",
        help="Compare only the numerics in expected_answer, not the entity or "
        "category labels beside them. Diagnostic only — it re-admits the "
        "false pass where every right number is attached to the wrong entity, "
        "so a release run refuses it.",
    )
    ap.add_argument(
        "--normalize-numerics",
        action="store_true",
        help=argparse.SUPPRESS,  # deprecated: value comparison is now the default
    )
    ap.add_argument(
        "--limit", type=int, default=0, help="score only first N pairs (0 = all)"
    )
    ap.add_argument(
        "--concurrency", type=int, default=4, help="in-flight judge (LLM) calls"
    )
    ap.add_argument(
        "--pulse-concurrency",
        type=int,
        default=4,
        help="in-flight platform (Pulse) calls. Live Pulse answers take ~30s "
        "each; raise this for a big run, within the API's rate limit. Forced to "
        "1 for --pulse sql.",
    )
    ap.add_argument(
        "--ignore-token-expiry",
        action="store_true",
        help="Start a --pulse live run even when PULSE_AUTH_TOKEN will expire "
        "before it finishes. Off by default: a token that dies mid-run turns the "
        "remaining questions into 401s and wastes the whole run.",
    )
    return ap


def _trust_os_ca_store() -> None:
    """Corp machines TLS-inspect with a private root CA the OS store knows but
    certifi doesn't. Do this once, before any HTTP client (judge or Pulse) is
    built, so both the LLM gateway and the platform API verify cleanly.
    """
    try:
        import truststore

        truststore.inject_into_ssl()
    except ImportError:
        pass


def run_from_args(args: argparse.Namespace) -> int:
    """Run the judge with a pre-parsed namespace. Returns the process exit code.

    Same contract as `main()` minus the sys.exit — lets main.py's `score` command
    invoke the same runner and translate the return code into its own error path.
    """
    _trust_os_ca_store()
    return asyncio.run(_run(args))


def main() -> None:
    args = build_argparser().parse_args()
    sys.exit(run_from_args(args))


if __name__ == "__main__":
    main()
