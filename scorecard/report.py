"""Human-readable scorecard renders — Markdown (PR inline) and PDF (§11.3).

Same aggregation as ``scorecard.summary``; exact-match and judge scores are
shown side by side and never blended (§11.3).
"""

from __future__ import annotations

from pathlib import Path

from judge.contracts import CLARIFICATION_SCORE
from judge.exact_match import ExactMatchResult
from scorecard.summary import (
    JUDGE_COLUMNS,
    RunContext,
    Result,
    build_summary_rows,
)

# §14.2 condition 1: the package does not ship below this. The PDF colours
# against it so a reader sees pass/fail rather than a number needing context.
ACCURACY_GATE_PCT: float = 95.0

# §9.1 tier quota per domain. Rendered against the actual counts because §14.2's
# last line asserts them in CI, and condition 4 — the one the scope says "gets
# quietly broken" — is precisely a pair disappearing from a tier.
TIER_QUOTA: dict[str, int] = {"T1": 32, "T2": 40, "T3": 32, "T4": 32, "T5": 24}

# Status palette, taken as given from a validated reference instance rather than
# chosen by eye. Light-surface contrast is 3.27 / 1.79 / 2.57 / 4.68 — warning
# and serious are sub-3:1 BY DESIGN, and the stated mitigation is that a status
# colour never carries meaning alone. Every use below is paired with the number
# and a verdict word, so hue is redundant.
_STATUS_GOOD = "#0ca30c"
_STATUS_WARNING = "#fab219"
_STATUS_SERIOUS = "#ec835a"
_STATUS_CRITICAL = "#d03b3b"
# Chart surface and ink, from the same instance.
_SURFACE = "#fcfcfb"
_INK = "#0b0b0b"
_INK_MUTED = "#52514e"


def _status_fill(pct: object) -> str:
    """Status role for an accuracy figure, banded against the §14.2 gate."""

    if not isinstance(pct, (int, float)):
        return _INK_MUTED
    if pct >= ACCURACY_GATE_PCT:
        return _STATUS_GOOD
    if pct >= 75:
        return _STATUS_WARNING
    if pct >= 50:
        return _STATUS_SERIOUS
    return _STATUS_CRITICAL

# The single wide table was 18 columns at 7.5pt — technically complete and
# practically unreadable. The PDF splits it the way §11.3 says the two scores
# are used: deterministic accuracy and judge quality, side by side, never
# blended. The CSV keeps every §11.1 column in one row for machines.
_EXACT_COLUMNS: list[str] = [
    "domain",
    "tier",
    "questions_total",
    "exact_match_pass",
    "exact_match_pct",
    "exact_match_not_applicable",
    "exact_match_clarification",
    "baseline_exact_match_pct",
    "delta_pct",
    "regression_flag",
]

_JUDGE_TABLE_COLUMNS: list[str] = [
    "domain",
    "tier",
    *JUDGE_COLUMNS.keys(),
    "judge_overall",
    "judge_clarifications",
    "judge_errors",
    "platform_errors",
    "null_handling_fail",
]

# Column header -> the short label the PDF prints, so a 10-column table fits
# without shrinking the font to unreadable.
_HEADER_LABELS: dict[str, str] = {
    "domain": "domain",
    "tier": "tier",
    "questions_total": "questions",
    "exact_match_pass": "pass",
    "exact_match_pct": "exact-match %",
    "exact_match_not_applicable": "n/a",
    "exact_match_clarification": "clarified",
    "baseline_exact_match_pct": "baseline %",
    "delta_pct": "delta pp",
    "regression_flag": "regression",
    "judge_factual": "factual",
    "judge_completeness": "complete",
    "judge_format": "format",
    "judge_sql": "sql",
    "judge_overall": "overall",
    "judge_clarifications": "clarified",
    "judge_errors": "judge err",
    "platform_errors": "platform err",
    "null_handling_fail": "null-handling",
}

