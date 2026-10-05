"""Human-readable scorecard renders — Markdown (PR inline) and PDF (Section 11.3).

Same aggregation as ``scorecard.summary``; exact-match and judge scores are
shown side by side and never blended (Section 11.3).
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

def _register_report_fonts() -> tuple[str, str, str]:
    """Embed a Unicode font, returning (regular, bold, italic) family names.

    The built-in Helvetica is one of the PDF standard-14 faces, which are NOT
    embedded: the viewer substitutes a local font, and a substitute missing a
    glyph draws a box. That is why `Section ` — a perfectly ordinary WinAnsi character —
    came out broken on another machine while extracting fine here.

    Bitstream Vera ships inside reportlab itself, so embedding it needs no system
    font and behaves identically on a developer Mac and in CI. It covers Section , ≥, ±,
    the em dash and the middle dot. It does NOT cover U+25CF or U+26A0, so those
    are not used anywhere in this module — see `_CHIP`.

    Falls back to Helvetica if registration fails for any reason: a slightly
    wrong glyph is better than no report.
    """

    try:
        import reportlab
        from reportlab.lib.fonts import addMapping
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        root = Path(reportlab.__file__).resolve().parent / "fonts"
        faces = {
            "NLQSans": "Vera.ttf",
            "NLQSans-Bold": "VeraBd.ttf",
            "NLQSans-Italic": "VeraIt.ttf",
            "NLQSans-BoldItalic": "VeraBI.ttf",
        }
        for name, filename in faces.items():
            pdfmetrics.registerFont(TTFont(name, str(root / filename)))
        # So <b> and <i> inside a Paragraph resolve to the embedded faces rather
        # than silently falling back to Helvetica mid-sentence.
        pdfmetrics.registerFontFamily(
            "NLQSans",
            normal="NLQSans",
            bold="NLQSans-Bold",
            italic="NLQSans-Italic",
            boldItalic="NLQSans-BoldItalic",
        )
        for bold in (0, 1):
            for italic in (0, 1):
                addMapping("NLQSans", bold, italic, [
                    "NLQSans", "NLQSans-Italic", "NLQSans-Bold", "NLQSans-BoldItalic"
                ][bold * 2 + italic])
        # Patching every ParagraphStyle and TableStyle by hand missed blocks, and
        # anything missed silently falls back to a NON-embedded standard-14 face.
        # These are the three places reportlab resolves an unspecified font, so
        # setting them covers the whole document by construction:
        #   canvas_basefontname -> the canvas, the sample stylesheet, Table cells
        #   shapes STATE_DEFAULTS -> graphics String(), which defaults to
        #                            Times-Roman and is what the charts draw with
        from reportlab import rl_config
        from reportlab.graphics import shapes

        rl_config.canvas_basefontname = "NLQSans"
        shapes.STATE_DEFAULTS["fontName"] = "NLQSans"
        return "NLQSans", "NLQSans-Bold", "NLQSans-Italic"
    except Exception:  # pragma: no cover - a broken install, not a code path
        return _FONT, _FONT_BOLD, _FONT_ITALIC


_FONT, _FONT_BOLD, _FONT_ITALIC = _register_report_fonts()

# The status mark beside a figure. U+25CF (a filled circle) is the obvious
# choice and is absent from the embedded face, so it drew a box; U+2022 is
# present and reads the same at 8pt beside a number.
_CHIP = "&#8226;"

# Section 14.2 condition 1: the package does not ship below this. The PDF colours
# against it so a reader sees pass/fail rather than a number needing context.
ACCURACY_GATE_PCT: float = 95.0

# Status palette, taken as given from a validated reference instance rather than
# chosen by eye. Light-surface contrast is 3.27 / 1.79 / 2.57 / 4.68 — warning
# and serious are sub-3:1 BY DESIGN, and the stated mitigation is that a status
# colour never carries meaning alone. Every use below is paired with a number and
# a verdict word, so hue is redundant. For TEXT, see `_pct_ink_hex`.
_STATUS_GOOD = "#0ca30c"
_STATUS_WARNING = "#fab219"
_STATUS_SERIOUS = "#ec835a"
_STATUS_CRITICAL = "#d03b3b"
# Chart surface and ink, from the same instance.
_SURFACE = "#fcfcfb"
_INK = "#0b0b0b"
_INK_MUTED = "#52514e"


def _status_fill(pct: object) -> str:
    """Status role for an accuracy figure, banded against the Section 14.2 gate."""

    if not isinstance(pct, (int, float)):
        return _INK_MUTED
    if pct >= ACCURACY_GATE_PCT:
        return _STATUS_GOOD
    if pct >= 75:
        return _STATUS_WARNING
    if pct >= 50:
        return _STATUS_SERIOUS
    return _STATUS_CRITICAL


_VERDICT_INK: dict[str, str] = {
    # Good
    "MEETS GATE": "#1b7f3b",
    "NO REGRESSION": "#1b7f3b",
    "PASS": "#1b7f3b",
    "YES": "#1b7f3b",
    # Bad
    "BELOW GATE": "#b42318",
    "REGRESSION": "#b42318",
    "FAIL": "#b42318",
    "NO": "#b42318",
    "DISAGREEMENT": "#9a6700",
}


def _verdict_hex(verdict: str) -> str:
    """Text-safe ink for a verdict word. A plain count falls back to ink."""

    return _VERDICT_INK.get(str(verdict).strip(), _INK)


def _pct_ink_hex(pct: object) -> str:
    """A TEXT-safe ink for a status verdict, banded like `_status_fill`.

    The saturated status steps are deliberately low-contrast (1.79:1 for warning
    on this surface), which is fine for a chip or a bar fill and unreadable as
    text. So status words wear these darkened inks — all >=4.7:1 — and the
    saturated hue appears only as a mark beside them.
    """

    if not isinstance(pct, (int, float)):
        return _INK_MUTED
    if pct >= ACCURACY_GATE_PCT:
        return "#1b7f3b"
    if pct >= 50:
        return "#9a6700"
    return "#b42318"


def _domain_label(domain: object) -> str:
    """`crm` -> `CRM`, `project_management` -> `Project Management`.

    Domains are lowercase identifiers everywhere they are keys. A stakeholder PDF
    is the one place they are prose.
    """

    text = str(domain or "").strip()
    if not text:
        return "—"
    if len(text) <= 4 and "_" not in text:
        return text.upper()
    return text.replace("_", " ").title()


# The single wide table was 18 columns at 7.5pt — technically complete and
# practically unreadable. The PDF splits it the way Section 11.3 says the two scores
# are used: deterministic accuracy and judge quality, side by side, never
# blended. The CSV keeps every Section 11.1 column in one row for machines.
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
        # Calibration is reported only when it PASSED. Project decision: an
        # uncalibrated run says nothing about calibration rather than carrying a
        # negative label. `calibrated` stays in the machine-readable summary, so
        # the combine gate and any later audit can still read it.
        f"mode: **{ctx.scorecard_mode}**"
        + ("  ·  judge: **calibrated**" if ctx.calibrated else ""),
        f"platform_version: `{ctx.platform_version or '—'}`  ·  "
        f"dataset_version: `{ctx.dataset_version or '—'}`",
    ]
    if ctx.scorecard_mode != "RELEASE":
        lines.append(
            "_PREVIEW run — not an official evaluation. Does not establish or "
            "update a baseline and must not certify a release (Section 10.2, Section 11.3)._"
        )
    if not ctx.judge_temperature_enforced:
        lines.append(
            "**judge determinism: temperature 0 was NOT enforced** — the model "
            "refused it; this run rests on the fixed seed (Section 10.1)"
        )
    if not ctx.judge_seed_enforced:
        lines.append(
            "**judge determinism: the configured seed never reached the provider** "
            "— see `judge_seed_enforced` (Section 10.1 'where supported')"
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
        lines.append(f"**⚠ rephrase-group finding (Section 9.5): {finding}**")
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
        "_Exact-match (HC-3, zero numeric tolerance) and judge scores (1–5) are "
        "reported side by side and never combined into a composite (Section 11.3). "
        "Baseline comparison and the regression flag are domain-level; tier rows "
        "are diagnostic. `exact_match_not_applicable` counts pairs with no "
        "deterministic core — they are outside the percentage, so a rise there is "
        "not a rise in accuracy (Section 14.2 condition 4). `exact_match_clarification` "
        "counts questions the platform declined to answer, asking a clarifying "
        "question instead (Section 2e); they are outside the percentage for the same "
        f"reason, and are scored at a fixed {CLARIFICATION_SCORE} with no "
        "dimensions — which is why `judge_overall` is a mean over "
        "`judge_clarifications` more rows than the dimension columns are. "
        "`null_handling_fail` is the "
        "Section 9.2 T3 diagnostic and is never part of either score._",
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
        *((("judge calibrated", "yes"),) if ctx.calibrated else ()),
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
    """Notice text alone — the Markdown report and older callers want strings."""

    return [text for _, text in _notices(ctx, rows, comparisons)]


def _notices(
    ctx: RunContext, rows: list[dict], comparisons: dict
) -> list[tuple[str, str]]:
    """`(severity, text)` for everything a reader must not miss.

    These were once interleaved with provenance as ordinary body text, so a
    regression flag and a timestamp carried the same weight. Promoting all of them
    to bold red has the same fault one step along: when a PREVIEW notice shouts as
    loudly as a failed accuracy gate, the block stops being read. Severity is
    resolved here so the PDF can render three weights, and so the list is ordered
    by consequence rather than by the order the checks happened to run.

    `critical` a gate, a regression, or a comparability rule failed.
    `caution`  a number is not what it looks like.
    `info`     provenance the reader should know before quoting a figure.
    """

    critical: list[tuple[str, str]] = []
    caution: list[tuple[str, str]] = []
    info: list[tuple[str, str]] = []

    flagged = sorted(d for d, c in comparisons.items() if c.regression_flag)
    if flagged:
        critical.append(
            (
                "critical",
                f"REGRESSION: {', '.join(flagged)} dropped 5 pp or more against "
                "baseline (Section 11.3).",
            )
        )

    for row in rows:
        if row["tier"] != "ALL":
            continue
        pct = row.get("exact_match_pct")
        if isinstance(pct, (int, float)) and pct < ACCURACY_GATE_PCT:
            critical.append(
                (
                    "critical",
                    f"ACCURACY GATE: {row['domain']} is at {pct:.2f}% against the "
                    f"{ACCURACY_GATE_PCT:.0f}% required before the package ships "
                    "(Section 14.2 condition 1).",
                )
            )
        clarified = row.get("exact_match_clarification") or 0
        if clarified:
            caution.append(
                (
                    "caution",
                    f"{row['domain']}: {clarified} question(s) were declined by the "
                    "platform, scored at a fixed "
                    f"{CLARIFICATION_SCORE} and EXCLUDED from the exact-match "
                    "denominator — so the percentage covers fewer questions than "
                    "were asked (Section 14.2 condition 4).",
                )
            )

    if not ctx.comparison_is_default:
        critical.append(
            (
                "critical",
                "Exact-match ran under a NON-DEFAULT comparison policy; this run "
                "cannot establish a baseline (OI-2 / OI-3).",
            )
        )

    for finding in ctx.rephrase_findings:
        caution.append(("caution", f"Rephrase-group finding (Section 9.5): {finding}"))

    if ctx.scorecard_mode != "RELEASE":
        info.append(
            (
                "info",
                "PREVIEW run — not an official evaluation. It neither establishes "
                "nor updates a baseline and cannot certify a release (Section 10.2, Section 11.3).",
            )
        )
    if ctx.provenance_note:
        info.append(("info", f"Assembled: {ctx.provenance_note}"))

    return critical + caution + info


def _pct_colour(value: object):
    """Red / amber / green against the Section 14.2 gate."""

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
        "cell", fontName=_FONT, fontSize=8.5, leading=10, alignment=2
    )

    header = [_HEADER_LABELS.get(c, c) for c in columns]
    body: list[list[object]] = [header]
    for row in rows:
        cells: list[object] = []
        for column in columns:
            text = _fmt(row.get(column))
            if column == "domain":
                # Prose casing, matching the tiles and chart titles. The CSV keeps
                # the lowercase identifier; mixed casing inside one PDF reads as
                # two different fields.
                text = _domain_label(row.get(column))
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
                        f'<font color="{chip}">{_CHIP}</font>&nbsp;{text}', cell
                    )
                )
            else:
                cells.append(text)
        body.append(cells)

    table = Table(body, repeatRows=1, hAlign="LEFT")
    style = [
        ("FONTNAME", (0, 0), (-1, -1), _FONT),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#22304a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), _FONT_BOLD),
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
            style.append(("FONTNAME", (0, index), (-1, index), _FONT_BOLD))
        if row.get("regression_flag") and "regression_flag" in columns:
            column = columns.index("regression_flag")
            style.append(
                ("TEXTCOLOR", (column, index), (column, index), colors.HexColor("#b42318"))
            )
            style.append(
                ("FONTNAME", (column, index), (column, index), _FONT_BOLD)
            )
    table.setStyle(TableStyle(style))
    return table


def _outcome_chart(rows: list[dict], *, width: float, height: float):
    """How the platform RESPONDED, as one stacked bar per domain.

    FORM. Part-to-whole across five mutually exclusive outcomes, so a stacked bar
    — and only five segments, inside the <=6 a stack can carry. The accuracy
    percentage answers "how often was it right"; this answers "what did it do",
    which is a different question and the one that explains the percentage. A
    domain whose misses are mostly declined questions needs a different
    conversation from one whose misses are wrong numbers.

    COLOUR. Status, not identity: each segment is a state (right, wrong, declined,
    errored, nothing deterministic to compare), so it comes from the reserved
    status palette. Two of those steps are sub-3:1 here by design, so every
    segment is also named in the legend with its count — hue is never the only
    carrier.
    """

    from reportlab.graphics.shapes import Drawing, Line, Rect, String
    from reportlab.lib import colors

    domains = [r for r in rows if r["tier"] == "ALL"]
    if not domains:
        return None

    # (key, label, status step). Ordered good -> bad -> not-applicable so the
    # stack reads left to right as "how well did this go".
    segments = (
        ("_pass", "answered correctly", _STATUS_GOOD),
        ("_fail", "answered incorrectly", _STATUS_CRITICAL),
        ("exact_match_clarification", "declined, asked to clarify", _STATUS_WARNING),
        ("platform_errors", "no answer returned", _STATUS_SERIOUS),
        ("exact_match_not_applicable", "no deterministic core", _INK_MUTED),
    )

    left, right, top, bottom = 58, 8, 16, 30
    plot_w = width - left - right
    plot_h = height - top - bottom
    bar_h = min(14.0, plot_h / max(1, len(domains)) - 10)
    gap = (plot_h - bar_h * len(domains)) / max(1, len(domains))

    drawing = Drawing(width, height)
    for index, row in enumerate(reversed(domains)):
        total = int(row.get("questions_total") or 0)
        if not total:
            continue
        passed = int(row.get("exact_match_pass") or 0)
        clarified = int(row.get("exact_match_clarification") or 0)
        errored = int(row.get("platform_errors") or 0)
        not_applicable = int(row.get("exact_match_not_applicable") or 0)
        counts = {
            "_pass": passed,
            "_fail": max(0, total - passed - clarified - errored - not_applicable),
            "exact_match_clarification": clarified,
            "platform_errors": errored,
            "exact_match_not_applicable": not_applicable,
        }

        y = bottom + index * (bar_h + gap) + gap / 2
        drawing.add(
            String(left - 8, y + bar_h / 2 - 3, _domain_label(row["domain"]),
                   fontName=_FONT_BOLD, fontSize=8,
                   fillColor=colors.HexColor(_INK), textAnchor="end")
        )
        x = left
        for key, _label, fill in segments:
            count = counts.get(key, 0)
            if not count:
                continue
            seg_w = plot_w * count / total
            drawing.add(
                Rect(x, y, max(0.0, seg_w - 2), bar_h, rx=0, ry=0,
                     fillColor=colors.HexColor(fill),
                     strokeColor=colors.HexColor(_SURFACE), strokeWidth=0)
            )
            # Count inside the segment only when it fits; never clipped.
            if seg_w > 24:
                drawing.add(
                    String(x + (seg_w - 2) / 2, y + bar_h / 2 - 3, str(count),
                           fontName=_FONT_BOLD, fontSize=7,
                           fillColor=colors.white, textAnchor="middle")
                )
            # 2px surface gap between fills, per the mark spec — a gap, not a border.
            x += seg_w
        drawing.add(
            String(left + plot_w + 4, y + bar_h / 2 - 3, f"n={total}",
                   fontName=_FONT, fontSize=7,
                   fillColor=colors.HexColor(_INK_MUTED))
        )

    # Legend: always present for >=2 series, so identity is never colour-alone —
    # but only for outcomes that actually occurred. A swatch for "no answer
    # returned" on a run where nothing failed reads as though something did.
    present = set()
    for row in domains:
        total = int(row.get("questions_total") or 0)
        if not total:
            continue
        passed = int(row.get("exact_match_pass") or 0)
        clarified = int(row.get("exact_match_clarification") or 0)
        errored = int(row.get("platform_errors") or 0)
        not_applicable = int(row.get("exact_match_not_applicable") or 0)
        if passed:
            present.add("_pass")
        if total - passed - clarified - errored - not_applicable > 0:
            present.add("_fail")
        if clarified:
            present.add("exact_match_clarification")
        if errored:
            present.add("platform_errors")
        if not_applicable:
            present.add("exact_match_not_applicable")

    lx = left
    ly = 10
    for _key, label, fill in [s for s in segments if s[0] in present]:
        drawing.add(Rect(lx, ly, 7, 7, fillColor=colors.HexColor(fill),
                         strokeColor=colors.HexColor(fill), strokeWidth=0))
        drawing.add(String(lx + 11, ly + 1, label, fontName=_FONT, fontSize=6.5,
                           fillColor=colors.HexColor(_INK_MUTED)))
        lx += 11 + len(label) * 3.3 + 14
    drawing.add(Line(left, bottom - 6, left + plot_w, bottom - 6,
                     strokeColor=colors.HexColor("#e6e7e4"), strokeWidth=0.5))
    return drawing


def _dimension_chart(rows: list[dict], domain: str, *, width: float, height: float):
    """The judge's four dimensions for one domain, on the 1-5 rubric scale.

    This is the chart that was missing. The dimensions sat in a table column, so
    the most actionable thing in the run was invisible: on CRM the platform scores
    ~4.1 for SQL plausibility and ~3.2 for factual correctness, which says it
    writes credible SQL and still returns the wrong number. That distinction
    decides whether the conversation is about query generation or about data
    interpretation, and a reader should not have to diff two columns to find it.

    FORM. Four ordered-by-nothing categories against a common 1-5 scale, so bars
    on one axis. One series, so one colour — a per-bar value ramp would encode
    length twice and burn the only free channel.
    """

    from reportlab.graphics.shapes import Drawing, Line, Rect, String
    from reportlab.lib import colors

    row = next((r for r in rows if r["domain"] == domain and r["tier"] == "ALL"), None)
    if row is None:
        return None
    dims = (
        ("judge_factual", "factual correctness"),
        ("judge_completeness", "completeness"),
        ("judge_format", "format adherence"),
        ("judge_sql", "SQL plausibility"),
    )
    values = [(label, row.get(key)) for key, label in dims]
    if not any(isinstance(v, (int, float)) for _l, v in values):
        return None

    left, right, top, bottom = 108, 54, 12, 22
    plot_w = width - left - right
    plot_h = height - top - bottom
    bar_h = min(9.0, plot_h / len(values) - 5)
    gap = (plot_h - bar_h * len(values)) / len(values)

    drawing = Drawing(width, height)
    # Recessive grid at each rubric anchor. 1, 3 and 5 are the scored anchors in
    # Section 10; 2 and 4 are interpolated, so they are ticked but not labelled.
    for score in (1, 2, 3, 4, 5):
        x = left + plot_w * (score - 1) / 4
        drawing.add(Line(x, bottom, x, bottom + plot_h,
                         strokeColor=colors.HexColor("#e6e7e4"), strokeWidth=0.5))
        drawing.add(String(x, bottom - 10, str(score) if score in (1, 3, 5) else "",
                           fontName=_FONT, fontSize=6.5,
                           fillColor=colors.HexColor(_INK_MUTED), textAnchor="middle"))

    for index, (label, value) in enumerate(reversed(values)):
        y = bottom + index * (bar_h + gap) + gap / 2
        drawing.add(String(left - 6, y + bar_h / 2 - 2.5, label,
                           fontName=_FONT, fontSize=7.5,
                           fillColor=colors.HexColor(_INK), textAnchor="end"))
        if not isinstance(value, (int, float)):
            continue
        # 1 is the floor of the scale, not zero, so the bar starts at 1.
        bar_w = plot_w * (max(1.0, min(5.0, value)) - 1) / 4
        if bar_w >= 1.0:
            fill = colors.HexColor(_status_fill(((value - 1) / 4) * 100))
            drawing.add(Rect(left, y, bar_w, bar_h, rx=3, ry=3, fillColor=fill,
                             strokeColor=fill.clone(), strokeWidth=0.5))
        drawing.add(String(left + plot_w + 6, y + bar_h / 2 - 2.5, f"{value:.2f}",
                           fontName=_FONT_BOLD, fontSize=7.5,
                           fillColor=colors.HexColor(_INK)))
    return drawing


def _tier_chart(rows: list[dict], domain: str, *, width: float, height: float):
    """Per-tier accuracy as horizontal bars against the Section 14.2 gate.

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
    # Thin marks: a saturated fill this wide reads loud as a heavy block.
    bar_h = min(9.0, plot_h / max(1, len(tiers)) - 5)
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
                   fontName=_FONT, fontSize=6.5,
                   fillColor=colors.HexColor(_INK_MUTED), textAnchor="middle")
        )

    for index, row in enumerate(reversed(tiers)):
        pct = row.get("exact_match_pct")
        y = bottom + index * (bar_h + gap) + gap / 2
        drawing.add(
            String(left - 6, y + bar_h / 2 - 2.5, str(row["tier"]),
                   fontName=_FONT_BOLD, fontSize=7.5,
                   fillColor=colors.HexColor(_INK), textAnchor="end")
        )
        if not isinstance(pct, (int, float)):
            drawing.add(
                String(left + 4, y + bar_h / 2 - 2.5, "no eligible questions",
                       fontName=_FONT_ITALIC, fontSize=7,
                       fillColor=colors.HexColor(_INK_MUTED))
            )
            continue

        fill = colors.HexColor(_status_fill(pct))
        bar_w = plot_w * pct / 100.0
        # A zero draws NOTHING. Clamping it to a hairline produced a 0.6pt stub
        # that reads as a stray glyph beside the tier label; the direct label
        # already says 0.0%, so the absence is the honest mark.
        if bar_w >= 1.0:
            drawing.add(
                Rect(left, y, bar_w, bar_h, rx=3, ry=3, fillColor=fill,
                     strokeColor=fill.clone(), strokeWidth=0.5)
            )
        # Direct label: the number and the count, in ink. Selective by
        # construction — five bars, five labels, no axis clutter needed.
        drawing.add(
            String(left + plot_w + 6, y + bar_h / 2 - 2.5,
                   f"{pct:.1f}%  ({row.get('exact_match_pass')}/"
                   f"{row.get('questions_total')})",
                   fontName=_FONT, fontSize=7,
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
               f"{ACCURACY_GATE_PCT:.0f}% gate (Section 14.2)",
               fontName=_FONT_BOLD, fontSize=6.5,
               fillColor=colors.HexColor(_INK), textAnchor="middle")
    )
    return drawing


