# Breakdown/Total Computed via Different Logic Than the Reference (95 questions — CRM 64, Sales 19, Finance 11, Logistics 1)

The largest category overall. The platform returns a complete,
confident-sounding answer with real numbers — just computed from a
different join, a different filter, or a different date boundary than
the reference query, so the numbers (and sometimes the ranked entities)
come out genuinely wrong. Entirely the live platform's own query
generation; the judge's independent `factual_correctness` score agrees
the numbers are wrong in nearly every instance below.

## CRM (64)

**Skipping the "contact" step undercounts or overcounts (the dominant
sub-pattern).** Many questions ask about activity "raised by" or "from"
an account's contacts. The correct path joins `accounts → contacts →
support_cases` (or `→ interactions`); the platform's SQL joins the
account directly to the case/interaction table, skipping the contacts
step:

- Overcounts when the shortcut includes rows it shouldn't. "Cases
  raised by their contacts" (Education) expects `Medium | 4588`; the
  platform reports `8,633` — about 1.9× too high, because it counts
  every case for the account, not just contact-raised ones.
- Undercounts when the shortcut column is sometimes blank. "Engagement
  points by channel" (Central region) expects `Phone | 84970`; the
  platform reports `72,782` — about 0.86× of correct, because the
  shortcut `account_id` column on interactions is blank for roughly 1
  in 5 rows that only carry a `contact_id`.

Both multipliers are stable and recur across every sibling question in
their families — systematic, not noise.

**Wrong entities named, not just wrong counts.** Ranking questions
("which accounts had the most X") frequently name a completely
different top-5. "Most meeting interactions" expects `Gould and Sons |
18` leading; the platform leads with `Smith and Sons` instead — a
different join/filter path produces a different candidate set to rank
in the first place.

**A related single-value case.** "How many interactions are phone call
events?" expects 28,627; the platform answers 57,349 — almost exactly
2× too high, the same join-duplication family on a single total.

**A further 11 questions** return one flat zero for an entire breakdown
or ranking instead of any per-category split — plausibly the same
literal-mismatch/join-miss family documented in the "Categorical
Literal Mismatch" category, but not independently re-verified with that
category's rigor, so counted here rather than claimed there without
proof: `CRM-T2-06-24`, `CRM-T2-06-25`, `CRM-T2-06-26`, `CRM-T4-02-08`,
`CRM-T4-04-19`, `CRM-T4-06-28`, `CRM-T5-02-08`, `CRM-T5-02-10`,
`CRM-T5-03-13`, `CRM-T5-03-15`, `CRM-T5-03-17`.

Full affected list (64): `CRM-RG-01-V01`, `CRM-RG-01-V02`, `CRM-RG-02-V01`,
`CRM-RG-02-V02`, `CRM-RG-05-V01`, `CRM-RG-06-V01`, `CRM-RG-13-V01`,
`CRM-RG-14-V01`, `CRM-T1-05-23`, `CRM-T2-06-24`, `CRM-T2-06-25`,
`CRM-T2-06-26`, `CRM-T2-06-27`, `CRM-T2-06-28`, `CRM-T4-01-01`,
`CRM-T4-01-02`, `CRM-T4-01-03`, `CRM-T4-01-04`, `CRM-T4-01-05`,
`CRM-T4-02-06`, `CRM-T4-02-07`, `CRM-T4-02-08`, `CRM-T4-02-09`,
`CRM-T4-02-10`, `CRM-T4-02-12`, `CRM-T4-03-13`, `CRM-T4-03-15`,
`CRM-T4-04-19`, `CRM-T4-05-21`, `CRM-T4-05-22`, `CRM-T4-05-23`,
`CRM-T4-05-24`, `CRM-T4-05-25`, `CRM-T4-06-26`, `CRM-T4-06-27`,
`CRM-T4-06-28`, `CRM-T4-06-29`, `CRM-T4-06-30`, `CRM-T4-06-31`,
`CRM-T4-06-32`, `CRM-T5-01-01`, `CRM-T5-01-02`, `CRM-T5-01-03`,
`CRM-T5-01-04`, `CRM-T5-01-05`, `CRM-T5-02-06`, `CRM-T5-02-07`,
`CRM-T5-02-08`, `CRM-T5-02-09`, `CRM-T5-02-10`, `CRM-T5-02-11`,
`CRM-T5-03-12`, `CRM-T5-03-13`, `CRM-T5-03-14`, `CRM-T5-03-15`,
`CRM-T5-03-16`, `CRM-T5-03-17`, `CRM-T5-04-18`, `CRM-T5-04-19`,
`CRM-T5-04-20`, `CRM-T5-04-21`, `CRM-T5-05-22`, `CRM-T5-05-23`,
`CRM-T5-05-24`.

## Sales (19)

Six distinct, confirmed causes:

1. **Date range inclusive on both ends (7).** Reference periods are
   `[period_start, period_end)` — the end date belongs to the next
   period. Generated queries use `BETWEEN period_start AND period_end`,
   pulling boundary-exact deals into the wrong period. `SALES-T2-09-37`:
   expected 940, answered 945.
2. **Quarter window applied to the wrong date column (6).** "Quoted
   revenue by product category" questions filter Q1/Q2 on
   `quotations.quoted_at` instead of `deals.close_date` — consistently
   lower totals (hardware: $878.4M vs. expected $984.0M).
3. **Joining through a non-unique name column multiplies every row
   (1).** A territory deal comparison joins `deals` to `leads` on
   `rep_name` (text, not an id); 50 reps share 15,000 leads, so each
   deal row duplicates ~300×. Expected $6.75M; answered ~$2.05 billion.
4. **A different definition of "most recently completed period" (4).**
   Reference: one global latest period. Generated query: latest period
   *per rep*, bounded by the real-world current date — a narrower rule
   that can pick a different period.
5. **A status word dropped from the filter.** "How many **new** leads
   never became a deal?" drops the `lead_status = 'New'` filter and
   counts every unconverted lead. (This mechanism's only member passed
   in the current run — kept here as a description, not currently
   observed failing.)
6. **An incomplete status enumeration in a multi-value filter (1).** A
   rephrase lists "decided (accepted, rejected, or expired)" but the
   generated `IN (...)` list omits `'EXPIRED'`, shrinking the
   denominator: expected 34.19, answered 50.68.

Affected: `SALES-T2-09-37`, `SALES-T3-03-15`, `SALES-T4-04-14` through
`-18`, `SALES-T5-05-19` through `-22`, `SALES-T5-06-23`,
`SALES-RG-14-V01`, `SALES-T5-03-12`, `SALES-T5-02-05`, `SALES-T5-02-06`,
`SALES-T5-02-08`, `SALES-T5-02-09`, `SALES-RG-03-V01`.

## Finance (11)

Three distinct causes (a fourth, cardinality-of-category-literal, moved
to the "Categorical Literal Mismatch" category):

- **Wrong side of a currency conversion used as the filter (1).** "USD
  transactions" means `source_currency = 'USD'`; the generated query
  filters on `target_currency = 'USD'` instead — nearly the whole
  table. Expected 2,345,393,479.41; answers ~8× too high.
  (`FIN-T2-06-30`)
- **FX rate lookup uses the wrong currency field, with a silent 1:1
  fallback (8).** The correct lookup keys off the transaction's own
  currency pair and date; the generated query keys off the ledger
  entry's own currency field, falling back to a 1:1 rate when no match
  is found instead of excluding the row — quarterly totals come out
  different by tens to hundreds of millions. (`FIN-T5-02-07`,
  `FIN-T5-02-08`, `FIN-T5-02-09`, `FIN-T5-02-10`, `FIN-T5-06-21`,
  `FIN-T5-06-22`, `FIN-RG-06-V01`, `FIN-RG-06-V02`)
- **"Has activity" check ignores the time period being asked about
  (2).** The correct check is scoped to the specific budget period's
  date range; the generated query's check is "has this account *ever*
  had any ledger entry" — overcounts "has activity" since almost every
  account has some history somewhere. `FIN-T4-02-08` expects 40/43;
  answers 78/5. (`FIN-T4-02-07`, `FIN-T4-02-08`)

## Logistics (1)

`LOG-RG-13-V01` ("compare total order value, previous quarter vs. the
quarter before that") expects `79421787.31 | 18610.50 | ... | down`. The
response reports the most recent quarter as $0.00 and the earlier
quarter as $18,610.50 — the two periods are backwards and the larger
one is zeroed. `results.json`'s own `rephrase_groups.platform_findings`
independently flags this exact rephrase group as resolving to
inconsistent ground truth across its variants — the same question asked
three ways produces three different outcomes from the live platform.

## Where to look these up

Search by `question_id` in these two files per domain — the first has
the full question text and expected answer, the second has the
platform's actual answer, `exact_match_detail`, and judge scores:

| Domain | Question + expected answer | Platform's answer + judge scores |
|---|---|---|
| CRM | `release/v1.0.0-fixed/crm/qa_pairs/crm_qa_pairs.csv` | `release/v1.0.0-fixed/crm/judge/20261014T033522Z/results.json` |
| Sales | `release/v1.0.0-fixed/sales/qa_pairs/sales_qa_pairs.csv` | `release/v1.0.0-fixed/sales/judge/20261013T102604Z/results.json` |
| Finance | `release/v1.0.0-fixed/finance/qa_pairs/finance_qa_pairs.csv` | `release/v1.0.0-fixed/finance/judge/20261013T112105Z/results.json` |
| Logistics | `release/v1.0.0-fixed/logistics/qa_pairs/logistics_qa_pairs.csv` | `release/v1.0.0-fixed/logistics/judge/20261013T160913Z/results.json` |

## Status

Not fixable in this evaluation's scorer or dataset. Every cause above
is the live platform's own query construction. Where a fix is at all
describable, it's a platform-side query-generation correction specific
to each cause (join through the stated relationship explicitly; use
half-open date ranges; filter on the right date/currency column; scope
existence checks to the right time window) — none of it is available to
change from this side.