_DISPLAY_COLUMNS: list[str] = [
    "domain",
    "tier",
    "questions_total",
    "exact_match_pass",
    "exact_match_pct",
    "exact_match_not_applicable",
    "exact_match_clarification",
    *JUDGE_COLUMNS.keys(),
    "judge_overall",
    "judge_clarifications",
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
    if ctx.provenance_note:
        lines.append(f"assembled: {ctx.provenance_note}")
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
        "not a rise in accuracy (§14.2 condition 4). `exact_match_clarification` "
        "counts questions the platform declined to answer, asking a clarifying "
        "question instead (§2e); they are outside the percentage for the same "
        f"reason, and are scored at a fixed {CLARIFICATION_SCORE} with no "
        "dimensions — which is why `judge_overall` is a mean over "
        "`judge_clarifications` more rows than the dimension columns are. "
        "`null_handling_fail` is the "
        "§9.2 T3 diagnostic and is never part of either score._",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _provenance(ctx: RunContext) -> list[tuple[str, str]]:
    """The run's identity, as label/value pairs.

    Built as data rather than reused from the Markdown header, because that path
    stripped markdown emphasis with `.replace("_", "")` and silently mangled
    every snake_case identifier with it — `run_id` printed as `runid`,
    `platform_version` as `platformversion`. A provenance block whose field
    names are wrong is worse than no provenance block.
    """

    return [
        ("run_id", ctx.run_id),
        ("run_timestamp_iso", ctx.run_timestamp_iso),
        ("platform_version", ctx.platform_version or "not supplied"),
        ("dataset_version", ctx.dataset_version or "not supplied"),
        ("scorecard_mode", ctx.scorecard_mode),
        ("judge calibrated", "yes" if ctx.calibrated else "NO"),
        ("exact-match comparison", ctx.comparison_policy),
        (
            "determinism",
            "temperature 0 + seed enforced"
            if ctx.judge_temperature_enforced and ctx.judge_seed_enforced
            else ", ".join(
                filter(
                    None,
                    [
                        ""
                        if ctx.judge_temperature_enforced
                        else "temperature 0 NOT enforced",
                        "" if ctx.judge_seed_enforced else "seed never reached provider",
                    ],
                )
            ),
        ),
    ]


def _warnings(ctx: RunContext, rows: list[dict], comparisons: dict) -> list[str]:
    """Everything a reader must not miss, gathered in one place.

    Previously these were interleaved with provenance as ordinary body text, so
    a regression flag and a timestamp had the same visual weight.
    """

    out: list[str] = []
    flagged = sorted(d for d, c in comparisons.items() if c.regression_flag)
    if flagged:
        out.append(
            f"REGRESSION: {', '.join(flagged)} dropped 5 pp or more against "
            "baseline (§11.3)."
        )

    for row in rows:
        if row["tier"] != "ALL":
            continue
        pct = row.get("exact_match_pct")
        if isinstance(pct, (int, float)) and pct < ACCURACY_GATE_PCT:
            out.append(
                f"ACCURACY GATE: {row['domain']} is at {pct:.2f}% against the "
                f"{ACCURACY_GATE_PCT:.0f}% required before the package ships "
                "(§14.2 condition 1)."
            )
        clarified = row.get("exact_match_clarification") or 0
        if clarified:
            out.append(
                f"{row['domain']}: {clarified} question(s) were declined by the "
                "platform, scored at a fixed "
                f"{CLARIFICATION_SCORE} and EXCLUDED from the exact-match "
                "denominator — so the percentage covers fewer questions than were "
                "asked (§14.2 condition 4)."
            )

    if ctx.scorecard_mode != "RELEASE":
        out.append(
            "PREVIEW run — not an official evaluation. It neither establishes nor "
            "updates a baseline and cannot certify a release (§10.2, §11.3)."
        )
    if not ctx.calibrated:
        out.append(
            "The judge is UNCALIBRATED. These scores are diagnostic only and must "
            "not feed a release decision (§10.2)."
        )
    if not ctx.comparison_is_default:
        out.append(
            "Exact-match ran under a NON-DEFAULT comparison policy; this run "
            "cannot establish a baseline (OI-2 / OI-3)."
        )
    for finding in ctx.rephrase_findings:
        out.append(f"Rephrase-group finding (§9.5): {finding}")
    if ctx.provenance_note:
        out.append(f"Assembled: {ctx.provenance_note}")
    return out


def _pct_colour(value: object):
    """Red / amber / green against the §14.2 gate."""

    from reportlab.lib import colors

    if not isinstance(value, (int, float)):
        return None
    if value >= ACCURACY_GATE_PCT:
        return colors.HexColor("#1b7f3b")
    if value >= 50:
        return colors.HexColor("#9a6700")
    return colors.HexColor("#b42318")


def _table(rows: list[dict], columns: list[str], *, highlight_pct: bool):
    """One styled table. Tier rows are indented under their domain roll-up.

    The accuracy figure stays in ink with a small status chip beside it, rather
    than being recoloured itself: values and labels wear text tokens, and a
    coloured mark next to them carries the state. That also keeps the number
    readable where a status hue is deliberately low-contrast.
    """

    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, Table, TableStyle

    cell = ParagraphStyle(
        "cell", fontName="Helvetica", fontSize=8.5, leading=10, alignment=2
    )

    header = [_HEADER_LABELS.get(c, c) for c in columns]
    body: list[list[object]] = [header]
    for row in rows:
        cells: list[object] = []
        for column in columns:
            text = _fmt(row.get(column))
            if column == "tier" and text != "ALL":
                text = f"  {text}"
            if (
                highlight_pct
                and column == "exact_match_pct"
                and isinstance(row.get(column), (int, float))
            ):
                chip = _status_fill(row[column])
                cells.append(
                    Paragraph(
                        f'<font color="{chip}">&#9679;</font>&nbsp;{text}', cell
                    )
                )
            else:
                cells.append(text)
        body.append(cells)

    table = Table(body, repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#22304a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("FONTSIZE", (0, 1), (-1, -1), 8.5),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c9ced6")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for index, row in enumerate(rows, start=1):
        # The domain roll-up is the number people quote; tier rows support it.
        if row["tier"] == "ALL":
            style.append(
                ("BACKGROUND", (0, index), (-1, index), colors.HexColor("#eef1f6"))
            )
            style.append(("FONTNAME", (0, index), (-1, index), "Helvetica-Bold"))
        if row.get("regression_flag") and "regression_flag" in columns:
            column = columns.index("regression_flag")
            style.append(
                ("TEXTCOLOR", (column, index), (column, index), colors.HexColor("#b42318"))
            )
            style.append(
                ("FONTNAME", (column, index), (column, index), "Helvetica-Bold")
            )
    table.setStyle(TableStyle(style))
    return table


def _tier_chart(rows: list[dict], domain: str, *, width: float, height: float):
    """Per-tier accuracy as horizontal bars against the §14.2 gate.

    FORM. The data's job is magnitude against a threshold, for five ordinal
    categories — so: bars, one axis, a rule at the gate. Horizontal because the
    gate then reads as a line the bars must cross, which is what it is.

    COLOUR. Status, not categorical: the hue encodes a state (over the gate,
    under it, badly under it), so it comes from the reserved status palette and
    never from a series slot. Two of those steps are sub-3:1 on this surface by
    design, so every bar also carries its number and its count — hue is
    redundant, never load-bearing. A thin outline keeps a pale fill's shape
    legible in print.

    Single series, so no legend: the section title names it.
    """

    from reportlab.graphics.shapes import Drawing, Line, Rect, String
    from reportlab.lib import colors

    tiers = [r for r in rows if r["domain"] == domain and r["tier"] != "ALL"]
    if not tiers:
        return None

    left, right, top, bottom = 34, 96, 14, 20
    plot_w = width - left - right
    plot_h = height - top - bottom
    bar_h = min(13.0, plot_h / max(1, len(tiers)) - 5)
    gap = (plot_h - bar_h * len(tiers)) / max(1, len(tiers))

    drawing = Drawing(width, height)

    # Recessive grid first, so marks sit on top of it.
    for pct in (0, 25, 50, 75, 100):
        x = left + plot_w * pct / 100.0
        drawing.add(
            Line(x, bottom, x, bottom + plot_h,
                 strokeColor=colors.HexColor("#e6e7e4"), strokeWidth=0.5)
        )
        drawing.add(
            String(x, bottom - 11, f"{pct}",
                   fontName="Helvetica", fontSize=6.5,
                   fillColor=colors.HexColor(_INK_MUTED), textAnchor="middle")
        )

    for index, row in enumerate(reversed(tiers)):
        pct = row.get("exact_match_pct")
        y = bottom + index * (bar_h + gap) + gap / 2
        drawing.add(
            String(left - 6, y + bar_h / 2 - 2.5, str(row["tier"]),
                   fontName="Helvetica-Bold", fontSize=7.5,
                   fillColor=colors.HexColor(_INK), textAnchor="end")
        )
        if not isinstance(pct, (int, float)):
            drawing.add(
                String(left + 4, y + bar_h / 2 - 2.5, "no eligible questions",
                       fontName="Helvetica-Oblique", fontSize=7,
                       fillColor=colors.HexColor(_INK_MUTED))
            )
            continue

        bar_w = max(0.6, plot_w * pct / 100.0)
        fill = colors.HexColor(_status_fill(pct))
        drawing.add(
            Rect(left, y, bar_w, bar_h, rx=2, ry=2, fillColor=fill,
                 strokeColor=fill.clone(), strokeWidth=0.5)
        )
        # Direct label: the number and the count, in ink. Selective by
        # construction — five bars, five labels, no axis clutter needed.
        drawing.add(
            String(left + plot_w + 6, y + bar_h / 2 - 2.5,
                   f"{pct:.2f}%  ({row.get('exact_match_pass')}/"
                   f"{row.get('questions_total')})",
                   fontName="Helvetica", fontSize=7,
                   fillColor=colors.HexColor(_INK))
        )

    gate_x = left + plot_w * ACCURACY_GATE_PCT / 100.0
    drawing.add(
        Line(gate_x, bottom - 3, gate_x, bottom + plot_h + 3,
             strokeColor=colors.HexColor(_INK), strokeWidth=1,
             strokeDashArray=[2, 2])
    )
    drawing.add(
        String(gate_x, bottom + plot_h + 6,
               f"{ACCURACY_GATE_PCT:.0f}% gate (§14.2)",
               fontName="Helvetica-Bold", fontSize=6.5,
               fillColor=colors.HexColor(_INK), textAnchor="middle")
    )
    return drawing


def gate_checklist(
    ctx: RunContext, rows: list[dict], comparisons: dict
) -> list[tuple[str, str, str]]:
    """The §14.2 nine conditions as (condition, verdict, note).

    §14.2 is a ship / do-not-ship instrument, so the scorecard states where the
    run sits against it. Conditions this artefact cannot observe — an
    independent reviewer re-executing every reference_sql, for instance — are
    marked MANUAL rather than quietly omitted or assumed passed.
    """

    domain_rows = [r for r in rows if r["tier"] == "ALL"]
    below = [
        r["domain"]
        for r in domain_rows
        if isinstance(r.get("exact_match_pct"), (int, float))
        and r["exact_match_pct"] < ACCURACY_GATE_PCT
    ]
    excluded = sum(int(r.get("exact_match_clarification") or 0) for r in domain_rows)
    excluded += sum(int(r.get("exact_match_not_applicable") or 0) for r in domain_rows)

    checks: list[tuple[str, str, str]] = [
        (
            f"1. >={ACCURACY_GATE_PCT:.0f}% exact-match, every domain",
            "FAIL" if below else "PASS",
            f"below the gate: {', '.join(below)}" if below else "all domains at or above",
        ),
        (
            "2. Numeric values identical on repeat runs",
            "PASS" if ctx.judge_seed_enforced and ctx.judge_temperature_enforced else "AT RISK",
            "exact-match is deterministic; the judge's own determinism is "
            + ("enforced" if ctx.judge_seed_enforced and ctx.judge_temperature_enforced
               else "NOT fully enforced this run"),
        ),
        ("3. Independent reviewer re-executes every reference_sql", "MANUAL",
         "§15 protocol — not observable from a run"),
        (
            "4. Failing pairs reworked, never removed",
            "REVIEW" if excluded else "PASS",
            f"{excluded} question(s) sit outside the percentage this run — confirm "
            "each is a genuine exclusion" if excluded
            else "no questions excluded from the denominator",
        ),
        ("5. Generated SQL logged for every failure", "PASS",
         "platform_generated_sql is written for every question in question_results.csv"),
        ("6. Cross-domain question uniqueness", "MANUAL",
         "§15 normalise-and-hash across the corpus"),
        (
            "7. Baseline run completed across all domains",
            "PASS" if ctx.scorecard_mode == "RELEASE" else "NOT YET",
            f"this is a {ctx.scorecard_mode} scorecard"
            + ("" if ctx.scorecard_mode == "RELEASE" else "; only a RELEASE run establishes a baseline"),
        ),
        ("8. Clean-room re-run reproduces byte-identical CSVs", "MANUAL",
         "asserted by the dataset pipeline, not by this run"),
        (
            "9. CRM certified before final delivery",
            "PASS" if "crm" not in below and any(r["domain"] == "crm" for r in domain_rows)
            else ("FAIL" if "crm" in below else "NOT COVERED"),
            "crm is above the gate" if "crm" not in below and any(
                r["domain"] == "crm" for r in domain_rows)
            else ("crm is below the gate" if "crm" in below else "no crm rows in this scorecard"),
        ),
    ]
    return checks


def tier_quota_rows(rows: list[dict]) -> list[tuple[str, str, int, int, str]]:
    """Actual questions per tier against the §9.1 quota.

    §14.2's closing line asserts these counts in CI because a tier quietly
    losing a pair raises the percentage without improving anything. Showing the
    comparison here makes that visible to a reader, not just to CI.
    """

    out: list[tuple[str, str, int, int, str]] = []
    for row in rows:
        tier = str(row["tier"])
        if tier == "ALL" or tier not in TIER_QUOTA:
            continue
        actual = int(row.get("questions_total") or 0)
        quota = TIER_QUOTA[tier]
        verdict = "ok" if actual == quota else ("SHORT" if actual < quota else "over")
        out.append((str(row["domain"]), tier, actual, quota, verdict))
    return out


def failure_reasons(
    results: list[Result], limit: int = 6
) -> tuple[int, list[tuple[int, str]]]:
    """(total failures, most common failure KINDS commonest first).

    Answers the question a reader actually has in front of a number like 55%:
    what sort of thing went wrong. Grouping has to be on the kind, not the
    value — `value '5' not present` and `value '0' not present` are one finding
    seen twice, and leaving them apart turned 72 failures into 14 singletons
    that said nothing. Quoted literals are therefore collapsed.

    The full per-question detail, with its platform SQL, stays in
    question_results.csv (§14.2 condition 5); this is only the shape of it.
    """

    import re
    from collections import Counter

    counter: Counter[str] = Counter()
    total = 0
    for _pair, _req, _verdict, outcome in results:
        if outcome.result is not ExactMatchResult.FAIL:
            continue
        total += 1
        detail = (outcome.detail or "").strip()
        if not detail:
            counter["no detail recorded"] += 1
            continue
        kind = detail.split(";")[0].strip()
        # 'value 5' / "2026-04-01" / 4,182.00 -> a placeholder, so the kind groups.
        kind = re.sub(r"""(['"])[^'"]*\1""", "<value>", kind)
        kind = re.sub(r"\b\d[\d,.\-]*\b", "<value>", kind)
        counter[kind[:100] or "no detail recorded"] += 1
    return total, [(count, reason) for reason, count in counter.most_common(limit)]


def write_scorecard_pdf(
    out_dir: Path,
    results: list[Result],
    ctx: RunContext,
    *,
    baseline: dict | None = None,
) -> Path:
    """The §11.3 PDF summary — for a human deciding something, not a data dump.

    Structure follows what a reader needs in order: the headline number against
    the gate, anything alarming, where the run came from, then the detail split
    into deterministic accuracy and judge quality.
    """

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        KeepTogether,
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
    body = ParagraphStyle(
        "body", parent=styles["BodyText"], fontSize=9, leading=12, alignment=TA_LEFT
    )
    warn = ParagraphStyle(
        "warn",
        parent=body,
        textColor=colors.HexColor("#b42318"),
        fontName="Helvetica-Bold",
    )
    section = ParagraphStyle(
        "section",
        parent=styles["Heading3"],
        fontSize=11,
        spaceBefore=10,
        spaceAfter=4,
        textColor=colors.HexColor("#22304a"),
    )

    doc = SimpleDocTemplate(
        str(path),
        pagesize=landscape(letter),
        leftMargin=0.45 * inch,
        rightMargin=0.45 * inch,
        topMargin=0.45 * inch,
        bottomMargin=0.45 * inch,
        title=f"Regression scorecard — {ctx.run_id}",
        author="NLQ Evaluation Framework",
        subject=f"{ctx.scorecard_mode} scorecard for platform {ctx.platform_version or 'unversioned'}",
    )

    story: list = [
        Paragraph(
            f"Regression scorecard <font color='#6b7280'>·</font> "
            f"<font size=12>{ctx.scorecard_mode}</font>",
            styles["Title"],
        ),
        Spacer(1, 0.10 * inch),
    ]

    # --- headline: the number, against the gate ---------------------------
    domain_rows = [r for r in rows if r["tier"] == "ALL"]
    if domain_rows:
        head = [["domain", "exact-match", "of eligible", f"vs {ACCURACY_GATE_PCT:.0f}% gate",
                 "judge overall", "vs baseline"]]
        for row in domain_rows:
            pct = row.get("exact_match_pct")
            gate = (
                "—"
                if not isinstance(pct, (int, float))
                else ("PASS" if pct >= ACCURACY_GATE_PCT else "BELOW GATE")
            )
            delta = row.get("delta_pct")
            head.append(
                [
                    str(row["domain"]),
                    f"{pct:.2f}%" if isinstance(pct, (int, float)) else "—",
                    f"{row.get('exact_match_pass')} of {row.get('questions_total')}",
                    gate,
                    _fmt(row.get("judge_overall")),
                    "no baseline" if delta is None else f"{delta:+.2f} pp",
                ]
            )
        headline = Table(head, hAlign="LEFT")
        hstyle = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#22304a")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 9),
            ("FONTSIZE", (0, 1), (-1, -1), 13),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c9ced6")),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
            ("TOPPADDING", (0, 1), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]
        for index, row in enumerate(domain_rows, start=1):
            colour = _pct_colour(row.get("exact_match_pct"))
            if colour is not None:
                hstyle.append(("TEXTCOLOR", (1, index), (3, index), colour))
        headline.setStyle(TableStyle(hstyle))
        story.append(headline)
        story.append(Spacer(1, 0.14 * inch))

    # --- anything alarming, before the detail -----------------------------
    alerts = _warnings(ctx, rows, comparisons)
    if alerts:
        story.append(Paragraph("Read first", section))
        for alert in alerts:
            story.append(Paragraph(f"• {alert}", warn))
        story.append(Spacer(1, 0.10 * inch))

    # --- provenance, with field names intact ------------------------------
    story.append(Paragraph("Run provenance", section))
    prov = Table(
        [[label, value] for label, value in _provenance(ctx)],
        colWidths=[1.9 * inch, 8.2 * inch],
        hAlign="LEFT",
    )
    prov.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#42506b")),
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#f6f7f9")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    story.append(prov)

    # --- ship / do-not-ship, against §14.2 --------------------------------
    checks = gate_checklist(ctx, rows, comparisons)
    verdict_ink = {
        "PASS": colors.HexColor("#1b7f3b"),
        "FAIL": colors.HexColor("#b42318"),
        "AT RISK": colors.HexColor("#9a6700"),
        "REVIEW": colors.HexColor("#9a6700"),
        "NOT YET": colors.HexColor("#42506b"),
        "MANUAL": colors.HexColor(_INK_MUTED),
        "NOT COVERED": colors.HexColor(_INK_MUTED),
    }
    gate_table = Table(
        [["§14.2 condition", "status", "basis"]]
        + [[c, v, note] for c, v, note in checks],
        colWidths=[3.3 * inch, 0.95 * inch, 5.85 * inch],
        hAlign="LEFT",
        repeatRows=1,
    )
    gate_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#22304a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (1, 1), (1, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c9ced6")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]
    for index, (_c, verdict, _n) in enumerate(checks, start=1):
        gate_style.append(
            ("TEXTCOLOR", (1, index), (1, index),
             verdict_ink.get(verdict, colors.HexColor(_INK)))
        )
    gate_table.setStyle(TableStyle(gate_style))
    story.append(
        KeepTogether(
            [
                Paragraph(
                    "Accuracy gate (§14.2) — all nine before the package ships",
                    section,
                ),
                gate_table,
            ]
        )
    )

    # --- per-tier shape, as a picture -------------------------------------
    for domain in sorted({r["domain"] for r in rows}):
        chart = _tier_chart(rows, domain, width=6.6 * inch, height=1.5 * inch)
        if chart is None:
            continue
        story.append(
            KeepTogether(
                [
                    Paragraph(
                        f"Exact-match by tier — {domain}", section
                    ),
                    chart,
                ]
            )
        )

    # --- §9.1 quota, because condition 4 is about counts ------------------
    quota = tier_quota_rows(rows)
    if quota:
        short = [q for q in quota if q[4] == "SHORT"]
        quota_table = Table(
            [["domain", "tier", "questions", "§9.1 quota", ""]]
            + [[d, t, str(a), str(q), v] for d, t, a, q, v in quota],
            hAlign="LEFT",
            repeatRows=1,
        )
        qstyle = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#22304a")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ALIGN", (2, 1), (3, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c9ced6")),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]
        for index, entry in enumerate(quota, start=1):
            if entry[4] == "SHORT":
                qstyle.append(
                    ("TEXTCOLOR", (0, index), (-1, index), colors.HexColor("#b42318"))
                )
                qstyle.append(
                    ("FONTNAME", (4, index), (4, index), "Helvetica-Bold")
                )
        quota_table.setStyle(TableStyle(qstyle))
        story.append(
            Paragraph("Pair counts against the §9.1 quota", section)
        )
        story.append(quota_table)
        story.append(
            Paragraph(
                "<i>A tier short of quota means a pair left the set. §14.2 "
                "condition 4: failing pairs are reworked, never removed — removing "
                "one raises the percentage without improving anything."
                + ("" if not short else " <b>Short tiers above.</b>")
                + "</i>",
                body,
            )
        )

    # --- what kind of thing failed ----------------------------------------
    total_failures, reasons = failure_reasons(results)
    if reasons:
        reason_table = Table(
            [["count", f"failure kind — {total_failures} failure(s) in total"]]
            + [[str(count), reason] for count, reason in reasons],
            colWidths=[0.8 * inch, 9.3 * inch],
            hAlign="LEFT",
            repeatRows=1,
        )
        reason_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#22304a")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("ALIGN", (0, 1), (0, -1), "RIGHT"),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c9ced6")),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        story.append(
            KeepTogether([Paragraph("Where the failures are", section), reason_table])
        )
        story.append(
            Paragraph(
                "<i>Grouped by kind — quoted and numeric literals are collapsed, so "
                "one finding seen many times reads as one row. Every "
                "failure's full detail and its platform-generated SQL are in "
                "question_results.csv (§14.2 condition 5).</i>",
                body,
            )
        )

    # --- the two scores, side by side but never blended -------------------
    story.append(
        KeepTogether(
            [
                Paragraph("Deterministic accuracy (§HC-3, zero numeric tolerance)", section),
                _table(rows, _EXACT_COLUMNS, highlight_pct=True),
            ]
        )
    )
    story.append(
        KeepTogether(
            [
                Paragraph("Judge quality (1–5 per dimension, §10)", section),
                _table(rows, _JUDGE_TABLE_COLUMNS, highlight_pct=False),
            ]
        )
    )

    story.append(Spacer(1, 0.14 * inch))
    story.append(
        Paragraph(
            "<i>Exact-match and judge scores are reported side by side and never "
            "combined into a composite (§11.3) — they measure different things. "
            "Baseline comparison and the regression flag are domain-level; tier "
            "rows are diagnostic. <b>n/a</b> counts pairs with no deterministic "
            "core and <b>clarified</b> counts questions the platform declined to "
            "answer; both sit outside the percentage, so a rise in either is not a "
            "rise in accuracy (§14.2 condition 4). <b>null-handling</b> is the "
            "§9.2 T3 diagnostic and is never part of either score.</i>",
            body,
        )
    )
    doc.build(story)
    return path
