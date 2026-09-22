"""Compare a partial re-run against the full run it came from.

WHY NOT OVERWRITE THE BASE RUN
------------------------------
A run directory is evidence: `run_manifest.json` records the commit, the input
hash and the argv that produced it, and `pulse_raw/` holds the untouched platform
payloads. Editing results.json in place would leave a manifest describing a run
that no longer matches its own output, and destroy the record of what the
platform actually said. So the base run is read-only and this reports the
combined picture instead.

    python judge/compare_runs.py --base <full_run_id> --new <rerun_id>

`combined exact-match` is the honest number after a partial re-run: outcomes from
the re-run for the pairs it covered, outcomes from the base run for everything
else. Pairs the re-run deliberately skipped keep their original verdict, so the
denominator stays the full domain.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load(domain: str, run_id: str) -> dict[str, dict]:
    path = REPO_ROOT / "release" / domain / "eval-runs" / run_id / "results.json"
    if not path.is_file():
        raise SystemExit(f"no results.json for run {run_id} ({path})")
    data = json.loads(path.read_text(encoding="utf-8"))
    return {row["question_id"]: row for row in data["rows"]}


def compare(domain: str, base_id: str, new_id: str) -> int:
    base = _load(domain, base_id)
    new = _load(domain, new_id)

    cleared, still_failing, regressed = [], [], []
    for qid, row in new.items():
        if qid not in base:
            continue
        was, now = base[qid]["exact_match_result"], row["exact_match_result"]
        if was != "pass" and now == "pass":
            cleared.append(qid)
        elif was != "pass":
            still_failing.append(qid)
        elif now != "pass":
            regressed.append(qid)

    print(f"base run : {base_id}  ({len(base)} pairs)")
    print(f"re-run   : {new_id}  ({len(new)} pairs)")
    print()
    print(f"  cleared       : {len(cleared)}")
    print(f"  still failing : {len(still_failing)}")
    if regressed:
        print(f"  REGRESSED     : {len(regressed)}  {', '.join(sorted(regressed))}")

    # Combined: the re-run wins where it covered a pair, the base holds elsewhere.
    combined = dict(base)
    combined.update(new)
    eligible = [
        r for r in combined.values() if r["exact_match_result"] in ("pass", "fail")
    ]
    passed = [r for r in eligible if r["exact_match_result"] == "pass"]
    base_eligible = [
        r for r in base.values() if r["exact_match_result"] in ("pass", "fail")
    ]
    base_passed = [r for r in base_eligible if r["exact_match_result"] == "pass"]

    print()
    print(
        f"  exact-match before : {len(base_passed)}/{len(base_eligible)} = "
        f"{100 * len(base_passed) / max(1, len(base_eligible)):.1f}%"
    )
    print(
        f"  exact-match after  : {len(passed)}/{len(eligible)} = "
        f"{100 * len(passed) / max(1, len(eligible)):.1f}%"
    )

    if cleared:
        print()
        print("cleared:")
        for qid in sorted(cleared):
            print(f"  {qid}")
    if still_failing:
        print()
        print("still failing (a verdict the re-upload did not change):")
        for qid in sorted(still_failing):
            print(f"  {qid}  {base[qid]['exact_match_detail'][:70]}")

    # The clarification question: did re-profiling populate the enum values?
    def clarified(run_id: str, qid: str) -> bool:
        """True when the platform declined and asked instead of answering.

        The flag lives in `response.response`, which is a *stringified* JSON
        document — so in the file on disk it appears escaped as `\\"clarify\\":
        true`. Matching the unescaped form against the raw file text silently
        finds nothing, which is exactly how this check reported zero
        clarifications on a run that had eleven. Parse, then look.
        """
        raw = (
            REPO_ROOT
            / "release"
            / domain
            / "eval-runs"
            / run_id
            / "pulse_raw"
            / f"{qid}.json"
        )
        if not raw.is_file():
            return False
        try:
            payload = json.loads(raw.read_text(encoding="utf-8"))
        except ValueError:
            return False
        response = payload.get("response") or {}
        inner = response.get("response")
        if isinstance(inner, str):
            try:
                return json.loads(inner).get("clarify") is True
            except ValueError:
                return False
        if isinstance(inner, dict):
            return inner.get("clarify") is True
        return False

    was_clar = [q for q in new if clarified(base_id, q)]
    if was_clar:
        still = [q for q in was_clar if clarified(new_id, q)]
        print()
        print(
            f"clarification requests: {len(was_clar)} before -> {len(still)} after "
            f"({len(was_clar) - len(still)} now answered)"
        )
        print(
            "  (these were all the schema profile missing enum values that exist in "
            "the data; a re-upload re-profiles, so this measures whether that fixed it)"
        )
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", required=True, help="the full run to compare against")
    ap.add_argument("--new", required=True, help="the partial re-run")
    ap.add_argument("--domain", default="crm")
    args = ap.parse_args()
    raise SystemExit(compare(args.domain, args.base, args.new))


if __name__ == "__main__":
    main()
