"""CSV input contract for the POC evaluation runner.

Required columns:
  natural_language_question, expected_answer, judge_reference, reference_sql

Optional columns (auto-synthesized from the file basename or CLI defaults
when absent — see fill_missing_* helpers):
  question_id, domain, tier, rephrase_group_id

Extra columns in the CSV (e.g., reference_tables, reference_fields,
derivation_rationale from Dhiru's qa_pair_poc output) are ignored — they're
not needed for scoring, just useful for pair authors.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

REQUIRED_FIELDS: tuple[str, ...] = (
    "natural_language_question",
    "expected_answer",
    "judge_reference",
    "reference_sql",
)

# Present when the pair author is following the fully-annotated contract.
# When absent, the loader synthesizes plausible defaults (see below) so a
# CSV emitted by any upstream tool can drive the judge without pre-processing.
AUTO_FILLABLE_FIELDS: tuple[str, ...] = ("question_id", "domain", "tier")
OPTIONAL_FIELDS: tuple[str, ...] = AUTO_FILLABLE_FIELDS + ("rephrase_group_id",)


@dataclass(frozen=True)
class InputRow:
    question_id: str
    domain: str
    tier: str
    natural_language_question: str
    expected_answer: str
    judge_reference: str
    reference_sql: str
    rephrase_group_id: str | None = None

    def as_pair(self) -> dict[str, str | None]:
        return {
            "question_id": self.question_id,
            "domain": self.domain,
            "tier": self.tier,
            "natural_language_question": self.natural_language_question,
            "expected_answer": self.expected_answer,
            "judge_reference": self.judge_reference,
            "reference_sql": self.reference_sql,
            "rephrase_group_id": self.rephrase_group_id,
        }


_TIER_RE = re.compile(r"[_-]t([1-5])[_.-]", re.IGNORECASE)


def _tier_from_basename(stem: str) -> str | None:
    """Extract tier from a filename like `crm_t1_pairs_HELD` → `T1`."""
    match = _TIER_RE.search(
        f"_{stem}_"
    )  # bracket with underscores so t1 at edge still matches
    return f"T{match.group(1)}" if match else None


def _domain_from_basename(stem: str) -> str | None:
    """Extract a domain from a filename prefix like `crm_qa_pairs_seed` → `crm`."""
    head = stem.split("_", 1)[0].lower()
    if head in {"crm", "sales", "finance", "pm", "logistics"}:
        return head
    return None


def load_input_csv(
    path: str | Path,
    *,
    default_domain: str = "crm",
    default_tier: str = "unknown",
) -> list[InputRow]:
    """Load pair rows. Missing question_id / domain / tier are auto-filled from
    the file basename (or the supplied defaults) so upstream authors don't have
    to keep the bookkeeping columns in sync — the loader will do it.
    """
    csv_path = Path(path)
    if not csv_path.is_file():
        raise FileNotFoundError(f"input CSV not found: {csv_path}")

    stem = csv_path.stem
    fallback_domain = _domain_from_basename(stem) or default_domain
    fallback_tier = _tier_from_basename(stem) or default_tier

    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames or []
        missing_required = [f for f in REQUIRED_FIELDS if f not in fieldnames]
        if missing_required:
            raise ValueError(
                "input CSV missing required field(s): " + ", ".join(missing_required)
            )

        rows: list[InputRow] = []
        for idx, raw in enumerate(reader, start=2):
            values = {f: (raw.get(f) or "").strip() for f in REQUIRED_FIELDS}
            if not any(values.values()):
                continue  # blank row
            missing_values = [f for f, v in values.items() if not v]
            if missing_values:
                raise ValueError(
                    f"input CSV row {idx} missing value(s): "
                    + ", ".join(missing_values)
                )
            question_id = (
                raw.get("question_id") or ""
            ).strip() or f"{stem}-{idx - 1:04d}"
            domain = (raw.get("domain") or "").strip() or fallback_domain
            tier = (raw.get("tier") or "").strip() or fallback_tier
            rephrase_group_id = (raw.get("rephrase_group_id") or "").strip() or None
            rows.append(
                InputRow(
                    question_id=question_id,
                    domain=domain,
                    tier=tier,
                    rephrase_group_id=rephrase_group_id,
                    **values,
                )
            )
    return rows
