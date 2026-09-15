# Calibration anchors (§10.2)

One file per domain, `<domain>.json`, holding the **human-graded** examples the
judge is validated against. `judge.calibration.load_anchors` reads exactly
`<domain>.json` — a file named anything else is inert by design.

## Status

| Domain | Anchor file | Usable for calibration |
|---|---|---|
| crm | `crm.provisional.json` | **No** — see below |
| sales, finance, project_management, logistics | — | Not authored yet |

`crm.provisional.json` is deliberately *not* named `crm.json`. It was authored
alongside the rubric rather than graded by humans in the Week-5 session, and its
`human_rationale` entries cite the rubric ("Score 3 per rubric") — so it grades
the prompt against itself. §10.2 is explicit that where acceptance fails you
"revise the rubric and prompt — not the human grades", which only means something
if the grades came from somewhere else first.

It also cannot detect a broken judge. `judge.calibration.anchor_strength` reports
that a judge which ignores its input entirely and always answers **4** scores
100% within ±1 on `factual_correctness` and 90% on `format_adherence`, with no
directional flips — a pass on half the dimensions. Two of the four dimensions
carry only two distinct human scores.

`assert_anchor_set_usable()` now refuses such a set, so this cannot be turned
into a marker by accident. It is kept in the repo as the starting worksheet for
the real session, not as an input.

## What a usable set needs

Per domain, ≥10 anchors (§10.2), and per dimension:

1. **At least 3 distinct human scores.** A dimension graded only 5s and 3s
   cannot separate a judge that reasons from one that guesses in the middle.
2. **No constant score passes.** Checked mechanically; see
   `anchor_strength()`. If a flat 4 would pass a dimension, that dimension has
   no anchors at the ends of the scale.
3. **At least one `factual_correctness = 1`** — the platform returns a
   *wrong number*. This is the case §HC-3 exists to catch and the whole
   zero-tolerance rule rests on. An anchor set without it cannot detect a judge
   that never scores 1, which is the most consequential judge failure available.

Grades are reconciled between the two human graders **before** they enter the
file. This module has no notion of per-grader votes, only the reconciled score.

## Marker files

A pass writes `judge/.calibration/<domain>.passed.json`, stamped with the
`judge_fingerprint` it was earned under — `model_version`, `prompt_version`,
`mode`. A run whose fingerprint differs is treated as uncalibrated, because a
pass on one model and prompt revision does not vouch for another. Edit a
template and every marker correctly goes stale.
