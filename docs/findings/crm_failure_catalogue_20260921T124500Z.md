# CRM evaluation — failure catalogue, run `20260921T124500Z`

160 pairs, live Pulse (org 4104), judge `floodgate/anthropic.claude-sonnet-4-6`.
PREVIEW / uncalibrated — **not a baseline** (§10.2, §11.3).

> **Read this first.** The dataset loaded in org 4104 is **not** the
> `dataset-v1.0.0` these Q&A pairs were generated against. Until it is
> re-uploaded and the run repeated, the headline number is not a measurement of
> Pulse. Evidence in §1. 70 of the 99 failures cannot be attributed to the
> platform until then.

- exact-match: **61/160 = 38.1%**
- judge overall: **3.23/5** (factual 2.62, completeness 3.29, format 3.42, sql 3.59)
- platform errors: 0 · judge errors: 0 · 160/160 answered

| tier | n | pass | pct | judge factual |
|---|---:|---:|---:|---:|
| ALL | 160 | 61 | 38.1 | 2.62 |
| T1 | 32 | 24 | 75.0 | 4.00 |
| T2 | 40 | 14 | 35.0 | 2.40 |
| T3 | 32 | 23 | 71.9 | 3.88 |
| T4 | 32 | 0 | 0.0 | 1.09 |
| T5 | 24 | 0 | 0.0 | 1.54 |

## 1. The dataset in Pulse is the wrong build — this dominates everything else

For several failures the reference SQL and Pulse's generated SQL are
semantically **identical**, yet the answers differ. Identical queries returning
different numbers means the data differs, not the query.

```sql
-- CRM-T3-03-13, ground truth AND Pulse both ran effectively this:
SELECT COUNT(*) FROM contacts c
  LEFT JOIN interactions i ON c.contact_id = i.contact_id
WHERE c.title = 'Business Analyst' AND i.interaction_id IS NULL;
```

Executing each pair's reference SQL against both builds:

| question | `dataset-v1.0.0` (ours) | `crm_dataset_v2-full` (older) | Pulse said |
|---|---:|---:|---:|
| CRM-T3-03-13 | 1678 | **1686** | **1686** |
| CRM-T2-07-29 | 4185 | **4178** | **4178** |
| CRM-T3-05-25 | 378 | **377** | **377** |
| CRM-T3-06-31 | 450 | **449** | **449** |

The older build reproduces **Pulse's** answer, not ours. Row counts are identical
in all six tables, so the mismatch is content, not shape — consistent with the
known divergence between the two builds in `campaigns`, `contact_campaigns`,
`interactions` and `support_cases` (`accounts` and `contacts` are byte-identical).

### What this does to the 99 failures

Each failure's reference SQL was run against both builds to classify it:

| Class | Count | Meaning |
|---|---:|---|
| DATA-MISMATCH | 17 | the older build reproduces Pulse's answer exactly — these disappear on re-upload |
| DATA-SENSITIVE | 53 | the two builds disagree, so no verdict is possible until the data matches |
| DATA-INDEPENDENT | 29 | both builds agree, so the failure is real today |

**70 of 99 failures are unattributable until the data is re-uploaded.**

Confirmed data mismatches: `CRM-T2-04-17`, `CRM-T2-04-18`, `CRM-T2-04-20`, `CRM-T2-07-29`, `CRM-T2-07-31`, `CRM-T2-07-32`, `CRM-T2-07-33`, `CRM-T2-07-34`, `CRM-T3-03-13`, `CRM-T3-03-14`, `CRM-T3-03-15`, `CRM-T3-03-16`, `CRM-T3-03-17`, `CRM-T3-03-18`, `CRM-T3-03-19`, `CRM-T3-05-25`, `CRM-T3-06-31`

## 2. Genuine platform findings — verifiable today

These 29 failures are data-independent: both dataset builds produce the
same ground truth, so Pulse's answer is wrong regardless of which build is loaded.

### 2a. Enum mapping: "high-priority" resolves to `Critical`

```sql
-- ground truth   WHERE priority = 'High'       -> 7550
-- Pulse          WHERE priority = 'Critical'   -> 2338
```

