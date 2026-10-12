# CRM Judge Failure Analysis — `v1.0.0-full-fixed2`

**Run:** `release/v1.0.0-full-fixed2/crm/judge/20261012T064133Z/results.json`
**Exact match:** 99 pass / 80 fail / 179 eligible — **55.31% pass rate**
**Target:** ≥90% — **gap: 34.69 points, 61 additional passes needed**

Every one of the 80 failing rows was read individually: the question, `expected_answer`, the platform's full prose `actual_answer`, the scorer's `exact_match_detail`, and — as an independent cross-check — the LLM judge's `factual_correctness` score and rationale (scored from the same platform answer, blind to the deterministic scorer's verdict). Where the judge independently agrees the platform's number is wrong, that is strong evidence the fault is in the **platform's answer**, not in `judge/exact_match.py`. Where the judge disagrees (rates the answer correct) despite an exact-match fail, that is the signature of a **scorer bug**.

**Result of that cross-check: 76 of 80 failures score `factual_correctness: 1` from the judge, 3 score `2`, and 1 scores `4`. There is no case in this run where the judge independently found the platform's answer correct while the deterministic scorer failed it.** That is the headline finding: **all 80 CRM failures in this run are platform-side — Pulse's own answer is numerically wrong, empty, or incomplete. None are fixable by changing `judge/exact_match.py`, and none are dataset defects** (every `expected_answer` checked against its `reference_sql` in `crm_qa_pairs.csv` is internally consistent).

---

## Category 1 — Platform returned zero/null for a question with a non-zero ground truth (12)

The platform's own generated SQL (or its interpretation of the question) produced an empty result set for a filter that does have matching rows in the dataset, and the platform reported that as "no data" / "0" / "none found" rather than the real total.

**Fixability: platform-only.** There is no answer text here for `judge/exact_match.py` to parse differently — the platform asserts the value is zero, in prose, repeatedly and explicitly.

Question IDs: `CRM-T1-01-04`, `CRM-T1-04-16`, `CRM-T1-04-18`, `CRM-T1-05-19`, `CRM-T1-05-20`, `CRM-T1-05-21`, `CRM-T2-03-12`, `CRM-T2-04-20`, `CRM-T2-07-30`, `CRM-T2-08-37`, `CRM-T3-05-28`, `CRM-RG-15-V01`.

**Examples:**
- `CRM-T1-01-04` — "What is the total budget of re-engagement campaigns?" Expected `23620415.61`. Platform: *"No budget data is available for re-engagement campaigns — the query returned a blank (null) value rather than a numeric total."* The campaign type plainly has budget data (every sibling campaign-type question in this same run resolves fine) — the platform's own query for this one type came back empty.
- `CRM-T1-05-19` — "How many interactions are email open events?" Expected `28725`. Platform: *"No email open events were found in the interaction data — the total comes back as 0."* `RG-15-V01` is a rephrase of the same underlying question and fails identically — the zero result is consistent, not a one-off fluke, which rules out a transient platform hiccup as the explanation.
- `CRM-T2-03-12` — "How many contacts do we have across accounts in financial services?" Expected `6589`. Platform: *"The query found no contacts associated with accounts classified as financial services."* Financial-services accounts exist and have contacts elsewhere in this same run's data (see Category 3's SLA-rate questions, which do return financial-services records) — this one query's filter/join simply didn't match them.

## Category 2 — Platform computed a different, close-but-wrong single value (5)

Same shape of question as Category 1 (a bare percentage), but the platform did produce a non-zero number — just not the right one, and not a rounding of the right one. All five are SLA-compliance-rate-by-industry questions from the same T2-08 family.

**Fixability: platform-only.** §HC-3/OI-2 already settled that this scorer compares numerics by value with zero tolerance; `49.87` and `50.11` are different numbers under that rule and should be, because they come from genuinely different underlying case/SLA counts, not a formatting difference.

