# Judge Prompt Templates

The Jinja2 templates that render the actual prompt sent to the judge model
(Floodgate/Anthropic). Rendered by `judge/prompts.py` with `StrictUndefined`
— a template referencing a variable that wasn't supplied raises at render
time rather than silently rendering empty, the same discipline `qa_pairs/`
uses for its SQL templates.

## Files

- **`combined.jinja`** — the default prompt: all four rubric dimensions
  scored in one model call. Includes `_rubric.jinja`,
  `_non_determinism_boundary.jinja`, and `_context.jinja`.
- **`per_dimension.jinja`** — scores exactly one dimension per call (the
  `--mode per_dimension` option). Not required by the Execution Spec — it
  costs 4× the model calls, but removes the halo effect where a poor score
  on one dimension drags the others down inside a single generation.
  Includes the matching file from `dimensions/`, plus
  `_non_determinism_boundary.jinja` and `_context.jinja`.
- **`dimensions/factual_correctness.jinja`**, **`completeness.jinja`**,
  **`format_adherence.jinja`**, **`sql_plausibility.jinja`** — the per-dimension
  rubric text used only by `per_dimension.jinja`'s `{% include %}`.
- **`_rubric.jinja`** — the shared rubric for `combined.jinja`: all four
  dimensions' 1–5 scales in one block. The 5/3/1 anchors are verbatim from
  Execution Spec §10's "Score scale" column; levels 2 and 4 are
  interpolated (not spec-defined) so the judge has something to reason
  toward — that interpolation is a delivery-team decision and must be
  signed off in the rubric PDF deliverable (§14.1).
- **`_non_determinism_boundary.jinja`** — the HC-3 boundary, with worked
  positive/negative examples. **Load-bearing, not decorative**: without the
  worked examples, judges consistently over-penalize phrasing and
  rendering variation (dropped cents, magnitude words like "$2.91 billion",
  spelled-out numerals), which corrupts every downstream regression
  comparison. If you add a new rounding/formatting case the deterministic
  scorer (`judge/exact_match.py`) was taught to accept, add a matching
  worked example here — the two must stay in agreement, or the judge and
  the deterministic scorer will disagree on cases that should pass both.
- **`_context.jinja`** — the evidence block (question, expected answer,
  judge reference, platform answer, platform-generated SQL) plus the
  anchoring rules: factual correctness is judged against the expected
  answer, completeness/format against the judge reference, SQL
  plausibility against the platform's own generated SQL. `reference_sql`
  is deliberately never included here — handing the judge the ground-truth
  query would let it infer correctness from the query shape rather than
  the platform's actual answer.

## Adding or changing a dimension

1. Edit the rubric text in both `_rubric.jinja` (the combined-mode copy)
   and `dimensions/<name>.jinja` (the per-dimension-mode copy) — they must
   stay in agreement; nothing enforces that mechanically.
2. If scoring behavior changed, update the worked examples in
   `_non_determinism_boundary.jinja` to match.
3. Regenerate the rubric PDF deliverable: `python main.py rubric` (see
   `judge/rubric.py` — it renders from these same templates, so the signed
   document can't drift from what's actually scored).
4. Re-run calibration (`python main.py calibrate --domain <domain>`) before
   any release run uses the changed rubric — a stale calibration marker
   does not get invalidated automatically.
