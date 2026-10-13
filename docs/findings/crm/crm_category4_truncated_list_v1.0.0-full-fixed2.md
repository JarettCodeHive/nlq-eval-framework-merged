# CRM — Category 4: Ranked List Cut Short (1 question)

One question asked for a ranked list of
7 items and only got 5.

## What's happening

**Question:** "For awareness campaigns, which contact titles carry the
most total multi-touch attribution weight?" — the correct answer has 7
ranked titles with a number for each.

Your answer correctly named and ranked the top 5, with the right numbers,
then switched to prose for the rest: *"Program Manager and Operations
Director also present at similar levels"* — no numbers given for those
last two. The 5 that were given were correct; the response just stopped
short of the full requested list.

## Suggested fix

When a question asks for "which X" without a stated limit, assume it
wants every item that's part of the real ranking (or state the limit
explicitly, e.g. "top 5"), rather than defaulting to a shorter list and
summarizing the remainder in prose. If there's an internal result-size cap
causing the cutoff, consider surfacing the full list up to a larger cap, or
explicitly telling the user there are more items than shown.