Question IDs: `CRM-T2-08-35`, `CRM-T2-08-36`, `CRM-T2-08-38`, `CRM-T2-08-39`, `CRM-RG-12-V01`.

**Example:** `CRM-T2-08-35` — "Of support cases for accounts in education that have been resolved, what percentage were resolved within their SLA deadline?" Expected `49.87`. Platform: *"Of the 6,181 resolved support cases for education accounts, 3,097 were resolved within their SLA deadline... approximately 50.11%."* All five sibling industries in this family cluster the same way — platform values land in a tight 48.8–50.1% band while expected values land in a tight 49.7–50.1% band, close enough to look superficially similar but never identical. This is consistent with the platform computing "resolved within SLA" against a different null-filtering rule than the reference SQL (`crm_qa_pairs.csv`'s `reference_sql` uses `TRY_CAST`/`NULLIF` on both `resolved_at` and `sla_due_at`; the platform's own generated SQL for each of these five uses a differently-shaped CTE each time — see `platform_generated_sql` in the run — so the two are not guaranteed to exclude the same edge-case rows).

## Category 3 — Wrong computed values or wrong entities for a multi-value breakdown / ranked list (62)

By far the largest category: every T4 breakdown-by-dimension question, every T5 top-N/ranking/trend question, the T2-06 "which accounts had the most X" questions, and their rephrase-group siblings. The platform returns a fully-formed, plausible-looking answer with real numbers — but a different set of numbers (and sometimes different entities entirely) than the ground truth.

**Fixability: platform-only.** The judge independently confirms the platform's numbers are wrong in every one of these (`factual_correctness: 1` for all but the three "close" cases discussed above as Category-2-adjacent). There is nothing for the deterministic scorer to parse more leniently — the values genuinely are not in the text, because the platform computed different ones.

