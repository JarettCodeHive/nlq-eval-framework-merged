# SLA Compliance Rate Computed From the Wrong Group (3 questions — CRM only)

A support case counts as "resolved" in two different ways depending on
which column you check: its **status** column says `Resolved`, or it
has a **`resolved_at`** timestamp filled in. Both `Resolved`-status
cases *and* `Closed`-status cases carry a `resolved_at` timestamp —
`Closed` is a second, equally valid way a case finishes. The platform's
SQL filters by `status ILIKE '%resolved%'`, which only keeps the
`Resolved`-status cases and silently drops every `Closed` one — about a
third of all actually-resolved cases.

## Examples

| Question | Industry | Correct group | Platform's group |
|---|---|---|---|
| `CRM-T2-08-36` | Energy | `resolved_at IS NOT NULL` → **50.11%** | status-only → ~50.0% |
| `CRM-T2-08-38` | Manufacturing | `resolved_at IS NOT NULL` → **49.74%** | status-only → ~49% |

One related question goes further wrong, in a different way:
`CRM-RG-12-V01` (a rephrase asking for "share of support cases that met
the SLA deadline") drops the "resolved" filter from the denominator
entirely and divides by every support case regardless of status —
20,376 cases instead of 9,485 — landing at 23.21% instead of ~49.87%.
Same underlying confusion about what counts as "resolved," taken one
step further.

(A fourth sibling question in this family, `CRM-T2-08-37`, is **not**
in this category — it currently fails for a different reason, a flat
zero from a categorical-literal mismatch on the `industry` filter; see
`categorical_literal_mismatch_v1.0.0-fixed.md`. The filter mismatch
happens before the resolved/status confusion would even matter.)

## Where to look these up

Search by `question_id` in `release/v1.0.0-fixed/crm/qa_pairs/crm_qa_pairs.csv`
(question text and expected answer) and
`release/v1.0.0-fixed/crm/judge/20261014T033522Z/results.json`
(platform's actual answer, `exact_match_detail`, judge scores).

## Status

Not fixable in this evaluation. Treating `resolved_at IS NOT NULL` (a
case has a resolution date) as the definition of "resolved," rather
than the `status` label, would fix this on the platform's side — a case
can finish as either `Resolved` or `Closed` and both should count.
