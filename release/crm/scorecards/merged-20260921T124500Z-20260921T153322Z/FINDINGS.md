# CRM evaluation — findings and actions

Accompanies the scorecard in this folder. Runs: `20260921T124500Z` (full 160)
and `20260921T153322Z` (55-pair re-run after the dataset re-upload).

**PREVIEW / uncalibrated — this is not a baseline** (§10.2, §11.3). It is
assembled from two runs against two different platform states.

## Where we are

| tier | n | pass | pct |
|---|---:|---:|---:|
| ALL | 160 | 88 | 55.0 |
| T1 | 32 | 26 | 81.2 |
| T2 | 40 | 30 | 75.0 |
| T3 | 32 | 32 | 100.0 |
| T4 | 32 | 0 | 0.0 |
| T5 | 24 | 0 | 0.0 |

Before the dataset re-upload this was 61/160 = 38.1%. The re-upload cleared 27
failures — the earlier run was scoring answers from one dataset build against
ground truth from another.

**T3 is now 100%, T1 81%, T2 75%. The entire remaining gap is T4 and T5.**

## Q&A side — 35 pairs, three definitions to add

In each case the platform and the reference SQL read the question differently
and *both readings are defensible*. The question text and the reference SQL need
to agree; which way they agree matters less.

### A. Define "engagement" — 24 pairs

```
ours  : SUM(i.engagement_points)
pulse : COUNT(*)            -- counts interaction rows
```

Affects every question phrased "how does engagement break down…" / "drove the
most engagement" / "how did engagement change…". Either say **total engagement
points** in the question, or move the ground truth to an interaction count.

Pairs: `CRM-T4-01-01`, `CRM-T4-01-02`, `CRM-T4-01-03`, `CRM-T4-01-04`, `CRM-T4-01-05`, `CRM-T4-06-26`, `CRM-T4-06-27`, `CRM-T4-06-28`, `CRM-T4-06-29`, `CRM-T4-06-30`, `CRM-T4-06-31`, `CRM-T4-06-32`, `CRM-T5-01-01`, `CRM-T5-01-02`, `CRM-T5-01-03`, `CRM-T5-01-04`, `CRM-T5-01-05`, `CRM-T5-04-18`, `CRM-T5-04-19`, `CRM-T5-04-20`, `CRM-T5-04-21`, `CRM-T5-05-22`, `CRM-T5-05-23`, `CRM-T5-05-24`

### B. `SUM` or `AVG` for attribution weight — 5 pairs

"Which contact titles carry the **most** multi-touch attribution weight" — the
reference SQL sums, the platform averaged. Highest total, or highest per touch?
Please state it.

Pairs: `CRM-T4-04-16`, `CRM-T4-04-17`, `CRM-T4-04-18`, `CRM-T4-04-19`, `CRM-T4-04-20`

### C. Define "at risk" and "recent" — 6 pairs

"Which accounts look **at risk** from an open support backlog and **no recent
engagement**" — the reference SQL hard-codes thresholds the question never
states, so the platform invented its own. Put them in the question text, e.g.
"≥N open cases and no interaction in the last M days".

Pairs: `CRM-T5-03-12`, `CRM-T5-03-13`, `CRM-T5-03-14`, `CRM-T5-03-15`, `CRM-T5-03-16`, `CRM-T5-03-17`

### Also outstanding on the Q&A side

- **`rephrase_group_id` is absent from the release rows**, so §9.5 rephrase
  agreement scored **0 groups and reported success** — a spec'd check is
  silently inactive. It also needs the 19 rephrase variants included as rows,
  plus a flag marking the release 160 so they stay out of the accuracy
  denominator.
- `qa_pairs/taxonomy/crm_allowed_join_paths.csv` exists but nothing in the
  question text constrains the join path. It matters: `interactions` has 37,248
  rows with a NULL `account_id`, and `support_cases` has 68,781 with a NULL
  `contact_id`, so the path chosen changes the answer materially.

## Platform side — not Q&A work, listed so the split is clear

- **Join path ignored where the question specifies it** (15 pairs). T4-02/03/05
  ask about "the support cases **raised by their contacts**"; the platform joined
  `support_cases.account_id` directly. For Education accounts that is 20,376 rows
  instead of 10,836.
- **Ranked "which X" questions answered with a grand total** (9 pairs).
- **Schema enum profiling incomplete** (5 pairs) — `Nurture`, `Retention`, `High`,
  `Meeting`, `Strategic` are missing from the profile but present in the data, and
  it is inconsistent within a single run.
- **SLA compliance denominator** (9 pairs) — on-time ÷ all cases (ours, 23.21%) vs
  on-time ÷ resolved cases (platform, 49.87%).
- **Date-sanity guard** — `BETWEEN 2000 AND 2035` silently drops the deliberately
  injected boundary dates.

## Expected effect

A, B and C together cover 35 of the 72 remaining failures. With the platform
items that would take the domain from 55% to roughly 90%.

## Evidence

- `scorecard_summary.csv` / `question_results.csv` — §11.1 / §11.2 in this folder
- `release/crm/eval-runs/<run_id>/pulse_raw/` — untouched platform payloads,
  including the SQL the platform generated for every question
- `docs/findings/crm_failure_catalogue_20260921T124500Z.md` — per-failure
  attribution for the original run

_Prepared 2026-09-21._
