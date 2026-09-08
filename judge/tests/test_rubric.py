"""Rubric PDF deliverable (§14.1)."""

from __future__ import annotations

from judge.prompts import prompt_version
from judge.rubric import _template_text, build_rubric_pdf


def test_template_text_renders_real_rubric_content():
    text = _template_text("judge/_rubric.jinja")
    assert "FACTUAL CORRECTNESS" in text
    assert "SQL PLAUSIBILITY" in text
    # Jinja comment markers must not leak into the deliverable.
    assert "{#" not in text and "#}" not in text


def test_non_determinism_boundary_worked_examples_are_carried():
    text = _template_text("judge/_non_determinism_boundary.jinja")
    assert "WORKED EXAMPLES" in text
    assert "DO NOT DEDUCT" in text and "DO DEDUCT" in text


def test_build_rubric_pdf_writes_a_pdf(tmp_path):
    out = tmp_path / "judge_rubric.pdf"
    path = build_rubric_pdf(out)
    assert path == out and path.is_file()
    assert path.read_bytes()[:5] == b"%PDF-"
    assert path.stat().st_size > 4000  # not an empty shell


def test_rubric_pdf_build_is_deterministic_for_a_fixed_prompt(tmp_path):
    # Same templates in → same page count out; the cover names prompt_version().
    a = build_rubric_pdf(tmp_path / "a.pdf").read_bytes()
    b = build_rubric_pdf(tmp_path / "b.pdf").read_bytes()
    assert len(a) == len(b)
    assert prompt_version().startswith("judge-")
