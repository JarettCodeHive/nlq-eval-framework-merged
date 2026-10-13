# CRM — Category 3: Wrong Numbers in Breakdowns and Rankings (62 questions)

This is the largest group of failures.
These questions ask for a breakdown (by category, by region, by quarter)
or a top-N ranking, and get back a complete, well-formatted answer — just
with the wrong numbers, or the wrong entities, in it.

## Confirmed pattern #1 — skipping the "contact" step undercounts or overcounts

Many questions ask about activity "raised by" or "from" an account's
**contacts** — e.g. *"support cases raised by their contacts"* or
*"engagement points... by interaction channel"* for a region's accounts.
The correct way to answer this joins `accounts → contacts → support_cases`
(or `→ interactions`) — i.e., go through the contact.

Your SQL instead takes a shortcut and joins the account directly to the
case/interaction table using a column like `account_id`, skipping the
contacts step. This causes two different symptoms, depending on which case
applies:

- **Overcounts** when the shortcut column includes rows that shouldn't be
  counted. Example: a "cases raised by their contacts" question should
  exclude cases with no identified contact — but joining straight on
  `support_cases.account_id` doesn't exclude anything, so account-level
  cases (no contact attached) get counted too. Expected: `10,836` total
  cases; your answer: `20,376` — almost double, because every case for the
  account was counted, not just the ones actually raised by a contact.
- **Undercounts** when the shortcut column is sometimes empty. Example:
  `interactions.account_id` is blank for about 1 in 5 interactions (they
  only have the contact recorded, not the account directly) — but every
  interaction *does* have a contact, and every contact has an account. A
  query that joins `interactions` straight to `accounts` on
  `interactions.account_id` misses all of those blank-account-id rows.
  Expected: `84,970` for the top channel; your answer: `72,782` — roughly
  85% of the correct total, consistently.

## Confirmed pattern #2 — wrong entities named, not just wrong counts

Some ranking questions ("which accounts had the most X") name a completely
different top-5 than the correct one — not a rounding or ordering issue,
different accounts entirely. This points to the same root cause as pattern
#1 (joining/filtering through the wrong path produces a different
candidate set to rank in the first place), rather than a separate bug.

## Suggested fix

When a question says "raised by," "from," or "by their [contacts/users/
owners]," join through that relationship table explicitly rather than
using a denormalized shortcut column on the target table — especially
when that shortcut column can be blank or doesn't carry the same filtering
condition the question implies. If a shortcut column is used for
performance, validate it's fully populated and semantically equivalent to
the relationship path before trusting it for a count that needs to be exact.
