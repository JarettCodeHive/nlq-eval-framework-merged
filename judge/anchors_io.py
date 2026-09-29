"""Anchor authoring — get real graded examples into `judge/anchors/<domain>.json`.

§10.2 needs ≥10 human-graded anchors per domain "spanning the score range — not
10 easy passes". The module that consumes them (`judge.calibration`) could
already check whether a set was strong enough; nothing produced one. The only
set in the repo, `crm.provisional.json`, was authored alongside the rubric — its
rationales cite the rubric back at itself — and is quarantined for exactly that
reason. So anchors have been the thing blocking calibration, and calibration the
thing blocking every release scorecard.

Two steps, deliberately separated by a human:

    export  a completed run   -> a CSV the graders fill in
    import  the filled CSV    -> judge/anchors/<domain>.json

WHY THE SHEET CARRIES NO JUDGE SCORES
-------------------------------------
§10.2 says to run the judge against the anchors "blind to the human scores".
That is one direction of a pair, and the other matters just as much: a grader
shown the judge's 4 will hand back a 4. The whole measurement is agreement
between two independent opinions, so the export writes the question, the
platform's answer and the SQL — and nothing the judge concluded about them.
`exact_match_result` is included because it is a deterministic fact about the
numbers, not an opinion about quality.

WHAT GETS PICKED
----------------
Sampling the first N rows of a run would reproduce the failure the provisional
set already has: T1 scalars that everything gets right. Candidates are
stratified across exact-match outcome and tier, and the selection deliberately
front-loads exact-match FAILs, because §10.2's most consequential case is a
judge that never scores 1 — and the only anchors that can catch it are ones
where the platform returned a *wrong number*.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

from judge.calibration import AnchorSetTooWeak, anchor_strength, anchors_path_for
from judge.config import REPO_ROOT, load_judge_config
from judge.contracts import DIMENSIONS

# Written by export, read back by import. The `human_*` columns are the graders'
# only job; everything before them is evidence.
EVIDENCE_COLUMNS: tuple[str, ...] = (
    "question_id",
    "tier",
    "natural_language_question",
    "expected_answer",
    "judge_reference",
    "platform_answer",
    "generated_sql",
    "exact_match_result",
)
SCORE_COLUMNS: tuple[str, ...] = tuple(f"human_{d}" for d in DIMENSIONS)
RATIONALE_COLUMNS: tuple[str, ...] = tuple(f"rationale_{d}" for d in DIMENSIONS)
SHEET_COLUMNS: tuple[str, ...] = EVIDENCE_COLUMNS + SCORE_COLUMNS + RATIONALE_COLUMNS

# Ordered worst-first so the stratified pick reaches the informative rows before
# it reaches the easy ones. A FAIL is a wrong number, which is the anchor that
# catches a judge that cannot say 1.
_OUTCOME_PRIORITY = ("fail", "clarification", "not_applicable", "error", "pass")


class AnchorExportError(RuntimeError):
    """Raised with the remedy, not just the gap."""


def _run_dir(domain: str, run_id: str | None, repo_root: Path | None = None) -> Path:
    root = repo_root or REPO_ROOT
    runs = root / load_judge_config(domain).run_output_root.format(domain=domain)
    if run_id:
        candidate = runs / run_id
        if not (candidate / "results.json").is_file():
            raise AnchorExportError(f"no results.json under {candidate}")
        return candidate
    available = sorted(p for p in runs.glob("*") if (p / "results.json").is_file())
    if not available:
        raise AnchorExportError(
            f"no scored run under {runs}. Score the domain first: "
            f"python main.py judge --domain {domain} --profile full"
        )
    return available[-1]


def _judge_reference_by_id(domain: str, profile: str) -> dict[str, dict[str, str]]:
    """`judge_reference` and `reference_sql` live in the pair set, not the run.

    A run never records `reference_sql` — it is withheld from the judge on
    purpose (§10.1) — and does not echo `judge_reference` back. Both belong in an
    anchor, so they come from the Q&A package the run scored.
    """

    from judge.resolve import judge_input_csv

    try:
        path = judge_input_csv(domain, profile, build_if_missing=False)
    except Exception:
        return {}
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["question_id"]: row for row in csv.DictReader(handle)}


def _stratified(rows: list[dict], count: int) -> list[dict]:
    """Pick `count` rows spread across exact-match outcome and tier.

    Deterministic: buckets are keyed and sorted, never sampled randomly, so the
    same run always proposes the same anchors and a re-export after a partial
    grading session does not reshuffle the sheet under the graders.
    """

    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        outcome = str(row.get("exact_match_result") or "unknown").lower()
        buckets[(outcome, str(row.get("tier") or ""))].append(row)
    for bucket in buckets.values():
        bucket.sort(key=lambda r: str(r.get("question_id")))

    def bucket_order(key: tuple[str, str]) -> tuple[int, str]:
        outcome, tier = key
        priority = (
            _OUTCOME_PRIORITY.index(outcome)
            if outcome in _OUTCOME_PRIORITY
            else len(_OUTCOME_PRIORITY)
        )
        return (priority, tier)

    keys = sorted(buckets, key=bucket_order)
    picked: list[dict] = []
    # Round-robin, so a domain with 100 FAILs cannot fill the whole sheet with
    # them and leave the judge unable to demonstrate it can still say 5.
    while len(picked) < count and any(buckets[k] for k in keys):
        for key in keys:
            if not buckets[key]:
                continue
            picked.append(buckets[key].pop(0))
            if len(picked) == count:
                break
    return picked


def export_candidates(
    domain: str,
    *,
    run_id: str | None = None,
    profile: str = "full",
    count: int | None = None,
    output: Path | None = None,
    repo_root: Path | None = None,
) -> Path:
    """Write a grading sheet of candidate anchors from a scored run."""

    config = load_judge_config(domain)
    target = count or max(config.calibration.min_anchors + 2, 12)
    run_dir = _run_dir(domain, run_id, repo_root)
    payload = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    rows = payload.get("rows") or []
    if not rows:
        raise AnchorExportError(f"{run_dir / 'results.json'} has no rows")

    references = _judge_reference_by_id(domain, profile)
    picked = _stratified(rows, target)

    out = output or run_dir / f"{domain}_anchor_candidates.csv"
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SHEET_COLUMNS)
        writer.writeheader()
        for row in picked:
            ref = references.get(str(row.get("question_id")), {})
            writer.writerow(
                {
                    "question_id": row.get("question_id", ""),
                    "tier": row.get("tier", ""),
                    "natural_language_question": row.get(
                        "natural_language_question", ""
                    ),
                    "expected_answer": row.get("expected_answer", ""),
                    "judge_reference": ref.get("judge_reference", ""),
                    "platform_answer": row.get("actual_answer", ""),
                    "generated_sql": row.get("platform_generated_sql", ""),
                    "exact_match_result": row.get("exact_match_result", ""),
                    # Left blank on purpose — see the module docstring.
                    **{column: "" for column in SCORE_COLUMNS + RATIONALE_COLUMNS},
                }
            )

    outcomes = defaultdict(int)
    for row in picked:
        outcomes[str(row.get("exact_match_result") or "unknown").lower()] += 1
    print(f"[anchors] run: {run_dir.name}")
    print(f"[anchors] {len(picked)} candidates, by exact-match outcome:")
    for outcome in sorted(outcomes):
        print(f"[anchors]   {outcome:16} {outcomes[outcome]}")
    print(f"[anchors] grading sheet: {out}")
    print(
        f"[anchors] Fill the human_* columns (1-5) and a rationale for each, "
        f"reconcile between BOTH graders, then:\n"
        f"[anchors]   python main.py anchors-import --domain {domain} "
        f"--sheet {out}"
    )
    return out


def _score(raw: str, column: str, question_id: str) -> int:
    value = (raw or "").strip()
    if not value:
        raise AnchorExportError(
            f"{question_id}: {column} is blank. Every anchor needs all four "
            f"dimensions graded — a partial sheet cannot be reconciled."
        )
    try:
        score = int(value)
    except ValueError:
        raise AnchorExportError(
            f"{question_id}: {column}={value!r} is not an integer 1-5."
        ) from None
    if not 1 <= score <= 5:
        raise AnchorExportError(f"{question_id}: {column}={score} is outside 1-5.")
    return score


def import_grades(
    domain: str,
    sheet: Path,
    *,
    output: Path | None = None,
    force: bool = False,
) -> Path:
    """Turn a filled grading sheet into `judge/anchors/<domain>.json`.

    The set is checked BEFORE it is written. `anchor_strength` is the §10.2
    "spanning the score range" test, and a set that a constant-scoring judge
    would pass is refused here rather than at calibration time, after the
    provider budget has been spent.
    """

    if not sheet.is_file():
        raise AnchorExportError(f"no grading sheet at {sheet}")
    with sheet.open(newline="", encoding="utf-8") as handle:
        graded = list(csv.DictReader(handle))
    if not graded:
        raise AnchorExportError(f"{sheet} has no rows")

    anchors: list[dict] = []
    for row in graded:
        question_id = (row.get("question_id") or "").strip()
        if not question_id:
            raise AnchorExportError(f"{sheet}: a row has no question_id")
        anchors.append(
            {
                "question_id": question_id,
                "natural_language_question": row.get("natural_language_question", ""),
                "expected_answer": row.get("expected_answer", ""),
                "judge_reference": row.get("judge_reference", ""),
                "reference_sql": row.get("reference_sql", ""),
                "platform_answer": row.get("platform_answer", ""),
                "generated_sql": row.get("generated_sql", ""),
                "human_scores": {
                    dimension: _score(
                        row.get(f"human_{dimension}", ""),
                        f"human_{dimension}",
                        question_id,
                    )
                    for dimension in DIMENSIONS
                },
                "human_rationale": {
                    dimension: (row.get(f"rationale_{dimension}") or "").strip()
                    for dimension in DIMENSIONS
                },
            }
        )

    config = load_judge_config(domain)
    minimum = config.calibration.min_anchors
    problems: list[str] = []
    if len(anchors) < minimum:
        problems.append(f"§10.2 requires ≥{minimum} anchors; the sheet has {len(anchors)}")
    for dimension, strength in anchor_strength(anchors).items():
        if not strength.passes:
            problems.append(f"{dimension}: {strength.reason}")
    if problems and not force:
        raise AnchorSetTooWeak(
            "this anchor set cannot certify a judge:\n  - "
            + "\n  - ".join(problems)
            + "\n\nThe most common gap is no factual_correctness=1 — an anchor "
            "where the platform returned a WRONG NUMBER. Without one, the set "
            "cannot detect a judge that never scores 1, which is the most "
            "consequential judge failure there is. Grade more of the "
            "exact-match FAIL rows, or pass --force to write it anyway as a "
            "work-in-progress (calibration will still refuse it)."
        )

    out = output or anchors_path_for(domain)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(anchors, indent=2) + "\n", encoding="utf-8")

    print(f"[anchors] wrote {len(anchors)} anchors: {out}")
    for dimension, strength in anchor_strength(anchors).items():
        mark = "ok " if strength.passes else "WEAK"
        print(f"[anchors]   {mark} {dimension}")
    if problems:
        print("[anchors] WRITTEN WITH --force; calibration will still refuse this set.")
    else:
        print(
            f"[anchors] Now run the §10.2 protocol: "
            f"python main.py calibrate --domain {domain}"
        )
    return out
