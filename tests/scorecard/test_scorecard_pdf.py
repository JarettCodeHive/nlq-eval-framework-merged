"""The §11.3 PDF summary is read by people deciding things — test what it says.

`pypdf` is not a project dependency, so text-extraction tests skip without it.
The structural assertions (that the writer runs, paginates, and colours) do not
need it and always run.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from judge.contracts import DIMENSIONS, ClarificationVerdict, JudgeVerdict
from judge.exact_match import ExactMatchOutcome, ExactMatchResult
from scorecard import report
from scorecard.report import (
    ACCURACY_GATE_PCT,
    _provenance,
    _warnings,
    write_scorecard_pdf,
)
from scorecard.summary import RunContext, build_summary_rows


def _verdict(score: int = 4) -> JudgeVerdict:
    return JudgeVerdict(
        dimension_rationales=dict.fromkeys(DIMENSIONS, "r"),
        factual_correctness=score,
        completeness=score,
        format_adherence=score,
        sql_plausibility=score,
        prompt_version="p",
        model_version="m",
    )


def _row(tier: str, result: ExactMatchResult, verdict) -> tuple:
    return (
        {"question_id": "q", "domain": "crm", "tier": tier},
        SimpleNamespace(
            question="q",
            expected_answer="1",
            platform_answer="1",
            generated_sql="SELECT 1",
        ),
        verdict,
        ExactMatchOutcome(result),
    )


def _results(*, passes: int, fails: int, clarifications: int = 0) -> list:
    rows = [_row("T1", ExactMatchResult.PASS, _verdict(5)) for _ in range(passes)]
    rows += [_row("T1", ExactMatchResult.FAIL, _verdict(2)) for _ in range(fails)]
    rows += [
        _row("T1", ExactMatchResult.CLARIFICATION, ClarificationVerdict())
        for _ in range(clarifications)
    ]
    return rows


def _ctx(**overrides) -> RunContext:
    base = {
        "run_id": "20260923T140000Z",
        "run_timestamp_iso": "2026-09-23T14:00:00+00:00",
        "platform_version": "pulse-2026.09",
        "dataset_version": "dataset-v1.0.0+qa-pairs-v0.3.0",
    }
    base.update(overrides)
    return RunContext(**base)


def _text(tmp_path: Path, results: list, ctx: RunContext, **kwargs) -> str:
    pypdf = pytest.importorskip("pypdf")
    path = write_scorecard_pdf(tmp_path, results, ctx, **kwargs)
    return "\n".join(
        page.extract_text() for page in pypdf.PdfReader(str(path)).pages
    )


# --- the bug: snake_case identifiers were being mangled -----------------------


def test_field_names_keep_their_underscores(tmp_path: Path) -> None:
    """`.replace("_", "")` stripped markdown italics and identifiers alike.

    The provenance block printed `runid`, `platformversion`, `datasetversion`.
    A provenance block whose field names are wrong is worse than none.
    """

    text = _text(tmp_path, _results(passes=5, fails=5), _ctx())

    assert "run_id" in text
    assert "platform_version" in text
    assert "dataset_version" in text
    assert "run_timestamp_iso" in text
    assert "runid" not in text
    assert "platformversion" not in text
    assert "datasetversion" not in text


def test_provenance_is_built_as_data_not_scraped_prose() -> None:
    labels = [label for label, _ in _provenance(_ctx())]

    assert labels[:4] == [
        "run_id",
        "run_timestamp_iso",
        "platform_version",
        "dataset_version",
    ]


def test_absent_versions_say_so_rather_than_printing_a_dash() -> None:
    values = dict(_provenance(_ctx(platform_version="", dataset_version="")))

    assert values["platform_version"] == "not supplied"
    assert values["dataset_version"] == "not supplied"


# --- the headline: the number against the gate --------------------------------


def test_a_failing_run_says_below_gate_on_its_face(tmp_path: Path) -> None:
    """The headline is a stat tile, not a table row — assert the verdict and the
    denominator reach the first screen, not the shape they arrive in."""

    text = _text(tmp_path, _results(passes=88, fails=72), _ctx())

    assert "BELOW GATE" in text
    assert "88 of 160" in text
    assert f"{ACCURACY_GATE_PCT:.0f}%" in text  # the gate is stated, not implied


def test_a_passing_run_says_it_meets_the_gate(tmp_path: Path) -> None:
    text = _text(tmp_path, _results(passes=96, fails=4), _ctx())

    assert "MEETS GATE" in text
    assert "BELOW GATE" not in text


# --- warnings -----------------------------------------------------------------


def test_below_the_gate_raises_a_warning() -> None:
    rows, comparisons = build_summary_rows(_results(passes=1, fails=1), _ctx())

    alerts = _warnings(_ctx(), rows, comparisons)

    assert any("ACCURACY GATE" in a and "Section 14.2" in a for a in alerts)


def test_at_the_gate_raises_no_accuracy_warning() -> None:
    results = _results(passes=96, fails=4)  # 96%
    ctx = _ctx(scorecard_mode="RELEASE", calibrated=True)
    rows, comparisons = build_summary_rows(results, ctx)

    alerts = _warnings(ctx, rows, comparisons)

    assert not any("ACCURACY GATE" in a for a in alerts)
    # A calibrated RELEASE run with nothing wrong should be quiet.
    assert alerts == []


def test_clarifications_are_flagged_as_a_denominator_exclusion() -> None:
    results = _results(passes=5, fails=5, clarifications=3)
    rows, comparisons = build_summary_rows(results, _ctx())

    alerts = _warnings(_ctx(), rows, comparisons)

    exclusion = next(a for a in alerts if "declined" in a)
    assert "EXCLUDED" in exclusion
    assert "Section 14.2 condition 4" in exclusion
    assert "3 question(s)" in exclusion


def test_preview_is_called_out_and_calibration_is_not() -> None:
    """Project decision: calibration is reported only when it PASSED, so an
    uncalibrated run is silent on the subject instead of carrying a negative
    notice. PREVIEW is unrelated and still called out."""

    rows, comparisons = build_summary_rows(_results(passes=5, fails=5), _ctx())

    alerts = _warnings(
        _ctx(scorecard_mode="PREVIEW", calibrated=False), rows, comparisons
    )

    assert any("PREVIEW" in a for a in alerts)
    assert not any("UNCALIBRATED" in a.upper() for a in alerts)


def test_a_non_default_comparison_is_called_out() -> None:
    ctx = _ctx(comparison_is_default=False, scorecard_mode="RELEASE", calibrated=True)
    rows, comparisons = build_summary_rows(_results(passes=96, fails=4), ctx)

    assert any("NON-DEFAULT" in a for a in _warnings(ctx, rows, comparisons))


def test_a_merge_note_is_surfaced_not_buried(tmp_path: Path) -> None:
    ctx = _ctx(provenance_note="55 of 160 rows from re-run 20260921T153322Z")

    text = _text(tmp_path, _results(passes=88, fails=72), ctx)

    assert "Assembled: 55 of 160 rows" in text


def test_determinism_failure_is_stated_plainly() -> None:
    values = dict(_provenance(_ctx(judge_seed_enforced=False)))

    assert "seed never reached provider" in values["determinism"]


def test_enforced_determinism_says_so() -> None:
    values = dict(_provenance(_ctx()))

    assert values["determinism"] == "temperature 0 + seed enforced"


# --- structure ----------------------------------------------------------------


def test_the_two_scores_are_split_into_labelled_sections(tmp_path: Path) -> None:
    """18 columns at 7.5pt was complete and unreadable. §11.3 wants them apart."""

    text = _text(tmp_path, _results(passes=5, fails=5), _ctx())

    assert "Deterministic accuracy" in text
    assert "Judge quality" in text
    # ...and never blended into one number.
    assert "never" in text and "composite" in text


def test_the_pdf_is_written_even_with_a_single_result(tmp_path: Path) -> None:
    path = write_scorecard_pdf(tmp_path, _results(passes=1, fails=0), _ctx())

    assert path.name == "scorecard.pdf"
    assert path.stat().st_size > 1000


# --- §14.2 gate checklist -----------------------------------------------------
# --- it all lands in the document --------------------------------------------


def test_the_new_sections_reach_the_pdf(tmp_path: Path) -> None:
    results = _results(passes=20, fails=12, clarifications=0)
    text = _text(tmp_path, results, _ctx())

    assert "Platform findings" in text
    assert "Answer outcomes" in text
    assert "Judge dimensions" in text
    assert "Exact-match by tier" in text
    assert "95% gate" in text
    assert "Where the failures are" in text
    # The card measures the PLATFORM. Our own delivery readiness — an
    # independent reviewer, clean-room regeneration, pair counts against the
    # §9.1 quota — belongs in the §14.1 QA audit report, a separate artefact.
    for ours in ("Independent reviewer", "Clean-room", "§9.1 quota",
                 "Cross-domain question uniqueness"):
        assert ours not in text, f"framework self-assessment leaked in: {ours}"


# --- fonts: the §-renders-as-a-box class of bug --------------------------------


def _text_of(path: Path) -> str:
    pypdf = pytest.importorskip("pypdf")
    return "\n".join(p.extract_text() for p in pypdf.PdfReader(str(path)).pages)


def _pdf_fonts_used(path: Path) -> set[str]:
    """Faces the content streams actually SELECT, not merely declare.

    ReportLab lists fonts in a page's resource dictionary whether or not any text
    uses them, so the resource dict alone proves nothing.
    """

    import re

    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(path))
    used: set[str] = set()
    for page in reader.pages:
        fonts = (page.get("/Resources", {}) or {}).get("/Font") or {}
        lookup = {k: str(v.get_object().get("/BaseFont")) for k, v in fonts.items()}
        # The RAW /Contents stream, not ContentStream.get_data() — the latter
        # reconstructs the stream and drops the Tf operators we are looking for.
        contents = page.get("/Contents")
        streams = contents if isinstance(contents, list) else [contents]
        for stream in streams:
            if stream is None:
                continue
            data = stream.get_object().get_data().decode("latin-1", "ignore")
            for ref in re.findall(r"(/[A-Za-z0-9#+_.-]+)\s+[\d.]+\s+Tf", data):
                used.add(lookup.get(ref, ref))
    return used


def test_every_glyph_comes_from_an_embedded_font(tmp_path: Path) -> None:
    """The standard-14 faces are NOT embedded: the viewer substitutes a local
    font, and a substitute missing a glyph draws a box. That is how `§` — an
    ordinary WinAnsi character — came out broken on another machine while
    extracting cleanly here. Every face the document draws with must be embedded.
    """

    out = tmp_path / "card"
    results = _results(passes=20, fails=12, clarifications=2)
    report.write_scorecard_pdf(out, results, _ctx())

    used = _pdf_fonts_used(out / "scorecard.pdf")
    assert used, "no font selections found — the probe is broken, not the PDF"
    not_embedded = sorted(f for f in used if "Vera" not in f)
    assert not not_embedded, f"non-embedded faces in use: {not_embedded}"


def test_the_pdf_contains_no_missing_glyph_boxes(tmp_path: Path) -> None:
    """U+25CF and U+26A0 are absent from the embedded face and used to render as
    black boxes. Nothing in the document may rely on a glyph the font lacks."""

    out = tmp_path / "card"
    report.write_scorecard_pdf(out, _results(passes=20, fails=12), _ctx())
    text = _text_of(out / "scorecard.pdf")

    for box in ("■", "□", "�"):
        assert box not in text, f"missing-glyph box {box!r} rendered"
    # The document no longer depends on a special character at all: the section
    # sign was correct, embedded, and STILL read as a box in a viewer, so the
    # references are spelled out. A stakeholder report is the wrong place to be
    # defending a glyph.
    assert "§" not in text, "the section sign is back; spell it out instead"
    assert "Section 14.2" in text


def _page_geometry(path: Path) -> tuple[tuple[float, float] | None, list[float]]:
    """Title baseline/size and horizontal rule positions, in PAGE coordinates.

    The transform tracking is the point: flowables draw inside nested q/cm
    blocks, so comparing raw y values across them is meaningless — an earlier
    version of this probe "measured" a 0.43pt gap between two elements that were
    in different frames entirely.
    """

    import re

    pypdf = pytest.importorskip("pypdf")
    page = pypdf.PdfReader(str(path)).pages[0]
    contents = page.get("/Contents")
    stream = contents if not isinstance(contents, list) else contents[0]
    data = stream.get_object().get_data().decode("latin-1", "ignore")

    tokens = re.findall(
        r"(q|Q|[-\d.]+ [-\d.]+ [-\d.]+ [-\d.]+ [-\d.]+ [-\d.]+ cm|"
        r"BT 1 0 0 1 [-\d.]+ [-\d.]+ Tm /\S+ [\d.]+ Tf|"
        r"n [-\d.]+ [-\d.]+ m [-\d.]+ [-\d.]+ l S)",
        data,
    )
    stack = [0.0]
    title = None
    rules: list[float] = []
    for token in tokens:
        if token == "q":
            stack.append(stack[-1])
        elif token == "Q":
            stack.pop()
        elif token.endswith("cm"):
            stack[-1] += float(token.split()[5])
        elif token.startswith("BT"):
            m = re.match(r"BT 1 0 0 1 [-\d.]+ ([-\d.]+) Tm /\S+ ([\d.]+) Tf", token)
            y, size = float(m.group(1)) + stack[-1], float(m.group(2))
            if size >= 16 and title is None:
                title = (y, size)
        else:
            m = re.match(r"n [-\d.]+ ([-\d.]+) m [-\d.]+ ([-\d.]+) l S", token)
            y1, y2 = float(m.group(1)) + stack[-1], float(m.group(2)) + stack[-1]
            if abs(y1 - y2) < 0.01:
                rules.append(y1)
    return title, rules


def test_the_masthead_rule_clears_the_title_descenders(tmp_path: Path) -> None:
    """The rule used to cut through the title. Setting fontSize inline with
    <font size=17> left the style's 12pt leading in place, so the line box was
    shorter than the glyphs and a cell-edge LINEBELOW landed on them — and
    BOTTOMPADDING could not fix it, because the leading absorbed the padding.
    The rule is a separate flowable now, so the distance is set, not inferred.
    """

    out = tmp_path / "card"
    report.write_scorecard_pdf(out, _results(passes=20, fails=12), _ctx())

    title, rules = _page_geometry(out / "scorecard.pdf")
    assert title is not None, "no title-sized text found on page 1"
    baseline, size = title
    below = [y for y in rules if y < baseline]
    assert below, "no rule beneath the title"

    # Vera's descender is ~21% of the em.
    clearance = (baseline - size * 0.21) - max(below)
    assert clearance > 1.5, f"rule is {clearance:.2f}pt from the descenders"


def test_table_header_text_is_light_on_the_dark_header_fill(tmp_path: Path) -> None:
    """A Paragraph carries its own colour, so a TableStyle TEXTCOLOR on the header
    row does not reach inside one. Converting the findings header from raw strings
    to Paragraphs silently turned it black on a near-black fill — legible to
    nobody, and invisible to every test that only checked the text was present.
    """

    import re

    pypdf = pytest.importorskip("pypdf")
    out = tmp_path / "card"
    report.write_scorecard_pdf(out, _results(passes=20, fails=12, clarifications=2), _ctx())

    headers = {"what this run observed about the platform", "verdict", "basis"}
    seen: dict[str, tuple[float, float, float] | None] = {}
    for page in pypdf.PdfReader(str(out / "scorecard.pdf")).pages:
        contents = page.get("/Contents")
        stream = contents if not isinstance(contents, list) else contents[0]
        data = stream.get_object().get_data().decode("latin-1", "ignore")
        fill = None
        for m in re.finditer(r"([\d.]+) ([\d.]+) ([\d.]+) rg|\((.*?)\) Tj", data):
            if m.group(1) is not None:
                fill = tuple(round(float(m.group(i)), 3) for i in (1, 2, 3))
            elif (m.group(4) or "").strip() in headers:
                seen[m.group(4).strip()] = fill

    assert seen, "findings table header not found in the document"
    for label, fill in seen.items():
        assert fill is not None, f"{label!r} drawn with no fill colour set"
        # The header fill is #22304a — dark. Header text must be light.
        assert sum(fill) > 2.0, f"{label!r} drawn dark ({fill}) on a dark header"
