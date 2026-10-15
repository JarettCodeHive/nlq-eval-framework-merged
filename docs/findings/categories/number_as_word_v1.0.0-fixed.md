# A Number Written as a Word, Not a Digit (1 question — Project Management only)

`PM-T3-01-02` — "How many cancelled projects currently have no tasks
assigned?" Expected `3`. Answer: *"Three cancelled projects currently
have no tasks assigned... the three projects were created in different
months..."* The judge independently scores this `factual_correctness =
5` — the answer is exactly right. But the digit `3` never appears
anywhere in the response; every reference to the count uses the word
"three," and the only bare digits present belong to unrelated breakdown
figures.

## Where to look this up

`PM-T3-01-02` — question text and expected answer in
`release/v1.0.0-fixed/project_management/qa_pairs/project_management_qa_pairs.csv`;
platform's actual answer, `exact_match_detail`, and judge scores in
`release/v1.0.0-fixed/project_management/judge/20261013T174157Z/results.json`.

## Status

Not a new defect — this is the first time this project's
already-documented policy on spelled-out numbers has actually been
observed to cost a question. `judge/README.md` records the decision
explicitly: recognizing number words was deliberately left out, because
teaching the scorer that "three" matches `3` would also make an
expected `1` match any answer that happens to contain the word "one" —
trading a rare false fail (this case) for a more common false pass
elsewhere. Documented for visibility, not proposing to revisit the
decision.
