"""
Generic business-vocabulary lookup. Scope doc Section 9.4: the
natural-language question uses business words; raw schema enum tokens and
column names do not appear in it.

Nothing here is domain-specific. The mapping itself (raw token ->
business phrase) is a per-domain data file - for CRM,
``generator/labels.json`` - loaded with :func:`load` and passed to
:func:`label` when a driver builds question text. reference_sql,
reference_fields, and derivation_rationale keep the real tokens.
"""

from __future__ import annotations

import json
from pathlib import Path


def load(path: str | Path) -> dict[str, str]:
    """Load a raw-token -> business-phrase mapping from a JSON file.

    Keys beginning with ``_`` (documentation) are ignored.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def label(value, mapping: dict[str, str]) -> str:
    """Business label for an enum value; falls back to the value itself."""
    return mapping.get(value, value)
