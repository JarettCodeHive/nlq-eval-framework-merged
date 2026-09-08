"""Evaluation runner: submit Q&A pairs to the platform, score, write the scorecard.

Deterministic exact-match (§HC-3) and the LLM-judge scores are reported SIDE BY
SIDE, never combined (§11.3). Output rows match §11.2 exactly, so a downstream
scorecard consumer can read the JSON without an adapter.

See `docs/judge_runbook.md` for the full flag surface and a live-run walkthrough.

  python -m judge.cli                                     # mock judge + good fixtures (CI/dev)
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

from judge.calibration import is_calibrated
from judge.client import JudgeClient
from judge.config import MissingCredentials, load_judge_config, load_llm_settings
from judge.contracts import DIMENSIONS, JudgeRequest, JudgeVerdict
from judge.exact_match import ExactMatchResult, NumericNormalization, exact_match
from judge.input_contract import InputRow, load_input_csv
from judge.mock_judge import MockJudge
from judge.mock_pulse import EchoPulse, MockPulse, SQLPulse, load_pairs
from judge.openai_judge import OpenAIJudge
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
_STANDIN_PULSE = {"good", "regressed", "bad", "echo", "sql"}

MODULE_ROOT = Path(__file__).resolve().parent
RUNS_DIR = MODULE_ROOT / "runs"


def _build_judge(
    name: str,
    mode: str,
    *,
    domain: str,
    prompt_log_path: Path | None,
    judge_run_id: str,
) -> tuple[JudgeClient, dict]:
    """Return (judge, provenance) — provenance goes into the run manifest."""
    if name == "mock":
        return MockJudge(), {"provider": "mock", "model": "mock-judge-v1"}
    if name == "llm":
        settings = load_llm_settings()
        cfg = load_judge_config(domain).model_copy(
            update={"mode": mode, "cache_enabled": True}
        )
        # Cache dir keyed on the model_version string so runs against different
        # providers/deployments don't share a cache line.
        model_version = settings.model
        cache_dir = MODULE_ROOT / ".judge_cache" / model_version.replace("/", "_")
        provenance = {
            **settings.redacted,
            "model": model_version,
            "temperature": cfg.temperature,
            "seed": cfg.seed,
        }
        return (
            OpenAIJudge(
                settings,
                cfg,
                cache_dir=cache_dir,
                prompt_log_path=prompt_log_path,
                judge_run_id=judge_run_id,
            ),
            provenance,
        )
    raise ValueError(f"unknown judge: {name!r}")


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
            domain=pair.get("domain", "crm"),
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
    em_result: ExactMatchResult,
) -> None:
    print(f"\n--- {pair['question_id']}  |  {pair['tier']}  ------------------")
    print(f"Q:  {req.question}")
    print(f"expected_answer : {req.expected_answer}")
    print(f"actual_answer   : {req.platform_answer[:200]}")
    print(f"exact_match     : {em_result.value.upper()}")
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


def _row_dict(
    pair: dict,
    req: JudgeRequest,
    verdict: JudgeVerdict | Exception,
    em_result: ExactMatchResult,
) -> dict:
    """Row shape matches Execution Spec §11.2 (question-level drill-down)."""
    row: dict = {
        "question_id": pair["question_id"],
        "domain": pair.get("domain", req.domain),
        "tier": pair["tier"],
        "natural_language_question": req.question,
        "expected_answer": req.expected_answer,
        "actual_answer": req.platform_answer,
        "exact_match_result": em_result.value,  # §HC-3, §11.2
        "platform_generated_sql": req.generated_sql,  # §HC-5 — mandatory on failure
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
        tuple[dict, JudgeRequest, JudgeVerdict | Exception, ExactMatchResult]
    ],
) -> dict:
    verdicts = [v for _, _, v, _ in results if isinstance(v, JudgeVerdict)]
    platform_errors = [v for _, _, v, _ in results if isinstance(v, PlatformError)]
    judge_errors = [
        v
        for _, _, v, _ in results
        if isinstance(v, Exception) and not isinstance(v, PlatformError)
    ]
    em = [e for _, _, _, e in results]
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
            "not_applicable": sum(
                1 for e in em if e == ExactMatchResult.NOT_APPLICABLE
            ),
            "platform_error": sum(1 for e in em if e == ExactMatchResult.ERROR),
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
    args: argparse.Namespace, *, calibrated: bool
) -> tuple[str, list[str]]:
    """Decide PREVIEW vs RELEASE. Returns (mode, blocking reasons).

    A RELEASE scorecard is the only kind that establishes or is compared to a
    baseline and that can certify a release (§10.3, §11.3). Everything else is a
    clearly-labelled PREVIEW that never touches the baseline.
    """
    if not getattr(args, "release", False):
        return "PREVIEW", []
    blockers: list[str] = []
    if args.judge != "llm":
        blockers.append(
            "a release run requires --judge llm (the mock judge never scores for real)"
        )
    if not calibrated:
        blockers.append(f"domain {args.domain!r} has no calibration marker (§10.3)")
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
        tuple[dict, JudgeRequest, JudgeVerdict | Exception, ExactMatchResult]
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
    # §10.3 gate: uncalibrated scores never enter the scorecard. The gate
    # applies to LLM judges only — the mock judge is a heuristic, plainly
    # labelled, and never used for real scoring.
    if (
        args.judge == "llm"
        and not is_calibrated(args.domain)
        and not args.allow_uncalibrated
    ):
        print(
            f"[judge] REFUSING TO RUN: no calibration passed for domain={args.domain!r} "
            "(§10.3 last line — uncalibrated scores never enter the scorecard).\n"
            "Options: (1) run the calibration protocol against anchors/<domain>.json and "
            "record the pass with calibration.record_passed(); or (2) pass "
            "--allow-uncalibrated for a smoke test (scores will be labelled uncalibrated).",
            file=sys.stderr,
        )
        return 3

    calibrated = is_calibrated(args.domain)
    scorecard_mode, blockers = _resolve_scorecard_mode(args, calibrated=calibrated)
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
    prompt_log_path = out_dir / "prompts_log.jsonl"

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

    if args.pulse == "live" and not args.input_csv:
        # The bundled data/sample_pairs.json is a canned demo set, not the real
        # Q&A pairs — sending it to the live platform produces misleading FAILs.
        print(
            "[judge] --pulse live requires --input-csv <Q&A pair CSV>. "
            "The bundled sample pairs are a fixture demo only.",
            file=sys.stderr,
        )
        return 2

    if args.input_csv:
        pairs = _rows_from_input(load_input_csv(args.input_csv))
    else:
        pairs = load_pairs()
    if args.limit:
        pairs = pairs[: args.limit]

    # Pulse source selection:
    #   good/regressed/bad -> canned fixtures keyed by question_id (CI/dev)
    #   echo / sql         -> offline stand-ins over --input-csv pairs
    #   live               -> the real platform API (HC-4)
    if args.pulse == "echo":
        if not args.input_csv:
            print(
                "[judge] --pulse echo requires --input-csv (echo replays each pair's expected_answer)",
                file=sys.stderr,
            )
            return 2
        pulse = EchoPulse(pairs)
        pulse_label = "echo (offline perfect-Pulse stand-in)"
    elif args.pulse == "sql":
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
            load_pulse_settings,
        )

        try:
            pulse_settings = load_pulse_settings()
        except MissingPulseCredentials as exc:
            print(f"[judge] {exc}", file=sys.stderr)
            return 2
        pulse = PulseClient(pulse_settings, pairs)
        pulse_label = f"live (org_id={pulse_settings.org_id})"
    else:
        pulse = MockPulse(mode=args.pulse)
        pulse_label = f"{args.pulse} (canned fixture)"

    provisional_pulse = args.pulse in _STANDIN_PULSE
    calibration_state = "CALIBRATED" if calibrated else "UNCALIBRATED"
    print(
        f"[judge] judge={args.judge}  mode={args.mode}  "
        f"pulse={pulse_label}  pairs={len(pairs)}  "
        f"domain={args.domain} [{calibration_state}]  scorecard={scorecard_mode}"
    )
    if scorecard_mode == "RELEASE" and provisional_pulse:
        print(
            "[judge] NOTE: release run against a non-live Pulse source — the "
            "baseline it writes is marked provisional until re-run with --pulse live."
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
    verdicts = await judge.judge_many(
        [req for _, req in to_judge], concurrency=args.concurrency
    )
    await judge.aclose()
    verdict_by_qid = {
        pair["question_id"]: v for (pair, _), v in zip(to_judge, verdicts, strict=True)
    }

    # Deterministic exact-match runs alongside the judge, not through it.
    normalize = (
        NumericNormalization() if args.normalize_numerics else None
    )  # OI-2: provisional relaxation, off by default
    results: list[
        tuple[dict, JudgeRequest, JudgeVerdict | Exception, ExactMatchResult]
    ] = []
    for pair, req, err in collected:
        if err:
            results.append((pair, req, PlatformError(err), ExactMatchResult.ERROR))
        else:
            v = verdict_by_qid[pair["question_id"]]
            em = exact_match(
                req.expected_answer, req.platform_answer, normalize=normalize
            )
            results.append((pair, req, v, em))

    if len(results) <= 30:
        for pair, req, v, em in results:
            _print_row(pair, req, v, em)

    summary = _summarise(results)
    summary["calibrated"] = calibrated
    summary["provider"] = provenance
    summary["numeric_normalization"] = normalize.label if normalize else "off"
    if normalize:
        print(
            "[judge] NOTE: --normalize-numerics is on — exact-match ignored "
            f"[{normalize.label}]. This is the provisional OI-2 relaxation, not "
            "the certified §HC-3 comparison; the scorecard is marked accordingly.",
            file=sys.stderr,
        )
    temp_enforced = getattr(judge, "temperature_enforced", True)
    summary["judge_temperature_enforced"] = temp_enforced
    if not temp_enforced:
        print(
            "[judge] ⚠ the judge model rejected temperature=0 — this run ran on "
            "the model default + fixed seed. Reproducibility rests on the seed "
            "(§10.1). Recorded as judge_temperature_enforced=false.",
            file=sys.stderr,
        )

    ctx = RunContext(
        run_id=run_id,
        run_timestamp_iso=run_timestamp_iso,
        platform_version=args.platform_version,
        dataset_version=args.dataset_version,
        scorecard_mode=scorecard_mode,
        calibrated=calibrated,
        numeric_normalization=normalize.label if normalize else "off",
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

    if args.judge == "mock":
        print(
            "\n[judge] NOTE: mock judge is a heuristic stand-in — scores are NOT "
            "semantic. Use --judge llm for real evaluation."
        )
    if not summary["calibrated"] and args.judge == "llm":
        print(
            "\n[judge] NOTE: judge is uncalibrated (§10.3). These scores are "
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
        tuple[dict, JudgeRequest, JudgeVerdict | Exception, ExactMatchResult]
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
    """Run the §10.3 calibration protocol against judge/anchors/<domain>.json.

    - Scores every anchor with the live LLM judge (combined mode, cache on).
    - Compares each dimension against the human anchor score.
    - Writes .calibration/<domain>.passed.json iff every dimension has
      ≥90% agreement within ±1 (per calibration.ACCEPTANCE_AGREEMENT_PCT)
      and ≥10 anchors are present.
    """
    from judge.calibration import evaluate, load_anchors, record_passed

    anchors = load_anchors(domain)
    settings = load_llm_settings()
    # per_dimension: 4 scoped prompts per anchor, prevents the halo effect where
    # a low score on one dimension pulls the others down (PLAN §10.2 note).
    cfg = load_judge_config(domain).model_copy(
        update={"mode": "per_dimension", "cache_enabled": True}
    )
    model_version = settings.model
    cache_dir = MODULE_ROOT / ".judge_cache" / model_version.replace("/", "_")
    judge_run_id = (
        f"calibration-{domain}-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )
    calibration_log = RUNS_DIR / judge_run_id / "prompts_log.jsonl"
    judge = OpenAIJudge(
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
        marker = record_passed(domain, result)
        print(f"[calibrate] PASSED — wrote {marker}")
        print(f"[calibrate] --judge llm now allowed against domain={domain!r}")
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
        choices=["llm", "mock"],
        default="llm",
        help="llm = Azure OpenAI / OpenAI direct (default). "
        "mock = deterministic heuristic fallback for CI/dev.",
    )
    ap.add_argument(
        "--mode",
        choices=["combined", "per_dimension"],
        default="combined",
        help="combined = 1 prompt for all 4 dims. per_dimension = 4 prompts, no halo effect.",
    )
    ap.add_argument(
        "--pulse",
        choices=["good", "regressed", "bad", "echo", "sql", "live"],
        default="good",
        help="Pulse source: good/regressed/bad = canned fixtures keyed by question_id (CI/dev). "
        "echo = replays each pair's expected_answer as the platform answer "
        "(offline plumbing test; requires --input-csv). "
        "sql = executes each pair's reference_sql against a CSV dataset and returns that "
        "(requires --input-csv AND --pulse-data). "
        "live = the real platform API (HC-4) — needs the PULSE_* creds in judge/.env and "
        "--input-csv. Only 'live' is eligible for a non-provisional release baseline.",
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
        help="§10.3 override: run the LLM judge without a calibration marker. "
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
        "--normalize-numerics",
        action="store_true",
        help="OI-2 provisional relaxation: ignore thousands separators and "
        "trailing decimal zeros in exact-match (so '$438,632.65' matches "
        "'438632.65'). OFF by default — the certified §HC-3 comparison is "
        "byte-identical. Runs using this are marked in the scorecard.",
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
