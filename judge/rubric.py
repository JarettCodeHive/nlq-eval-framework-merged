"""Render the LLM-as-Judge scoring rubric as a standalone PDF.

Execution Spec §14.1 lists a delivered **rubric PDF** as part of the judge
module's Definition of Done, and `_rubric.jinja` calls out that the interpolated
score levels 2 and 4 "must be signed off in the rubric PDF (Deliverable 5)".

The PDF is rendered from the *same* Jinja templates the judge runs, so it can
never drift from the calibrated prompt. The `prompt_version` hash is stamped on
the cover page — a sign-off is against that exact prompt revision.

    python -m judge.rubric                 # writes docs/judge_rubric.pdf
    python -m judge.rubric --out /tmp/r.pdf
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from judge.contracts import DIMENSIONS
from judge.prompts import (
    DIMENSION_LABELS,
    prompt_version,
    render_static as _template_text,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "docs" / "judge_rubric.pdf"

# §10 dimension table — "what the judge assesses" + the verbatim 5/3/1 anchors.
_DIMENSION_TABLE: list[tuple[str, str, str]] = [
    (
        "Factual correctness",
        "Does the answer match the expected answer in substance? "
        "Numeric values must be exact.",
        "5 = exact · 3 = partial · 1 = wrong",
    ),
    (
        "Completeness",
        "Does the response include all required values and context from the "
        "Q&A pair?",
        "5 = complete · 3 = missing fields · 1 = severely incomplete",
    ),
    (
        "Format adherence",
        "Is the answer in the expected format? Dashboard styling variation is "
        "explicitly not a failure.",
        "5 = correct · 3 = minor deviation · 1 = wrong format",
    ),
    (
        "SQL plausibility",
        "Is the generated SQL syntactically valid and logically coherent for "
        "the question?",
        "5 = valid & correct · 3 = valid but suboptimal · 1 = invalid",
    ),
]


def _settings_lines() -> list[str]:
    return [
        "Temperature is fixed at 0; a fixed seed is sent wherever the provider "
        "supports it. A temperature-0 rejection fails the run — a "
        "non-deterministic judge cannot certify determinism (§10.1).",
        "The judge receives the question, the platform response, the "
        "expected_answer and the judge_reference. It never receives the "
        "reference_sql — SQL plausibility is scored on the platform's own "
        "generated SQL (§10.1).",
        "Structured output is schema-validated: four integer scores (1–5, "
        "never coerced from strings or floats) plus one rationale per "
        "dimension. Malformed output is retried, then failed loudly (§10.1).",
        "The full prompt and full raw response are logged for every scored "
        "item under a stable judge_run_id, including cache hits (§10.1).",
        "Calibration must pass before any production scoring run: within ±1 of "
        "the human anchor score on ≥90% of anchors per dimension, with no "
        "directional disagreement, over ≥10 anchors per domain (§10.2). "
        "Uncalibrated scores never enter a scorecard (§10.3).",
    ]


def build_rubric_pdf(out_path: Path) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        HRFlowable,
        ListFlowable,
        ListItem,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
        XPreformatted,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    body = styles["BodyText"]
    h2 = ParagraphStyle("H2x", parent=styles["Heading2"], spaceBefore=10, spaceAfter=4)
    mono = ParagraphStyle(
        "Mono",
        parent=styles["Code"],
        fontSize=7.5,
        leading=9.5,
        alignment=TA_LEFT,
    )

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="LLM-as-Judge Scoring Rubric",
        author="Codehive — NLQ Evaluation Framework",
    )

    story: list = []
    story.append(Paragraph("LLM-as-Judge Scoring Rubric", styles["Title"]))
    story.append(Paragraph("NLQ Evaluation Framework — Deliverable 5 (§14.1)", h2))
    story.append(Spacer(1, 4 * mm))
    story.append(
        Paragraph(
            f"Prompt revision: <b>{prompt_version()}</b> &nbsp;·&nbsp; "
            f"generated {date.today().isoformat()}",
            body,
        )
    )
    story.append(Spacer(1, 3 * mm))
    story.append(
        Paragraph(
            "This rubric is rendered directly from the Jinja templates the judge "
            "executes, so it cannot drift from the calibrated prompt. Sign-off is "
            "against the prompt revision named above.",
            body,
        )
    )
    story.append(Spacer(1, 3 * mm))
    story.append(
        Paragraph(
            "<b>Purpose.</b> The judge captures answer quality that exact-match "
            "cannot measure — completeness, coherence, contextual soundness — "
            "without penalising the phrasing, length, tone, or dashboard-styling "
            "variation the client has told us to expect (§10, §HC-3). "
            "Exact-match accuracy and judge scores are always reported side by "
            "side and never blended into a composite (§11.3).",
            body,
        )
    )

    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph("1. The four dimensions", h2))
    table_data = [["Dimension", "What the judge assesses", "Anchor scale"]]
    table_data += [
        [Paragraph(f"<b>{n}</b>", body), Paragraph(desc, body), Paragraph(scale, body)]
        for n, desc, scale in _DIMENSION_TABLE
    ]
    dim_table = Table(table_data, colWidths=[32 * mm, 90 * mm, 52 * mm], repeatRows=1)
    dim_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#22304a")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(dim_table)

    story.append(Spacer(1, 5 * mm))
    story.append(
        Paragraph(
            "2. Full 1–5 scale "
            "<font size=8>(levels 2 and 4 require Platform Owner sign-off)</font>",
            h2,
        )
    )
    story.append(
        Paragraph(
            "Score anchors 5, 3 and 1 are verbatim from the Execution Scope. "
            "Levels 2 and 4 are interpolated by Codehive so the judge has a "
            "continuous scale to reason toward. <b>This interpolation is the item "
            "requiring sign-off on this document.</b>",
            body,
        )
    )
    story.append(Spacer(1, 2 * mm))
    story.append(XPreformatted(_template_text("judge/_rubric.jinja"), mono))

    story.append(PageBreak())
    story.append(
        Paragraph("3. Non-determinism boundary and worked examples (§HC-3, §10.2)", h2)
    )
    story.append(
        Paragraph(
            "Encoded verbatim in every judge prompt. The worked examples are "
            "load-bearing: without them the model over-penalises phrasing "
            "variation, which corrupts every downstream regression comparison.",
            body,
        )
    )
    story.append(Spacer(1, 2 * mm))
    story.append(
        XPreformatted(_template_text("judge/_non_determinism_boundary.jinja"), mono)
    )

    story.append(PageBreak())
    story.append(Paragraph("4. Per-dimension scoped instructions", h2))
    story.append(
        Paragraph(
            "In <i>per_dimension</i> mode each dimension is scored in its own "
            "prompt to remove the halo effect. These are the scoped instruction "
            "blocks.",
            body,
        )
    )
    for dim in DIMENSIONS:
        story.append(Spacer(1, 3 * mm))
        story.append(
            Paragraph(f"4.{DIMENSIONS.index(dim) + 1} {DIMENSION_LABELS[dim]}", body)
        )
        story.append(Spacer(1, 1 * mm))
        story.append(
            XPreformatted(_template_text(f"judge/dimensions/{dim}.jinja"), mono)
        )

    story.append(PageBreak())
    story.append(Paragraph("5. Determinism, structured output and audit settings", h2))
    story.append(
        ListFlowable(
            [
                ListItem(Paragraph(line, body), leftIndent=6)
                for line in _settings_lines()
            ],
            bulletType="bullet",
            start="•",
        )
    )

    story.append(Spacer(1, 10 * mm))
    story.append(HRFlowable(width="100%", color=colors.grey))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("Sign-off", h2))
    story.append(
        Paragraph(
            f"Approved for calibration and production scoring against prompt "
            f"revision <b>{prompt_version()}</b>, including the interpolated "
            f"score levels 2 and 4.",
            body,
        )
    )
    story.append(Spacer(1, 8 * mm))
    signoff = Table(
        [
            ["Platform Owner", "", "Date", ""],
            ["Codehive lead", "", "Date", ""],
        ],
        colWidths=[32 * mm, 62 * mm, 16 * mm, 40 * mm],
    )
    signoff.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("LINEBELOW", (1, 0), (1, -1), 0.5, colors.black),
                ("LINEBELOW", (3, 0), (3, -1), 0.5, colors.black),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    story.append(signoff)

    doc.build(story)
    return out_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="judge.rubric", description=__doc__)
    ap.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"output PDF path (default: {DEFAULT_OUT.relative_to(REPO_ROOT)})",
    )
    args = ap.parse_args(argv)
    try:
        path = build_rubric_pdf(args.out)
    except ImportError as exc:  # reportlab missing
        print(f"[rubric] cannot render PDF — {exc}", file=sys.stderr)
        return 2
    print(f"[rubric] wrote {path}  (prompt revision {prompt_version()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
