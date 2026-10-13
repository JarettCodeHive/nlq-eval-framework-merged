# Finance — Category 4: One Field Missing From an Otherwise-Correct Answer (1 question)

One quarter-over-quarter comparison
question got 4 of its 5 required numbers right and dropped the 5th.

## What's happening

**Question:** "How did FX-adjusted revenue change from Q1 2026 to Q2
2026?" (FIN-T5-06-22) — the expected answer has 5 parts: Q1 total, Q2
total, the absolute change, the **percentage** change, and the direction
word (up/down/flat).

Your answer got Q1, Q2, and the absolute change exactly right, with the
correct sign ("-$392,064,777"), and said "down" — but never stated the
percentage change anywhere in the response. The number itself (likely
already computed internally, since the direction and absolute change are
both derived from it) just never made it into the final text.

## Suggested fix

For any "how did X change" question, treat the percentage change as a
required field on the same footing as the absolute change — if your
pipeline computes it internally to decide "up" vs "down," surface the
number itself rather than only the word.