`CRM-T1-07-30` and `CRM-T2-02-07`, both ratios ≈3.23. The strongest single
finding in the run: `CRM-T1-07-30`'s reference SQL is a single-table `COUNT(*)`
with no join and no metric ambiguity, so the enum value is the only variable.

### 2b. Answered the aggregate instead of the requested ranking

`CRM-T2-06-*` and `CRM-T5-02-*` ask *which accounts* rank highest; Pulse replies
with a grand total.

- `CRM-T5-02-06` — expected `Brown LLC | 5; Gibson Ltd | 5; Johnson Nichols | 5; Long Chapman | 5; `
  · actual `There are currently 13,070 open access support cases across all accounts. This is a raw ticket count`
- `CRM-T2-06-24` — expected `Lopez Sanchez | 13; Gould and Sons | 12; Caldwell Edwards and Torres |`
  · actual `Across the CRM data, a total of 23,369 email open interactions were recorded against accounts. The b`

Right data, wrong shape of answer. Independent of the dataset question.

### 2c. Wrong filter value

`CRM-T1-01-05` asks for **retention** campaign budget. Pulse returned
`23,620,415.61`, which is the **Reengagement** total in both builds. Right shape,
wrong category selected.

### 2d. Date-sanity guard drops deliberately injected boundary rows

```sql
AND EXTRACT(YEAR FROM sla_due_ts) BETWEEN 2000 AND 2035
```

The dataset injects `1900-01-01`, `2038-01-19`, `2099-12-31` by design
(`imperfections.boundary_dates`). The guard excludes exactly those rows. A
legitimate design question for the Platform Owner rather than an obvious bug —
is silently discarding out-of-range dates correct? Either way, the imperfection
plan found a real behaviour.

Affected: `CRM-T1-07-31`, `CRM-T2-08-35`, `CRM-T2-08-36`, `CRM-T2-08-37`, `CRM-T2-08-38`, `CRM-T2-08-39`, `CRM-T2-08-40`, `CRM-T4-03-13`, `CRM-T4-03-14`, `CRM-T5-03-15`

### 2e. Clarification requests — 11 pairs

Pulse returned `clarify: true` instead of answering. For `Nurture` campaigns its
schema profile saw 3 of 5 `campaign_type` values and offered four "clarifying
options", none correct — the enum sampling is incomplete.

Affected: `CRM-T1-01-03`, `CRM-T1-03-12`, `CRM-T1-04-16`, `CRM-T1-05-22`, `CRM-T2-01-03`, `CRM-T2-04-19`, `CRM-T2-04-21`, `CRM-T4-03-15`, `CRM-T5-02-08`, `CRM-T5-04-19`, `CRM-T5-05-24`

## 3. Specification ambiguity — ours to fix, not the platform's

All of T4 (0/32) and T5 (0/24). Worked example, `CRM-T4-01-01`:

```sql
-- ground truth
SELECT i.channel, SUM(i.engagement_points) FROM accounts a
  INNER JOIN contacts c ON a.account_id = c.account_id
  INNER JOIN interactions i ON c.contact_id = i.contact_id
WHERE a.region = 'Central' GROUP BY i.channel;

-- Pulse
SELECT i.channel, COUNT(*) FROM studio.interactions i
  JOIN studio.accounts a ON i.account_id = a.account_id
WHERE a.region = 'Central' GROUP BY i.channel;
```

- **Metric.** "Engagement" is ambiguous. The pairs mean `SUM(engagement_points)`;
  Pulse counted rows, and says so: *"the figures represent interaction counts
  rather than…"*
- **Join path.** Ground truth reaches interactions via `contacts`; Pulse joins
  `interactions.account_id` directly. That column is nullable while the contact
  still belongs to the account, so the row sets differ.

These questions are unanswerable as written. They must name the metric and
constrain the join path. `qa_pairs/taxonomy/crm_allowed_join_paths.csv` exists but
is not reaching the question text.

## 4. Actions, in order

1. **Re-upload `release/crm/dataset-v1.0.0` to org 4104 and re-run.** Blocks
   everything else. Removes ~17 failures outright and makes 53 more decidable.
2. **Do not establish a baseline from this run.** `establish_baseline` is
   write-once per `platform_version`; freezing 38.1% would permanently encode a
   dataset mismatch and a spec ambiguity.
