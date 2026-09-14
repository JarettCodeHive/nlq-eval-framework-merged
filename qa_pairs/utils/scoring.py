"""Reference implementation of the three tier scoring modes (scope 9.2).

The delivered 7-field CSV is unchanged; scoring metadata lives in the
companion file. This module is what turns that metadata into a verdict, so
the "scorer path" is executable, not just a label:

  scalar_exact      T1/T2/T3 single-value answers. The deterministic
                    evaluator extracts the FIRST number from the platform
                    response and compares it to the golden number with zero
                    tolerance (configurable). Non-numeric golden answers
                    fall back to normalised string equality.

  table_exact       T2 multi-column / T4 grouped answers. Row-for-row,
                    cell-for-cell exact match. Numeric cells compare
                    numerically at zero tolerance; row order is significant
                    (the reference SQL has a deterministic ORDER BY).

  judge_plus_exact  T5. "The judge scores the surrounding reasoning; the
                    scorer scores the numbers." Every numeric component of
                    the golden answer must appear, in order, in the platform
                    response at zero tolerance. The judge verdict on the
                    prose is a separate boolean input; a pair passes only if
                    the numerics match AND the judge did not reject it.

`Decimal("0")` tolerance is the scope default (change C2). A caller may
pass a wider tolerance for exploratory scoring; the golden data never
relies on one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

MODES = ("scalar_exact", "table_exact", "judge_plus_exact")
_NUM = re.compile(r"-?\d+(?:\.\d+)?")
_ZERO = Decimal("0")


@dataclass
class Verdict:
    passed: bool
    mode: str
    reason: str
    numeric_mismatches: list = field(default_factory=list)


def numbers(text: str) -> list[Decimal]:
    """Every numeric token in reading order (commas / $ stripped first)."""
    cleaned = text.replace(",", "").replace("$", "")
    out = []
    for m in _NUM.findall(cleaned):
        try:
            out.append(Decimal(m))
        except InvalidOperation:  # pragma: no cover - regex guarantees valid
            pass
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _grid(answer: str) -> list[list[str]]:
    return [[c.strip() for c in row.split("|")] for row in answer.split(";")]


def _cell_eq(golden: str, actual: str, tol: Decimal) -> bool:
    g, a = numbers(golden), numbers(actual)
    if len(g) == 1 and len(a) >= 1:
        return abs(a[0] - g[0]) <= tol
    return _norm(golden) == _norm(actual)


def score_scalar_exact(golden: str, response: str, tol: Decimal = _ZERO) -> Verdict:
    g = numbers(golden)
    if not g:
        ok = _norm(golden) == _norm(response)
        return Verdict(ok, "scalar_exact", "string match" if ok else "string mismatch")
    a = numbers(response)
    if not a:
        return Verdict(False, "scalar_exact", "no number in response", [str(g[0])])
    diff = abs(a[0] - g[0])
    ok = diff <= tol
    return Verdict(
        ok,
        "scalar_exact",
        f"|{a[0]} - {g[0]}| = {diff} (tol {tol})",
        [] if ok else [f"{g[0]} vs {a[0]}"],
    )


def score_table_exact(golden: str, response: str, tol: Decimal = _ZERO) -> Verdict:
    gg, ga = _grid(golden), _grid(response)
    if len(gg) != len(ga):
        return Verdict(False, "table_exact", f"row count {len(ga)} != expected {len(gg)}")
    mism = []
    for ri, (gr, ar) in enumerate(zip(gg, ga)):
        if len(gr) != len(ar):
            return Verdict(
                False, "table_exact", f"row {ri}: column count {len(ar)} != expected {len(gr)}"
            )
        for ci, (gc, ac) in enumerate(zip(gr, ar)):
            if not _cell_eq(gc, ac, tol):
                mism.append(f"[{ri},{ci}] {gc!r} vs {ac!r}")
    ok = not mism
    return Verdict(
        ok, "table_exact", "exact table match" if ok else f"{len(mism)} cell mismatch(es)", mism
    )


def score_judge_plus_exact(
    golden: str, response: str, *, judge_verdict: bool | None = None, tol: Decimal = _ZERO
) -> Verdict:
    g, a = numbers(golden), numbers(response)
    mism = []
    # every golden number must appear, in order, somewhere in the response
    ai = 0
    for gn in g:
        hit = None
        while ai < len(a):
            if abs(a[ai] - gn) <= tol:
                hit = a[ai]
                ai += 1
                break
            ai += 1
        if hit is None:
            mism.append(f"missing numeric component {gn}")
    if mism:
        return Verdict(False, "judge_plus_exact", "numeric component(s) not matched", mism)
    if judge_verdict is False:
        return Verdict(False, "judge_plus_exact", "numerics match; judge rejected the reasoning")
    reason = (
        "numerics match; judge not run"
        if judge_verdict is None
        else "numerics match; judge accepted"
    )
    return Verdict(True, "judge_plus_exact", reason)


def score(
    mode: str,
    golden: str,
    response: str,
    *,
    judge_verdict: bool | None = None,
    tol: Decimal = _ZERO,
) -> Verdict:
    if mode == "scalar_exact":
        return score_scalar_exact(golden, response, tol)
    if mode == "table_exact":
        return score_table_exact(golden, response, tol)
    if mode == "judge_plus_exact":
        return score_judge_plus_exact(golden, response, judge_verdict=judge_verdict, tol=tol)
    raise ValueError(f"unknown scoring mode: {mode!r} (expected one of {MODES})")
