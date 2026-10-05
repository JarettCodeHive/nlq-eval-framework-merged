"""Build the judge input CSV from a Q&A release package.

`judge --input-csv` needs `question_id`, `domain` and `tier` alongside the
contracted seven fields. Most rows get their evaluation metadata from the
companion by joining on `natural_language_question`. CRM rephrase variants are
present only in the main contract; they carry their own identifiers and inherit
`family` / `scoring_mode` from the single companion base in the same
`rephrase_group_id`.

The join key is verified unique on both sides before anything is written: a
duplicated question would otherwise bind a pair to the wrong question_id and
silently mis-tier it in the scorecard.

Deterministic: same release in, byte-identical CSV out. Rows are emitted in
companion order (sorted by question_id), so a run log climbs tiers in order.

    python judge/build_input.py --qa-release release/v1.0.0/crm/qa_pairs

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


def _variant_metadata(
    pair: dict[str, str],
    companion_by_group: dict[str, list[dict[str, str]]],
    contract_path: Path,
) -> dict[str, str]:
    """Resolve metadata for a contract-only rephrase variant.

    A variant has its own question id and tier, but scoring metadata belongs to
    the group's base row in the companion. Requiring exactly one base prevents
    a malformed group from silently borrowing metadata from an arbitrary row.
    """

    question = pair[JOIN_KEY]
    group_id = (pair.get("rephrase_group_id") or "").strip()
    if not group_id:
        raise SystemExit(
            f"{contract_path}: contract row has no companion match and no "
            f"rephrase_group_id: {question!r}"
        )

    bases = companion_by_group.get(group_id, [])
    if len(bases) != 1:
        raise SystemExit(
            f"{contract_path}: rephrase group {group_id!r} must have exactly "
            f"one companion base; found {len(bases)}"
        )

    missing = [field for field in ("question_id", "tier") if not pair.get(field)]
    if missing:
        raise SystemExit(
            f"{contract_path}: rephrase variant {question!r} is missing "
            f"{', '.join(missing)}"
        )

    base = bases[0]
    return {
        "question_id": pair["question_id"],
        "tier": pair["tier"],
        "family": base["family"],
        "scoring_mode": base["scoring_mode"],
    }


def build(qa_release: Path, domain: str, output: Path) -> int:
    contract_path = qa_release / f"{domain}_qa_pairs.csv"
    companion_path = qa_release / f"{domain}_qa_pairs_companion.csv"

    contract = _read(contract_path)
    companion = _read(companion_path)
    _require_unique(contract, contract_path)
    _require_unique(companion, companion_path)

    by_question = {row[JOIN_KEY]: row for row in contract}
    companion_by_question = {row[JOIN_KEY]: row for row in companion}
    missing = [
        row["question_id"] for row in companion if row[JOIN_KEY] not in by_question
    ]
    if missing:
        raise SystemExit(
            f"{len(missing)} companion row(s) have no contract match, "
            f"first: {missing[0]}"
        )

    companion_by_group: dict[str, list[dict[str, str]]] = {}
    for row in companion:
        group_id = (row.get("rephrase_group_id") or "").strip()
        if group_id:
            companion_by_group.setdefault(group_id, []).append(row)

    fieldnames = [
        "question_id",
        "domain",
        "tier",
        "family",
        "scoring_mode",
        "rephrase_group_id",
        *CONTRACT_FIELDS,
    ]
    rows = []
    # Preserve the established base-row ordering from the companion, then add
    # contract-only variants in their deterministic contract order.
    for comp in companion:
        pair = by_question[comp[JOIN_KEY]]
        row = {field: comp[field] for field in COMPANION_FIELDS}
        row["domain"] = domain
        row["rephrase_group_id"] = (
            pair.get("rephrase_group_id") or comp.get("rephrase_group_id") or ""
        )
        row.update({field: pair[field] for field in CONTRACT_FIELDS})
        rows.append(row)

    for pair in contract:
        if pair[JOIN_KEY] in companion_by_question:
            continue
        row = _variant_metadata(pair, companion_by_group, contract_path)
        row["domain"] = domain
        row["rephrase_group_id"] = pair["rephrase_group_id"]
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
        default=Path("release/v1.0.0/crm/qa_pairs"),
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
