"""Build the judge input CSV from a Q&A release package.

`judge --input-csv` needs `question_id`, `domain` and `tier` alongside the
contracted seven fields, but the Q&A release splits them: `crm_qa_pairs.csv`
carries the §9.3 contract and nothing else, and `crm_qa_pairs_companion.csv`
carries the identifiers. This joins the two on `natural_language_question` —
the only column they share — and writes one file the judge can consume.

The join key is verified unique on both sides before anything is written: a
duplicated question would otherwise bind a pair to the wrong question_id and
silently mis-tier it in the scorecard.

Deterministic: same release in, byte-identical CSV out. Rows are emitted in
companion order (sorted by question_id), so a run log climbs tiers in order.

    python judge/build_input.py --qa-release release/crm/qa-pairs-v0.3.0

Supersedes the per-tier concatenation used before the Q&A pipeline emitted a
single package.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

CONTRACT_FIELDS: tuple[str, ...] = (
    "natural_language_question",
    "expected_answer",
    "reference_sql",
    "reference_tables",
    "reference_fields",
    "judge_reference",
    "derivation_rationale",
)

# Carried through from the companion. `scoring_mode` is not consumed by the
# judge today — it is the Q&A side's declaration of how each pair should be
# scored, and it travels with the pair so a run can be audited against it.
COMPANION_FIELDS: tuple[str, ...] = ("question_id", "tier", "family", "scoring_mode")

JOIN_KEY = "natural_language_question"


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise SystemExit(f"{path} has no rows")
    return rows


def _require_unique(rows: list[dict[str, str]], path: Path) -> None:
    counts = Counter(row[JOIN_KEY] for row in rows)
    duplicates = sorted(q for q, n in counts.items() if n > 1)
    if duplicates:
        raise SystemExit(
            f"{path}: {JOIN_KEY} is not unique, cannot join safely. "
            f"First duplicate: {duplicates[0]!r}"
        )


def build(qa_release: Path, domain: str, output: Path) -> int:
    contract_path = qa_release / f"{domain}_qa_pairs.csv"
    companion_path = qa_release / f"{domain}_qa_pairs_companion.csv"

    contract = _read(contract_path)
    companion = _read(companion_path)
    _require_unique(contract, contract_path)
    _require_unique(companion, companion_path)

    by_question = {row[JOIN_KEY]: row for row in contract}
    missing = [
        row["question_id"] for row in companion if row[JOIN_KEY] not in by_question
    ]
    if missing:
        raise SystemExit(
            f"{len(missing)} companion row(s) have no contract match, "
            f"first: {missing[0]}"
        )
    if len(contract) != len(companion):
        raise SystemExit(
            f"row count mismatch: {len(contract)} contract vs {len(companion)} companion"
        )

    fieldnames = [
        "question_id",
        "domain",
        "tier",
        "family",
        "scoring_mode",
        *CONTRACT_FIELDS,
    ]
    rows = []
    for comp in companion:
        pair = by_question[comp[JOIN_KEY]]
        row = {field: comp[field] for field in COMPANION_FIELDS}
        row["domain"] = domain
        row.update({field: pair[field] for field in CONTRACT_FIELDS})
        rows.append(row)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    tiers = Counter(row["tier"] for row in rows)
    for tier in sorted(tiers):
        print(f"{tier}: {tiers[tier]} pairs")
    print(f"\n{output}: {len(rows)} pairs, {len(fieldnames)} columns")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--qa-release",
        type=Path,
        default=Path("release/crm/qa-pairs-v0.3.0"),
        help="Q&A release package directory",
    )
    parser.add_argument("--domain", default="crm")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="defaults to <qa-release>/<domain>_judge_input.csv",
    )
    args = parser.parse_args()
    output = args.output or args.qa_release / f"{args.domain}_judge_input.csv"
    build(args.qa_release, args.domain, output)


if __name__ == "__main__":
    main()