def platform_findings(
    ctx: RunContext, rows: list[dict], comparisons: dict
) -> list[tuple[str, str, str]]:
    """What this run observed about the PLATFORM, as (finding, verdict, basis).

    This replaced a table of Section 14.2's nine ship conditions. Seven of those nine
    describe our own deliverables — an independent reviewer re-executing the
    reference SQL, cross-domain question uniqueness, a clean-room byte-identical
    regeneration, whether a failing pair was reworked rather than removed. None of
    them say anything about how Pulse answered, and printing them on a card the
    Platform Owner reads as a verdict on their platform confuses the evaluation
    framework with the thing being evaluated. They belong in the Section 14.1 QA audit
    report, which is a separate deliverable.

    What is left here is only what the run can assert about the system under
    evaluation. Three of these were already computed and buried in table columns.
    """

    domain_rows = [r for r in rows if r["tier"] == "ALL"]
    out: list[tuple[str, str, str]] = []

    below = [
        r["domain"]
        for r in domain_rows
        if isinstance(r.get("exact_match_pct"), (int, float))
        and r["exact_match_pct"] < ACCURACY_GATE_PCT
    ]
    out.append(
        (
            f"Deterministic accuracy vs the {ACCURACY_GATE_PCT:.0f}% gate",
            "BELOW GATE" if below else "MEETS GATE",
            ", ".join(
                f"{_domain_label(r['domain'])} {r['exact_match_pct']:.2f}%"
                for r in domain_rows
                if isinstance(r.get("exact_match_pct"), (int, float))
            )
            or "no eligible questions",
        )
    )

    regressed = sorted(d for d, c in comparisons.items() if c.regression_flag)
    if any(c.has_baseline for c in comparisons.values()):
        out.append(
            (
                "Change against the established baseline",
                "REGRESSION" if regressed else "NO REGRESSION",
                f"{', '.join(_domain_label(d) for d in regressed)} dropped ≥5 pp"
                if regressed
                else "no domain dropped 5 pp or more",
            )
        )

    errors = sum(int(r.get("platform_errors") or 0) for r in domain_rows)
    out.append(
        (
            "Platform answered every question",
            "YES" if not errors else "NO",
            "no request failed"
            if not errors
            else f"{errors} question(s) the platform could not answer at all",
        )
    )

    clarified = sum(int(r.get("exact_match_clarification") or 0) for r in domain_rows)
    if clarified:
        out.append(
            (
                "Questions the platform declined to answer (Section 2e)",
                f"{clarified}",
                "answered with a clarifying question instead; scored at a fixed "
                f"{CLARIFICATION_SCORE} and held outside the percentage",
            )
        )

    nulls = sum(int(r.get("null_handling_fail") or 0) for r in domain_rows)
    out.append(
        (
            "NULL / outer-join handling (Section 9.2, T3)",
            "PASS" if not nulls else "FAIL",
            "outer-join questions preserved the rows an outer join preserves"
            if not nulls
            else f"{nulls} question(s) answered an outer-join question with an "
            "inner join, silently dropping unmatched rows",
        )
    )

    if ctx.rephrase_findings:
        out.append(
            (
                "Rephrase-group agreement (Section 9.5)",
                "DISAGREEMENT",
                "; ".join(str(f) for f in ctx.rephrase_findings)[:160],
            )
        )

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
    question_results.csv (Section 14.2 condition 5); this is only the shape of it.
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
    """The Section 11.3 PDF summary — for a human deciding something, not a data dump.

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
        HRFlowable,
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
    for _name in ("Normal", "BodyText", "Title", "Heading1", "Heading2", "Heading3"):
        if _name in styles:
            styles[_name].fontName = _FONT_BOLD if "Heading" in _name or _name == "Title" else _FONT
    body = ParagraphStyle(
        "body",
        parent=styles["BodyText"],
        fontName=_FONT,
        fontSize=9,
        leading=12,
        alignment=TA_LEFT,
    )
    warn = ParagraphStyle(
        "warn",
        parent=body,
        textColor=colors.HexColor("#b42318"),
        fontName=_FONT_BOLD,
    )
    section = ParagraphStyle(
        "section",
        parent=styles["Heading3"],
        fontName=_FONT_BOLD,
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
        bottomMargin=0.62 * inch,
        title=f"Regression scorecard — {ctx.run_id}",
        author="NLQ Evaluation Framework",
        subject=f"{ctx.scorecard_mode} scorecard for platform {ctx.platform_version or 'unversioned'}",
    )

    # --- masthead: what this is, at a glance ------------------------------
    # A report that opens straight into a table reads as a dump. The masthead
    # states the artifact, its mode, and the provenance a reader needs before
    # quoting any figure from it — on the first line, not three sections down.
    mode_ink = (
        colors.HexColor("#1b7f3b")
        if ctx.scorecard_mode == "RELEASE"
        else colors.HexColor("#42506b")
    )
    masthead = Table(
        [
            [
                Paragraph(
                    "Regression scorecard",
                    # fontSize on the STYLE, with leading to match. Setting the
                    # size inline with <font size=17> left the style's 12pt
                    # leading in place, so the line box was shorter than the
                    # glyphs and LINEBELOW cut through the descenders.
                    ParagraphStyle(
                        "masthead_title",
                        parent=body,
                        fontName=_FONT_BOLD,
                        fontSize=17,
                        leading=21,
                        textColor=colors.HexColor(_INK),
                    ),
                ),
                Paragraph(
                    ctx.scorecard_mode,
                    ParagraphStyle(
                        "masthead_mode",
                        parent=body,
                        fontName=_FONT_BOLD,
                        fontSize=10,
                        leading=14,
                        alignment=2,
                        textColor=colors.white,
                        backColor=mode_ink,
                    ),
                ),
            ]
        ],
        colWidths=[8.5 * inch, 1.6 * inch],
        hAlign="LEFT",
    )
    masthead.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), _FONT),
                ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                # NO LINEBELOW. A rule on the cell edge sits wherever the
                # paragraph's line box ends, and a 17pt face inside a 21pt
                # leading leaves its descenders ~0.4pt off that edge — touching,
                # whatever BOTTOMPADDING says, because the leading absorbs it.
                # The rule is a separate flowable below, so its distance from the
                # text is set explicitly instead of inferred.
            ]
        )
    )
    meta = " \u00b7 ".join(
        part
        for part in (
            ctx.run_id,
            (ctx.run_timestamp_iso or "")[:19].replace("T", " ") + " UTC",
            f"platform {ctx.platform_version}" if ctx.platform_version else None,
            f"dataset {ctx.dataset_version}" if ctx.dataset_version else None,
            "judge calibrated" if ctx.calibrated else None,
        )
        if part
    )
    story: list = [
        masthead,
        Spacer(1, 0.045 * inch),
        HRFlowable(
            width="100%",
            thickness=0.75,
            color=colors.HexColor("#22304a"),
            spaceBefore=0,
            spaceAfter=0,
        ),
        Spacer(1, 0.045 * inch),
        Paragraph(
            f"<font size=7.5 color='{_INK_MUTED}'>{meta}</font>",
            ParagraphStyle("meta", parent=body, fontSize=7.5),
        ),
        Spacer(1, 0.16 * inch),
    ]

    # --- headline as stat tiles, not a table ------------------------------
    # The story here is one number per domain. A table makes the reader parse a
    # grid to find it; a tile states it. The gate verdict rides as a word beside
    # a status chip, so hue is never the only carrier (the saturated status steps
    # are deliberately sub-3:1 and must not be used as text).
    domain_rows = [r for r in rows if r["tier"] == "ALL"]
    if domain_rows:
        tiles: list = []
        for row in domain_rows:
            pct = row.get("exact_match_pct")
            numeric = isinstance(pct, (int, float))
            gate_word = (
                "—" if not numeric else ("MEETS GATE" if pct >= ACCURACY_GATE_PCT else "BELOW GATE")
            )
            delta = row.get("delta_pct")
            tiles.append(
                Table(
                    [
                        [
                            Paragraph(
                                f"<font size=8 color='{_INK_MUTED}'>"
                                f"<b>{_domain_label(row['domain'])}</b>  exact-match"
                                f"</font>",
                                body,
                            )
                        ],
                        [
                            Paragraph(
                                f"<font size=26 color='{_INK}'>"
                                f"{pct:.1f}%</font>" if numeric else "—",
                                ParagraphStyle("hero", parent=body, leading=30),
                            )
                        ],
                        [
                            Paragraph(
                                f"<font size=8 color='{_INK_MUTED}'>"
                                f"{row.get('exact_match_pass')} of "
                                f"{row.get('questions_total')} eligible · "
                                f"judge {_fmt(row.get('judge_overall'))}/5</font>",
                                body,
                            )
                        ],
                        [
                            Paragraph(
                                f"<font size=8 color='{_pct_ink_hex(pct)}'><b>"
                                f"{gate_word}</b></font>"
                                f"<font size=8 color='{_INK_MUTED}'>"
                                + (
                                    "  ·  no baseline"
                                    if delta is None
                                    else f"  ·  {delta:+.2f} pp vs baseline"
                                )
                                + "</font>",
                                body,
                            )
                        ],
                    ],
                    colWidths=[2.35 * inch],
                )
            )
            tiles[-1].setStyle(
                TableStyle(
                    [
                        ("FONTNAME", (0, 0), (-1, -1), _FONT),
                        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#c9ced6")),
                        ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(_status_fill(pct))),
                        ("LEFTPADDING", (0, 0), (-1, -1), 9),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                        ("TOPPADDING", (0, 0), (-1, 0), 7),
                        ("BOTTOMPADDING", (0, -1), (-1, -1), 7),
                        ("TOPPADDING", (0, 1), (-1, -1), 0),
                        ("BOTTOMPADDING", (0, 0), (-1, -2), 1),
                    ]
                )
            )
        # Laid out in one row, padded so a single domain does not stretch.
        tile_row = Table(
            [tiles + [""] * max(0, 4 - len(tiles))],
            colWidths=[2.55 * inch] * 4,
            hAlign="LEFT",
        )
        tile_row.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), _FONT),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        story.append(tile_row)
        story.append(Spacer(1, 0.18 * inch))

    # --- anything alarming, before the detail -----------------------------
    notices = _notices(ctx, rows, comparisons)
    if notices:
        story.append(Paragraph("Read first", section))
        severity_ink = {
            "critical": "#b42318",
            "caution": "#9a6700",
            "info": _INK_MUTED,
        }
        severity_chip = {
            "critical": _STATUS_CRITICAL,
            "caution": _STATUS_WARNING,
            "info": "#c9ced6",
        }
        # One row per notice: a status chip, then the text in its severity ink.
        # Bold red on everything meant the block read as noise; a PREVIEW notice
        # and a failed accuracy gate are not the same news.
        notice_rows = []
        for severity, text in notices:
            notice_rows.append(
                [
                    "",
                    Paragraph(
                        f"<font color='{severity_ink[severity]}'>"
                        + (f"<b>{text}</b>" if severity == "critical" else text)
                        + "</font>",
                        ParagraphStyle("notice", parent=body, fontSize=8.5, leading=11),
                    ),
                ]
            )
        notice_table = Table(
            notice_rows, colWidths=[0.14 * inch, 9.96 * inch], hAlign="LEFT"
        )
        notice_style = [
            ("FONTNAME", (0, 0), (-1, -1), _FONT),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (0, -1), 0),
            ("RIGHTPADDING", (0, 0), (0, -1), 0),
            ("LEFTPADDING", (1, 0), (1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]
        for index, (severity, _text) in enumerate(notices):
            notice_style.append(
                ("BACKGROUND", (0, index), (0, index), colors.HexColor(severity_chip[severity]))
            )
        notice_table.setStyle(TableStyle(notice_style))
        story.append(notice_table)
        story.append(Spacer(1, 0.12 * inch))

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
                ("FONTNAME", (0, 0), (-1, -1), _FONT),
                ("FONTNAME", (0, 0), (0, -1), _FONT_BOLD),
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

    # --- ship / do-not-ship, against Section 14.2 --------------------------------
    checks = platform_findings(ctx, rows, comparisons)
    # Every cell is a Paragraph: a raw string in a reportlab Table does not
    # wrap, it overflows the column. The basis text runs to ~100 characters.
    finding_cell = ParagraphStyle(
        "finding", parent=body, fontSize=8, leading=10, alignment=TA_LEFT
    )
    verdict_cell = ParagraphStyle(
        "verdict", parent=body, fontName=_FONT_BOLD, fontSize=8, leading=10
    )
    gate_table = Table(
        [
            [
                Paragraph("<b>what this run observed about the platform</b>", finding_cell),
                Paragraph("<b>verdict</b>", finding_cell),
                Paragraph("<b>basis</b>", finding_cell),
            ]
        ]
        + [
            [
                Paragraph(c, finding_cell),
                Paragraph(
                    f'<font color="{_verdict_hex(v)}">{v}</font>', verdict_cell
                ),
                Paragraph(note, finding_cell),
            ]
            for c, v, note in checks
        ],
        colWidths=[3.1 * inch, 1.45 * inch, 5.55 * inch],
        hAlign="LEFT",
        repeatRows=1,
    )
    gate_style = [
        ("FONTNAME", (0, 0), (-1, -1), _FONT),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#22304a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), _FONT_BOLD),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (1, 1), (1, -1), _FONT_BOLD),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c9ced6")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]
    gate_table.setStyle(TableStyle(gate_style))
    story.append(
        KeepTogether(
            [
                Paragraph(
                    "Platform findings — what this run observed about Pulse",
                    section,
                ),
                gate_table,
            ]
        )
    )

    # --- what the platform DID, before how often it was right -------------
    outcome = _outcome_chart(rows, width=7.0 * inch, height=1.5 * inch)
    if outcome is not None:
        story.append(
            KeepTogether(
                [
                    Paragraph("Answer outcomes", section),
                    outcome,
                    Paragraph(
                        "<i>The accuracy percentage counts only questions with a "
                        "deterministic core that the platform actually answered. "
                        "Declined questions and errors sit outside it, so this is "
                        "where they are visible (Section 14.2 condition 4).</i>",
                        body,
                    ),
                ]
            )
        )
        story.append(Spacer(1, 0.10 * inch))

    # --- where answer QUALITY drops, per dimension ------------------------
    for domain in sorted({r["domain"] for r in rows}):
        chart = _dimension_chart(rows, domain, width=6.6 * inch, height=1.15 * inch)
        if chart is None:
            continue
        story.append(
            KeepTogether(
                [
                    Paragraph(
                        f"Judge dimensions — {_domain_label(domain)}", section
                    ),
                    chart,
                ]
            )
        )
    if any(r["tier"] == "ALL" for r in rows):
        story.append(
            Paragraph(
                "<i>The Section 10 rubric, 1–5, averaged over the domain. The gap between "
                "SQL plausibility and factual correctness is the useful read: a high "
                "SQL score beside a low factual score means the platform is writing "
                "credible queries and still returning the wrong number, which is a "
                "different problem from writing a query that cannot answer the "
                "question. Never blended with exact-match (Section 11.3).</i>",
                body,
            )
        )
        story.append(Spacer(1, 0.10 * inch))

    # --- per-tier shape, as a picture -------------------------------------
    for domain in sorted({r["domain"] for r in rows}):
        chart = _tier_chart(rows, domain, width=6.6 * inch, height=1.5 * inch)
        if chart is None:
            continue
        story.append(
            KeepTogether(
                [
                    Paragraph(
                        f"Exact-match by tier — {_domain_label(domain)}", section
                    ),
                    chart,
                ]
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
                    # Base font first: a FONTNAME scoped to the header row only
                    # leaves the body cells on a non-embedded standard-14 face.
                    ("FONTNAME", (0, 0), (-1, -1), _FONT),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#22304a")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), _FONT_BOLD),
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
                "question_results.csv (Section 14.2 condition 5).</i>",
                body,
            )
        )

    # --- the two scores, side by side but never blended -------------------
    story.append(
        KeepTogether(
            [
                Paragraph("Deterministic accuracy (HC-3, zero numeric tolerance)", section),
                _table(rows, _EXACT_COLUMNS, highlight_pct=True),
            ]
        )
    )
    story.append(
        KeepTogether(
            [
                Paragraph("Judge quality (1–5 per dimension, Section 10)", section),
                _table(rows, _JUDGE_TABLE_COLUMNS, highlight_pct=False),
            ]
        )
    )

    # Attached to the table above with KeepTogether: as a bare paragraph it
    # routinely landed alone on a final page, which reads as a printing fault.
    story[-1] = KeepTogether(
        [
            story[-1],
            Spacer(1, 0.12 * inch),
            Paragraph(
                "<i>Exact-match and judge scores are reported side by side and never "
            "combined into a composite (Section 11.3) — they measure different things. "
            "Baseline comparison and the regression flag are domain-level; tier "
            "rows are diagnostic. <b>n/a</b> counts pairs with no deterministic "
            "core and <b>clarified</b> counts questions the platform declined to "
            "answer; both sit outside the percentage, so a rise in either is not a "
            "rise in accuracy (Section 14.2 condition 4). <b>null-handling</b> is the "
                "Section 9.2 T3 diagnostic and is never part of either score.</i>",
                body,
            ),
        ]
    )

    def _footer(canvas, document) -> None:
        """Page number, run id and mode on every page.

        A scorecard gets printed and passed around; a loose page with no run id on
        it cannot be traced back to the run that produced it, which is the whole
        point of Section 11.1 carrying both version tags.
        """

        canvas.saveState()
        canvas.setFont(_FONT, 7)
        canvas.setFillColor(colors.HexColor(_INK_MUTED))
        canvas.setStrokeColor(colors.HexColor("#c9ced6"))
        y = 0.34 * inch
        canvas.setLineWidth(0.25)
        canvas.line(0.45 * inch, y + 10, landscape(letter)[0] - 0.45 * inch, y + 10)
        canvas.drawString(
            0.45 * inch,
            y,
            f"{ctx.run_id}  ·  {ctx.scorecard_mode}"
            + ("  ·  judge calibrated" if ctx.calibrated else ""),
        )
        canvas.drawRightString(
            landscape(letter)[0] - 0.45 * inch, y, f"page {document.page}"
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return path
