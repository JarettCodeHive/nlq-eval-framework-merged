"""Human-readable scorecard renders — Markdown (PR inline) and PDF (§11.3).

Same aggregation as ``scorecard.summary``; exact-match and judge scores are
shown side by side and never blended (§11.3).
"""

from __future__ import annotations

from pathlib import Path

from scorecard.summary import (
    JUDGE_COLUMNS,
    RunContext,
    Result,
    build_summary_rows,
)

_DISPLAY_COLUMNS: list[str] = [
    "domain",
    "tier",
    "questions_total",
    "exact_match_pass",
    "exact_match_pct",
    "exact_match_not_applicable",
    *JUDGE_COLUMNS.keys(),
    "judge_overall",
    "judge_errors",
    "platform_errors",
    "null_handling_fail",
    "baseline_exact_match_pct",
    "delta_pct",
    "regression_flag",
]


def _fmt(value: object) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "YES" if value else "no"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _header_lines(ctx: RunContext, comparisons: dict) -> list[str]:
    flagged = sorted(d for d, c in comparisons.items() if c.regression_flag)
    lines = [
        f"run_id: `{ctx.run_id}`  ·  {ctx.run_timestamp_iso}",
        f"mode: **{ctx.scorecard_mode}**  ·  calibrated: **{ctx.calibrated}**",
        f"platform_version: `{ctx.platform_version or '—'}`  ·  "
        f"dataset_version: `{ctx.dataset_version or '—'}`",
    ]
    if ctx.scorecard_mode != "RELEASE":
        lines.append(
            "_PREVIEW run — not an official evaluation. Does not establish or "
            "update a baseline and must not certify a release (§10.2, §11.3)._"
        )
    if not ctx.judge_temperature_enforced:
        lines.append(
            "**judge determinism: temperature 0 was NOT enforced** — the model "
            "refused it; this run rests on the fixed seed (§10.1)"
        )
    if not ctx.judge_seed_enforced:
        lines.append(
            "**judge determinism: the configured seed never reached the provider** "
            "— see `judge_seed_enforced` (§10.1 'where supported')"
        )
    lines.append(f"exact-match comparison: `{ctx.comparison_policy}`")
    if not ctx.comparison_is_default:
        lines.append(
            "**exact-match ran under a NON-DEFAULT comparison** — this run relaxes "
            "the declared policy and cannot establish a baseline (OI-2 / OI-3)"
        )
    for finding in ctx.rephrase_findings:
        lines.append(f"**⚠ rephrase-group finding (§9.5): {finding}**")
    if flagged:
        lines.append(
            f"**⚠ REGRESSION FLAG: {', '.join(flagged)}** (domain drop ≥ 5 pp vs baseline)"
        )
    return lines


def write_scorecard_md(
    out_dir: Path,
    results: list[Result],
    ctx: RunContext,
    *,
    baseline: dict | None = None,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "scorecard.md"
    rows, comparisons = build_summary_rows(results, ctx, baseline=baseline)

    lines: list[str] = [f"# Regression scorecard — `{ctx.run_id}`", ""]
    lines += [f"- {line}" for line in _header_lines(ctx, comparisons)]
    lines += ["", "## Summary — per domain × tier", ""]
    lines.append("| " + " | ".join(_DISPLAY_COLUMNS) + " |")
    lines.append("| " + " | ".join("---" for _ in _DISPLAY_COLUMNS) + " |")
    for row in rows:
        lines.append(
            "| " + " | ".join(_fmt(row.get(c)) for c in _DISPLAY_COLUMNS) + " |"
        )
    lines += [
        "",
        "_Exact-match (§HC-3, zero numeric tolerance) and judge scores (1–5) are "
        "reported side by side and never combined into a composite (§11.3). "
        "Baseline comparison and the regression flag are domain-level; tier rows "
        "are diagnostic. `exact_match_not_applicable` counts pairs with no "
        "deterministic core — they are outside the percentage, so a rise there is "
        "not a rise in accuracy (§14.2 condition 4). `null_handling_fail` is the "
        "§9.2 T3 diagnostic and is never part of either score._",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_scorecard_pdf(
    out_dir: Path,
    results: list[Result],
    ctx: RunContext,
    *,
    baseline: dict | None = None,
) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "scorecard.pdf"
    rows, comparisons = build_summary_rows(results, ctx, baseline=baseline)

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(
        str(path),
        pagesize=landscape(letter),
        leftMargin=0.4 * inch,
        rightMargin=0.4 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
        title=f"Regression scorecard — {ctx.run_id}",
    )

    story: list = [
        Paragraph(f"Regression scorecard — <b>{ctx.run_id}</b>", styles["Title"])
    ]
    story.append(Spacer(1, 0.12 * inch))
    for line in _header_lines(ctx, comparisons):
        story.append(
            Paragraph(
                line.replace("`", "").replace("**", "").replace("_", ""),
                styles["BodyText"],
            )
        )
    story.append(Spacer(1, 0.18 * inch))

    table_data: list[list[str]] = [_DISPLAY_COLUMNS]
    for row in rows:
        table_data.append([_fmt(row.get(c)) for c in _DISPLAY_COLUMNS])

    table = Table(table_data, repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#22304a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    flag_col = _DISPLAY_COLUMNS.index("regression_flag")
    for r, row in enumerate(rows, start=1):
        if row.get("regression_flag"):
            style.append(("TEXTCOLOR", (flag_col, r), (flag_col, r), colors.red))
            style.append(("FONTNAME", (flag_col, r), (flag_col, r), "Helvetica-Bold"))
    table.setStyle(TableStyle(style))
    story.append(table)
    story.append(Spacer(1, 0.18 * inch))
    story.append(
        Paragraph(
            "<i>Exact-match (zero numeric tolerance) and judge scores (1–5) are "
            "reported side by side and never combined into a composite (§11.3). "
            "Baseline comparison and the regression flag are domain-level.</i>",
            styles["BodyText"],
        )
    )
    doc.build(story)
    return path