3. **Score clarifications as their own outcome** (judge side) so a declined answer
   stops counting as a wrong one. Must land before the first baseline.
4. **Rewrite T4/T5 questions** to name metric and join path (Q&A author).
5. **Raise with the Platform Owner:** enum mapping (2a), aggregate-vs-ranking (2b),
   wrong filter value (2c), date guard (2d), incomplete enum sampling (2e).
6. **§9.5 rephrase agreement did not run** — 0 groups, because the release package
   carries no `rephrase_group_id`. A spec'd check is silently inactive.

## Appendix — per-failure attribution

| question | tier | class |
|---|---|---|
| `CRM-T1-01-03` | T1 | DATA-INDEPENDENT |
| `CRM-T1-01-05` | T1 | DATA-INDEPENDENT |
| `CRM-T1-03-12` | T1 | DATA-INDEPENDENT |
| `CRM-T1-04-16` | T1 | DATA-INDEPENDENT |
| `CRM-T1-05-20` | T1 | DATA-INDEPENDENT |
| `CRM-T1-05-22` | T1 | DATA-INDEPENDENT |
| `CRM-T1-07-30` | T1 | DATA-INDEPENDENT |
| `CRM-T1-07-31` | T1 | DATA-INDEPENDENT |
| `CRM-T2-01-03` | T2 | DATA-INDEPENDENT |
| `CRM-T2-02-07` | T2 | DATA-INDEPENDENT |
| `CRM-T2-04-17` | T2 | DATA-MISMATCH |
| `CRM-T2-04-18` | T2 | DATA-MISMATCH |
| `CRM-T2-04-19` | T2 | DATA-SENSITIVE |
| `CRM-T2-04-20` | T2 | DATA-MISMATCH |
| `CRM-T2-04-21` | T2 | DATA-SENSITIVE |
| `CRM-T2-05-22` | T2 | DATA-SENSITIVE |
| `CRM-T2-05-23` | T2 | DATA-SENSITIVE |
| `CRM-T2-06-24` | T2 | DATA-SENSITIVE |
| `CRM-T2-06-25` | T2 | DATA-SENSITIVE |
| `CRM-T2-06-26` | T2 | DATA-SENSITIVE |
| `CRM-T2-06-27` | T2 | DATA-SENSITIVE |
| `CRM-T2-06-28` | T2 | DATA-SENSITIVE |
| `CRM-T2-07-29` | T2 | DATA-MISMATCH |
| `CRM-T2-07-30` | T2 | DATA-SENSITIVE |
| `CRM-T2-07-31` | T2 | DATA-MISMATCH |
| `CRM-T2-07-32` | T2 | DATA-MISMATCH |
| `CRM-T2-07-33` | T2 | DATA-MISMATCH |
| `CRM-T2-07-34` | T2 | DATA-MISMATCH |
| `CRM-T2-08-35` | T2 | DATA-INDEPENDENT |
| `CRM-T2-08-36` | T2 | DATA-INDEPENDENT |
| `CRM-T2-08-37` | T2 | DATA-INDEPENDENT |
| `CRM-T2-08-38` | T2 | DATA-INDEPENDENT |
| `CRM-T2-08-39` | T2 | DATA-INDEPENDENT |
| `CRM-T2-08-40` | T2 | DATA-INDEPENDENT |
| `CRM-T3-03-13` | T3 | DATA-MISMATCH |
| `CRM-T3-03-14` | T3 | DATA-MISMATCH |
| `CRM-T3-03-15` | T3 | DATA-MISMATCH |
| `CRM-T3-03-16` | T3 | DATA-MISMATCH |
| `CRM-T3-03-17` | T3 | DATA-MISMATCH |
| `CRM-T3-03-18` | T3 | DATA-MISMATCH |
| `CRM-T3-03-19` | T3 | DATA-MISMATCH |
| `CRM-T3-05-25` | T3 | DATA-MISMATCH |
| `CRM-T3-06-31` | T3 | DATA-MISMATCH |
| `CRM-T4-01-01` | T4 | DATA-SENSITIVE |
| `CRM-T4-01-02` | T4 | DATA-SENSITIVE |
| `CRM-T4-01-03` | T4 | DATA-SENSITIVE |
| `CRM-T4-01-04` | T4 | DATA-SENSITIVE |
| `CRM-T4-01-05` | T4 | DATA-SENSITIVE |
| `CRM-T4-02-06` | T4 | DATA-SENSITIVE |
| `CRM-T4-02-07` | T4 | DATA-SENSITIVE |
| `CRM-T4-02-08` | T4 | DATA-SENSITIVE |
| `CRM-T4-02-09` | T4 | DATA-SENSITIVE |
| `CRM-T4-02-10` | T4 | DATA-SENSITIVE |
| `CRM-T4-02-11` | T4 | DATA-SENSITIVE |
| `CRM-T4-02-12` | T4 | DATA-SENSITIVE |
| `CRM-T4-03-13` | T4 | DATA-SENSITIVE |
| `CRM-T4-03-14` | T4 | DATA-SENSITIVE |
| `CRM-T4-03-15` | T4 | DATA-SENSITIVE |
| `CRM-T4-04-16` | T4 | DATA-INDEPENDENT |
| `CRM-T4-04-17` | T4 | DATA-INDEPENDENT |
| `CRM-T4-04-18` | T4 | DATA-INDEPENDENT |
| `CRM-T4-04-19` | T4 | DATA-INDEPENDENT |
| `CRM-T4-04-20` | T4 | DATA-INDEPENDENT |
| `CRM-T4-05-21` | T4 | DATA-SENSITIVE |
| `CRM-T4-05-22` | T4 | DATA-SENSITIVE |
| `CRM-T4-05-23` | T4 | DATA-SENSITIVE |
| `CRM-T4-05-24` | T4 | DATA-SENSITIVE |
| `CRM-T4-05-25` | T4 | DATA-SENSITIVE |
| `CRM-T4-06-26` | T4 | DATA-SENSITIVE |
| `CRM-T4-06-27` | T4 | DATA-SENSITIVE |
| `CRM-T4-06-28` | T4 | DATA-SENSITIVE |
| `CRM-T4-06-29` | T4 | DATA-SENSITIVE |
| `CRM-T4-06-30` | T4 | DATA-SENSITIVE |
| `CRM-T4-06-31` | T4 | DATA-SENSITIVE |
| `CRM-T4-06-32` | T4 | DATA-SENSITIVE |
| `CRM-T5-01-01` | T5 | DATA-SENSITIVE |
| `CRM-T5-01-02` | T5 | DATA-SENSITIVE |
| `CRM-T5-01-03` | T5 | DATA-SENSITIVE |
| `CRM-T5-01-04` | T5 | DATA-SENSITIVE |
| `CRM-T5-01-05` | T5 | DATA-SENSITIVE |
| `CRM-T5-02-06` | T5 | DATA-INDEPENDENT |
| `CRM-T5-02-07` | T5 | DATA-INDEPENDENT |
| `CRM-T5-02-08` | T5 | DATA-INDEPENDENT |
| `CRM-T5-02-09` | T5 | DATA-INDEPENDENT |
| `CRM-T5-02-10` | T5 | DATA-INDEPENDENT |
| `CRM-T5-02-11` | T5 | DATA-INDEPENDENT |
| `CRM-T5-03-12` | T5 | DATA-SENSITIVE |
| `CRM-T5-03-13` | T5 | DATA-SENSITIVE |
| `CRM-T5-03-14` | T5 | DATA-SENSITIVE |
| `CRM-T5-03-15` | T5 | DATA-INDEPENDENT |
| `CRM-T5-03-16` | T5 | DATA-SENSITIVE |
| `CRM-T5-03-17` | T5 | DATA-INDEPENDENT |
| `CRM-T5-04-18` | T5 | DATA-SENSITIVE |
| `CRM-T5-04-19` | T5 | DATA-SENSITIVE |
| `CRM-T5-04-20` | T5 | DATA-SENSITIVE |
| `CRM-T5-04-21` | T5 | DATA-SENSITIVE |
| `CRM-T5-05-22` | T5 | DATA-SENSITIVE |
| `CRM-T5-05-23` | T5 | DATA-SENSITIVE |
| `CRM-T5-05-24` | T5 | DATA-SENSITIVE |

_Generated from `release/crm/eval-runs/20260921T124500Z/` on 2026-09-21._