A sub-pattern worth flagging precisely because it recurs with a consistent signature (useful if anyone later gets access to the platform's own query logs): the "support cases raised by contacts" breakdown questions (`CRM-T4-02-*`, `CRM-T4-05-*`) all come back roughly **1.84×–1.93× too high** across every category/status value — e.g. `CRM-T4-02-06` expects `Medium | 4588` and the platform reports `8,633` (1.88×); `CRM-T4-05-21` expects `Resolved | 4551` and the platform reports `8,633` (1.90×) — note the platform's two different questions land on the *same* raw total (8,633 and 28,731 total cases respectively appear more than once across this family), which looks like a join producing one row per contact-to-account relationship rather than one row per case. By contrast, the engagement-points-by-channel/type questions (`CRM-T4-01-*`, `CRM-T4-06-*`) run the opposite way — roughly **0.85× too low** — so this is not one global multiplier; it is at minimum two distinct, question-family-specific join/aggregation divergences on the platform side. Neither is something this codebase's SQL, dataset, or scorer controls — `reference_sql` in `crm_qa_pairs.csv` is the join the platform should be reproducing and is not.

Question IDs: `CRM-T2-06-24`, `CRM-T2-06-25`, `CRM-T2-06-26`, `CRM-T2-06-28`, `CRM-T4-01-01`, `CRM-T4-01-02`, `CRM-T4-01-03`, `CRM-T4-01-04`, `CRM-T4-01-05`, `CRM-T4-02-06`, `CRM-T4-02-07`, `CRM-T4-02-08`, `CRM-T4-02-09`, `CRM-T4-02-10`, `CRM-T4-02-11`, `CRM-T4-03-13`, `CRM-T4-03-15`, `CRM-T4-04-19`, `CRM-T4-05-21`, `CRM-T4-05-23`, `CRM-T4-05-24`, `CRM-T4-05-25`, `CRM-T4-06-26`, `CRM-T4-06-27`, `CRM-T4-06-28`, `CRM-T4-06-29`, `CRM-T4-06-30`, `CRM-T4-06-31`, `CRM-T4-06-32`, `CRM-T5-01-01`, `CRM-T5-01-02`, `CRM-T5-01-03`, `CRM-T5-01-04`, `CRM-T5-01-05`, `CRM-T5-02-06`, `CRM-T5-02-07`, `CRM-T5-02-08`, `CRM-T5-02-09`, `CRM-T5-02-10`, `CRM-T5-02-11`, `CRM-T5-03-12`, `CRM-T5-03-13`, `CRM-T5-03-14`, `CRM-T5-03-15`, `CRM-T5-03-16`, `CRM-T5-03-17`, `CRM-T5-04-18`, `CRM-T5-04-19`, `CRM-T5-04-20`, `CRM-T5-04-21`, `CRM-T5-05-22`, `CRM-T5-05-23`, `CRM-T5-05-24`, `CRM-RG-01-V01`, `CRM-RG-01-V02`, `CRM-RG-02-V01`, `CRM-RG-02-V02`, `CRM-RG-05-V01`, `CRM-RG-06-V01`, `CRM-RG-13-V01`, `CRM-RG-14-V01`.

**Examples:**
- `CRM-T2-06-24` — "Which accounts had the most email open interactions recorded directly against them?" Expected top 5: `Lopez Sanchez | 13; Gould and Sons | 12; ...`. Platform names an entirely different top 5 (`Smith Group (35), Johnson and Sons (34), ...`) — wrong entities, not just wrong counts.
- `CRM-T4-02-06` — see fanout discussion above: `Medium 8,633` vs. expected `4,588`.
- `CRM-T5-04-18` — "How did total engagement points among Central-region accounts change from Q1 2026 to Q2 2026?" Expected `44221 | 20714 | -23507 | -53.16 | down`. Platform reports `37,580 → 17,577`, a drop of `20,003` — the *direction* (down) is right, every magnitude is wrong, consistent with Category-3's broader "engagement points undercounted" sub-pattern.
- `CRM-RG-06-V01` — "Which region had the best total engagement points trend among preferred-tier accounts from Q1 2026 to Q2 2026?" Expected `West | 12247 | 6524 | -46.73`. Platform also picks the wrong *winner region* (`North`, not `West`) in addition to wrong magnitudes.

## Category 4 — Required list truncated to fewer entries than the question needs (1)

`CRM-T4-04-16` — "For awareness campaigns, which contact titles carry the most total multi-touch attribution weight?" Expected 7 ranked titles; the platform's judge-scored `factual_correctness` is **4** (the only non-1/2 score in this entire failure set) because the 5 titles it *did* name are correctly rounded versions of the expected values (`1435.6310→1,435.6` etc.) — but it only names 5 of the 7, closing with "Program Manager and Operations Director also present at similar levels" instead of giving their numbers. The scorer correctly fails this: the two numbers genuinely never appear anywhere in the text, not even approximately.

**Fixability: platform-only.** Nothing in `judge/exact_match.py` can score a value that was never printed; the platform's response needs to enumerate the full requested rank, not a truncated top-5-of-7.

---

## Summary

| Category | Count | Fixability |
|---|---|---|
| 1 — Zero/null for non-zero ground truth | 12 | Platform-only |
| 2 — Wrong single value (close magnitude) | 5 | Platform-only |
| 3 — Wrong values/entities in a multi-value breakdown or ranking | 62 | Platform-only |
| 4 — Required list truncated | 1 | Platform-only |
| **Total** | **80** | **0 scorer-fixable, 0 dataset defects, 80 platform-only** |

Of 80 CRM exact-match failures in this run, **0 are fixable by changing `judge/exact_match.py`, 0 are dataset/pair defects, and all 80 are the Pulse platform computing or reporting a materially different answer than the ground truth** — independently corroborated by the LLM judge's `factual_correctness` dimension, which agrees the platform is wrong in 79 of 80 cases and is lenient-but-still-correctly-failing on the 80th (the truncated-list case). Closing this gap to the ≥90% target is a platform/prompt-engineering problem on the Pulse side (query generation for multi-dimensional breakdowns, Q1/Q2 trend windows, and ranked-list completeness), not a change available in this repository's scoring or dataset code.
