# Project Management — Judge Failure Analysis (`v1.0.0-full-fixed2`)

**Run:** `release/v1.0.0-full-fixed2/project_management/judge/20261011T095609Z/results.json`
**Exact match:** eligible 179 · pass 125 · fail 54 · **pass_pct 69.83%**
**Target:** ≥ 90% · **Gap:** 54 failing questions, need ≥36 more passes to clear 90% (161/179)

This is an analysis of every `exact_match_result == "fail"` row (54 of them). Each
is classified by root cause using the platform's own LLM-judge
`factual_correctness` score/rationale as a cross-check: a high judge score with
a rationale confirming the number is right is strong evidence the **scorer**
is wrong; a low judge score agreeing the platform's number is wrong is
evidence the **platform** is wrong. No code was changed to produce this file —
analysis only.

Six categories, summing to all 54 fails. Three recur across other domains and
are written up in the cross-domain file (`docs/findings/judge_failures_common_patterns_v1.0.0-full-fixed2.md`).

---

## Category 1 — Zero-result: multi-word status/location phrase not translated (22, platform-side)

**Fixability: not fixable in this codebase.** The platform's NLQ→SQL layer
fails specifically when the question phrases a filter value as spaced words
(`"in progress status"`, `"not started status"`, `"on hold projects"`,
`"Asia Pacific location"`, `"North America location"`) that correspond to a
smashed-together, multi-word stored value (`InProgress`, `NotStarted`,
`OnHold`, `AsiaPacific`, `NorthAmerica`). Every failure in this category
returns a literal `0` or `null` for the primary metric, and the judge
independently scores `factual_correctness = 1` and confirms the real
underlying answer is nonzero. Single-word status values (`Active`,
`Completed`, `Cancelled`, `Planning`) are **not** affected — see Category 2,
where those filters apply correctly but a different bug shows up.

**Status-filter instances (19):** `PM-T1-03-12`, `PM-T1-03-13`, `PM-T2-03-12`,
`PM-T2-03-13`, `PM-T2-05-22`, `PM-T2-06-26`, `PM-T2-06-27`, `PM-T3-01-04`,
`PM-T3-03-14`, `PM-T3-04-19`, `PM-T4-01-03`, `PM-T4-01-04`, `PM-T4-05-22`,
`PM-T4-06-26`, `PM-T4-06-27`, `PM-T5-01-04`, `PM-T5-06-20`, `PM-T5-07-23`,
`PM-T5-07-24`.

**Location-filter instances (3):** `PM-T2-09-35`, `PM-T2-09-37`, `PM-T3-09-31`.

**Worked example — `PM-T1-03-12`:** *"How many tasks are currently in in
progress status?"* Expected `3408`. Platform: *"There are currently no tasks
showing an 'in progress' status — the query returned a count of 0."* The
platform's own words show it recognized the phrase but still filtered on it
incorrectly against the stored `InProgress` enum.

**Worked example — `PM-T2-09-35`:** *"How many task assignments involve
resources in the Asia Pacific location?"* Expected `6548`. Platform:
*"...the query returned a total of 0... This could mean there are genuinely
no Asia Pacific resources... or it could reflect how location data is
recorded..."* — the platform itself flags the likely cause (a label/value
mismatch) without resolving it.

**Worked example — `PM-T3-03-14`:** *"How many overdue tasks belong to on
hold projects?"* Expected `266`. Platform: *"There are no overdue tasks
belonging to on-hold projects... "* — same `OnHold` phrase-translation
failure, just composed with an additional `overdue` predicate (Category 2
below). The zero wins over the inflation bug because the status filter
excludes every row before the date predicate is even reached.

---

## Category 2 — Overdue-count inflation: reference-date drift (7, dataset/process)

**Fixability: not a code fix — a methodology/process gap.** Every PM
"overdue" fact is generated against a **frozen** `reference_today` of
`2026-08-01` (`config/generation/project_management/generation.json`:
`"overdue": "due_date < reference_today AND completed_date IS NULL"`). This
judge run executed on **2026-10-11**, roughly ten weeks later. If the live
platform evaluates "overdue" against its own current date rather than
honoring the dataset's frozen reference point, every overdue count will be
systematically **higher** than the ground truth, because additional tasks
have crossed their `due_date` in the ten weeks since generation — which is
exactly the pattern observed:

| Question | Status filter | Expected | Platform said |
|---|---|---|---|
| `PM-T3-03-11` / `PM-RG-06-V01` / `PM-RG-06-V02` | Active | 1,328 | 1,509 |
| `PM-T3-03-12` | Cancelled | 186 | 210 |
| `PM-T3-03-13` | Completed | 747 | 860 |
| `PM-T3-03-15` | Planning | 349 | 390 |
| `PM-T5-04-12` | (no project-status filter, task-type ranking) | Bug 598 / Testing 591 / Deployment 576 | Bug 677 / Testing 670 / Deployment 638 |

