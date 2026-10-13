# Finance — Category 8: Materially Wrong Totals (13 questions)

This is the largest Finance group. These
questions get a complete, confident-sounding answer — just computed from
the wrong rows or the wrong column. Four distinct, confirmed causes below.

## Cause 1 — category value stored without spaces, filter written with spaces (1 question)

Same issue as several CRM questions: `accounts.account_subtype` is stored
as one word (`AccountsPayable`), but the filter was written as
`ILIKE '%accounts payable%'` (with a space) — which never matches. Result:
"No budgeted amount data was found," instead of the real total of
$2,987,315.56.

**Fix:** same as the CRM write-up — match against the real stored values,
not a human-spaced guess.

## Cause 2 — wrong side of a currency conversion used as the filter (1 question)

**Question:** "Among USD transactions with a known FX rate, what is the
total amount converted to USD?" "USD transactions" means transactions that
**originated** in USD (`source_currency = 'USD'`). Your query instead
filtered on `target_currency = 'USD'` — i.e. every transaction that ends
up *reported* in USD, which is nearly the whole table. Result: ~$19.76
billion instead of the correct $2.35 billion (about 8.4x too high).

**Fix:** when a question says "USD transactions," check the transaction's
originating currency, not its reporting/target currency.

## Cause 3 — FX rate lookup uses the wrong currency field, with a silent 1:1 fallback (7 questions)

All of the Q1-vs-Q2 "FX-adjusted" trend questions in this run. The correct
calculation looks up the FX rate using the **transaction's** currency pair
and date. Your query instead looks up the rate using the **ledger entry's**
own currency field — and when no matching rate is found for a given day,
it quietly substitutes a 1:1 rate (`COALESCE(rate, 1)`) rather than
excluding or flagging that row. Both of these choices can pull in rows the
reference calculation would exclude, or convert them incorrectly, so the
quarterly totals (and the swing between quarters) come out different —
sometimes by tens of millions.

**Fix:** key the FX-rate lookup off the transaction's own currency, matched
to the same transaction date, the same way the rest of the pipeline does —
and when no rate matches, exclude the row (or flag it) rather than
assuming parity with USD.

## Cause 4 — "has activity" check ignores the time period being asked about (4 questions)

**Question:** "How many budget periods recorded ledger activity for their
account versus none?" The correct check is: *did this account have any
ledger activity **during this specific budget period's date range**?*
Your query's check was: *has this account **ever** had any ledger entry,
at any time?* Almost every account has *some* history somewhere, so this
massively overcounts "has activity" — e.g. expected `108` periods with
activity / `130` without, your answer: `222` with / `16` without.

**Fix:** scope the "has activity" existence check to the budget period's
own `period_start`/`period_end` window, not the account's entire history.
