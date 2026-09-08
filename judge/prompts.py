"""Jinja2 prompt rendering + prompt versioning.

Prompt logic lives in `templates/`, never in code. `prompt_version()` is a
content hash over every template file, so it changes automatically when a
template does — a score always carries the exact prompt revision that produced
it, which a hand-maintained version string can't guarantee.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from judge.contracts import DIMENSIONS, JudgeRequest

TEMPLATES_ROOT = Path(__file__).resolve().parent / "templates"

DIMENSION_LABELS = {
    "factual_correctness": "Factual Correctness",
    "completeness": "Completeness",
    "format_adherence": "Format Adherence",
    "sql_plausibility": "SQL Plausibility",
}


@lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES_ROOT),
        # StrictUndefined: a typo'd variable raises at render time instead of
        # silently rendering an empty string, which would produce a confident
        # score based on missing evidence.
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,  # prompts are plain text, not markup
    )


@lru_cache(maxsize=1)
def prompt_version() -> str:
    """Short content hash over every judge template."""
    digest = hashlib.sha256()
    root = TEMPLATES_ROOT / "judge"
    for path in sorted(_walk(root)):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return "judge-" + digest.hexdigest()[:12]


def _walk(root: Path):
    for entry in sorted(root.iterdir()):
        if entry.is_dir():
            yield from _walk(entry)
        elif entry.name.endswith(".jinja"):
            yield entry


def render_static(name: str) -> str:
    """Render a variable-free template to plain text (used by the rubric PDF)."""
    return _env().get_template(name).render().strip()


def _ctx(req: JudgeRequest) -> dict[str, str | None]:
    return {
        "question": req.question,
        "expected_answer": req.expected_answer,
        "judge_reference": req.judge_reference,
        "platform_answer": req.platform_answer,
        "generated_sql": req.generated_sql,
    }


def render_combined(req: JudgeRequest) -> str:
    """One prompt scoring all four dimensions."""
    return _env().get_template("judge/combined.jinja").render(**_ctx(req))


def render_dimension(req: JudgeRequest, dimension: str) -> str:
    """One prompt scoring a single dimension (removes halo effect between dimensions)."""
    if dimension not in DIMENSIONS:
        raise ValueError(
            f"unknown dimension {dimension!r}; expected one of {DIMENSIONS}"
        )
    return (
        _env()
        .get_template("judge/per_dimension.jinja")
        .render(
            dimension=dimension,
            dimension_label=DIMENSION_LABELS[dimension],
            **_ctx(req),
        )
    )