Every one of the six single/no-status-filter overdue questions is inflated
by a similar relative margin (~12–15%), and the judge scores these
`factual_correctness = 1` while still noting the number looks internally
plausible — it's wrong relative to the frozen ground truth, not implausible
on its own. This is the same underlying `due_date` column either way; the
discrepancy is purely about which "today" is used to evaluate it.

**What would actually resolve this:** regenerate the dataset/Q&A release
(or re-peg `reference_today`) close to the actual date a judge run executes,
or confirm whether the platform can be told to treat a specific date as "now"
for this evaluation. Neither is an `exact_match.py` change — this is a
scheduling/process constraint on how far apart generation and judging can
drift, and is called out in the cross-domain file since the same frozen-date
mechanism underlies every domain's "overdue"/"current"-style questions.

---

## Category 3 — `non_billable` label not recognized (11, scorer-side — known hard problem)

**Fixability: open, not a quick fix.** The platform writes `"non-billable"`
(hyphen) for the dataset's `non_billable` enum value, and the judge
independently rates these `factual_correctness = 5` — the numbers are right.
This is the exact "`billable`-is-a-substring-of-`non-billable`" ownership
confound identified and deliberately left unresolved earlier in this
project's history: because the response text also contains the standalone
word `"billable"` (sometimes several times — by-resource breakdowns, totals,
etc.), the scorer's label-ownership logic in `judge/exact_match.py`
(`_find_label` / `_label_owns_a_value` / `_attachment_cost`) cannot reliably
tell which occurrence of `"billable"` inside the word `"non-billable"` is the
"real" structural pairing versus a free-standing mention of billable work. A
narrower fix was explored for this project before and rejected because it
was itself unreliable and risked turning *other* passing answers into false
passes elsewhere. Verified against the current codebase on both the
`full_documentation` branch and the `fix_exact_match_judge` branch (which
contains unrelated, already-landed `exact_match.py` hardening) — this
specific case fails identically on both, confirming it was never silently
fixed and remains an open problem, not stale data.

**Instances (11):** `PM-T4-04-14`, `PM-T4-04-15`, `PM-T4-04-16`,
`PM-T4-04-17`, `PM-T4-04-18`, `PM-T4-07-28`, `PM-T4-07-29`, `PM-T4-07-30`,
`PM-T4-07-31`, `PM-T4-07-32`, `PM-RG-08-V01`.

**Worked example — `PM-T4-04-14`:** *"For resources in the delivery
department, how does the count of logged time entries break down by
billable status?"* Expected `billable | 20183; non_billable | 5684`.
Platform: *"...20,183 were marked billable and 5,684 were marked
non-billable..."* — both numbers present and correctly attached in plain
English, judge agrees (`factual_correctness = 5`), yet
`exact_match_detail` reports `label 'non_billable' not present`.

**Worked example — `PM-RG-08-V01`:** detail is
`label 'billable' present but 252 chars from any of its values (limit 120);
label 'non_billable' not present` — here even the easier `billable` label
loses its ownership check in a longer response, showing the attachment-cost
distance heuristic (120-char limit) is also too tight for this domain's
longer prose, compounding the underscore/hyphen problem.

---

## Category 4 — Trend records missing the derived percentage/delta figure (11, dataset/pair contract)

**Fixability: fixable, but in `qa_pairs/`, not `judge/exact_match.py`.** The
T5 quarter-over-quarter trend family's `expected_answer` contract is five
fields — `Q1_total | Q2_total | delta_abs | delta_pct | direction` — but the
platform's natural-language answers reliably state the first three and the
direction, and usually never spell out the computed percentage change as a
standalone number. The judge scores these `factual_correctness = 4`
(materially correct, nothing factually wrong), while `exact_match` fails
because the literal `delta_pct` string genuinely does not appear anywhere in
the text — not a scorer bug, the number is just never written.

**Instances (11):** `PM-T5-02-06`, `PM-T5-02-07`, `PM-T5-02-08`,
`PM-T5-02-09`, `PM-T5-02-10`, `PM-T5-05-13`, `PM-T5-05-15`, `PM-T5-05-16`,
`PM-T5-05-17`, `PM-RG-10-V01`, `PM-RG-16-V01`.

**Worked example — `PM-T5-02-06`:** Expected
`11886.49 | 15966.20 | 4079.71 | 34.32 | up`. Platform: *"...rose from
11,886.49 hours in Q1 2026... to 15,966.20 hours in Q2 2026..., an increase
of 4,079.71 hours..."* — three of five fields present verbatim, direction
("rose"/"up") implied and accepted, but `34.32` (the percentage) is simply
never computed or stated by the platform anywhere in the response.

**Secondary scorer defect inside this category — `PM-RG-16-V01` only:** its
detail is `label 'up' present but None chars from any of its values (limit
120)` — note `None`, not a number. `judge/exact_match.py:902` formats this
message with the attachment distance (`nearest`), which is left unset for
labels resolved through `_find_direction()` rather than the normal
`_find_label()` path. This is a real, narrow scorer bug (a cosmetic
message defect today, since the record still correctly fails for missing
`delta_abs`/`delta_pct`, but worth fixing for message correctness on any
future direction-label record where distance actually matters) and is
distinct from the missing-percentage issue every other row in this category
has.

**What would resolve this:** either drop the `delta_pct` field from this
family's `expected_answer` contract (the platform isn't being asked for it
explicitly, so grading on it is testing phrasing, not correctness — the
non-determinism boundary this project's rubric is built around), or rephrase
the question to explicitly request "...and by what percentage?" so the
platform has a clear prompt to compute and state it.

---

## Category 5 — Platform drops a stated filter and returns the global aggregate (2, platform-side)

**Fixability: not fixable in this codebase.** Both questions ask for a
breakdown scoped to one resource role or one project status; the platform
instead answers with the **unscoped, dataset-wide** totals — recognizable
because those totals exactly match other questions' *global* expected
answers.

- **`PM-T4-03-09`** — *"For analyst resources, how does task assignment
  count break down by task status?"* Expected `Completed | 1960; InProgress
  | 1879; NotStarted | 958; Blocked | 670` (analyst-only, ~5,467 total).
  Platform answered `Completed 10,813 / InProgress 10,196 / NotStarted 5,310
  / Blocked 3,681` (30,000 total, every resource role combined) — `10,196`
  and `5,310` are themselves the *global* expected answers to `PM-T2-03-12`
  and `PM-T2-03-13` elsewhere in this same run. The `analyst` filter was
  dropped entirely.
- **`PM-T4-05-23`** — same shape for *"planning projects"*: expected
  `Completed | 6215; InProgress | 5549; NotStarted | 2461; Blocked | 1810`;
  platform answered with a 151,500-entry grand total, an order of magnitude
  too large to be one project status's slice.

---

## Category 6 — Platform's primary figure contradicts its own breakdown (1, platform-side)

**Fixability: not fixable in this codebase.**
**`PM-T2-05-23`** — *"What percentage of milestones for planning projects
have been completed?"* Expected `47.44`. The platform's primary result says
**100%**, but in the same response its own detailed breakdown reports 111
Completed / 123 Not Completed out of 234 total milestones —
`111 / 234 = 47.44%`, matching the expected answer almost exactly. The
platform even flags the inconsistency itself: *"this figure appears
inconsistent with the detailed completion-status breakdown... a data
processing or aggregation issue rather than true completion performance."*
The platform's secondary breakdown is right; its primary aggregate query is
wrong. Nothing in this codebase can correct Pulse's own internal
inconsistency.

---

## Summary

| Category | Count | Fixability |
|---|---|---|
| 1. Zero-result: multi-word status/location phrase not translated | 22 | Platform-side — not fixable here |
| 2. Overdue-count inflation: reference-date drift | 7 | Dataset/process — needs re-pegged `reference_today` or a judge-run-time regeneration, not a code fix |
| 3. `non_billable` label not recognized | 11 | Scorer-side — known hard problem, open, not a quick fix |
| 4. Trend records missing derived percentage/delta | 11 | Dataset/pair contract — fixable in `qa_pairs/` (drop or re-elicit the field), not `exact_match.py` (1 of the 11 also carries an unrelated small scorer message defect) |
| 5. Platform drops a stated filter | 2 | Platform-side — not fixable here |
| 6. Platform self-contradiction | 1 | Platform-side — not fixable here |
| **Total** | **54** | — |

Of 54 failures, only Category 3 (11) sits in `judge/exact_match.py` itself,
and it is explicitly an open, previously-rejected-as-unreliable fix — not a
quick win. Category 4 (11) is addressable, but as a `qa_pairs/` contract
change, not a scorer change. The largest block, Category 1 (22), plus
Categories 5 and 6 (3 more), are platform behavior this codebase has no
lever over. Category 2 (7) is a scheduling artifact of how far judge runs
drift from dataset generation, not a bug in either system. Realistically,
closing the gap to 90% requires platform-side fixes (status/location phrase
translation chief among them, worth 22 of the 54 on its own) more than
anything available in this repository.
