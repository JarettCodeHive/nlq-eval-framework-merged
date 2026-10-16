# Trend Record Omits the Derived Percentage/Delta Figure (26 questions — Sales 5, Logistics 8, Finance 2, Project Management 11)

Every "how did X change from Q1 2026 to Q2 2026" question's expected
answer is a 5-part record: Q1 total, Q2 total, absolute change,
percentage change, and a direction word. Across four domains, the
response reliably states the first four parts correctly — Q1, Q2, the
absolute change, and the direction in prose — but the percentage figure
is the one part that's consistently left out of the final text, even
though it's presumably computed internally to decide the "up"/"down"
wording.

## Sales (5)

Example: expected `... | -90.65 | down`. Response: "...a drop of
$2,158,521.67..." — no percentage anywhere in ~250 words of prose.

Affected: `SALES-T5-03-10`, `SALES-T5-03-11`, `SALES-T5-03-14`,
`SALES-RG-13-V01`, `SALES-RG-13-V02`.

## Logistics (8)

Example (`LOG-T5-02-09`, NA region order value): expected
`72296800.92 | 3492949.09 | -68803851.83 | -95.17 | down`. Response:
"total order value fell sharply from $72,296,800.92 in Q1 2026... to
$3,492,949.09 in Q2 2026... a decline of $68,803,851.83" — three of
five values exact, direction correctly conveyed, but `-95.17` appears
nowhere. Judge `factual_correctness: 4` across this group — the judge
agrees the core numbers are right and only the percentage is missing.

Affected: `LOG-T5-02-06`, `LOG-T5-02-09`, `LOG-T5-05-12`, `LOG-T5-05-13`,
`LOG-T5-05-14`, `LOG-T5-05-16`, `LOG-RG-13-V02`, `LOG-RG-14-V01`.

## Finance (2)

- `FIN-T5-02-06` — "...fell from about $1.75 billion in Q1 2026 to
  about $954.5 million in Q2 2026... a decline of roughly $798.9
  million." Expected percentage: `-45.56`. Never stated.
- `FIN-T5-06-24` — "...rose from about $657.81 million... to roughly
  $889.65 million..., a change of approximately $231.84 million."
  Expected percentage: `35.25`. Never stated.

## Project Management (11)

Example (`PM-T5-05-14`): expected `8610.82 | 11607.10 | 2996.28 | 34.80 |
up`. Response: "...increased from 8,610.82 in Q1 2026... to 11,607.10 in
Q2 2026..., a change of 2,996.28 hours..." — the percentage, `34.80`, is
never computed or stated.

Affected: `PM-T5-02-06`, `PM-T5-02-07`, `PM-T5-02-09`, `PM-T5-02-10`,
`PM-T5-05-13`, `PM-T5-05-14`, `PM-T5-05-15`, `PM-T5-05-16`, `PM-T5-05-17`,
`PM-RG-10-V01`, `PM-RG-16-V01`.

One PM instance (`PM-RG-16-V01`) is also where a separate, unrelated
diagnostic-message defect was found in this evaluation's own code
(`judge/exact_match.py` prints the literal string `"None"` instead of a
character count in one label-ownership message, when the distance can't
be computed for a particular label kind). The defect is real and still
present, but it doesn't change any pass/fail outcome — this question
fails for the ordinary missing-percentage reason either way, and the
message bug just happens not to be visibly triggered in this run's data.
Worth fixing for message clarity, not because it affects any score.

## Where to look these up

Search by `question_id` in these two files per domain — the first has
the full question text and expected answer, the second has the
platform's actual answer, `exact_match_detail`, and judge scores:

| Domain | Question + expected answer | Platform's answer + judge scores |
|---|---|---|
| Sales | `release/v1.0.0-fixed/sales/qa_pairs/sales_qa_pairs.csv` | `release/v1.0.0-fixed/sales/judge/20261013T102604Z/results.json` |
| Logistics | `release/v1.0.0-fixed/logistics/qa_pairs/logistics_qa_pairs.csv` | `release/v1.0.0-fixed/logistics/judge/20261013T160913Z/results.json` |
| Finance | `release/v1.0.0-fixed/finance/qa_pairs/finance_qa_pairs.csv` | `release/v1.0.0-fixed/finance/judge/20261013T112105Z/results.json` |
| Project Management | `release/v1.0.0-fixed/project_management/qa_pairs/project_management_qa_pairs.csv` | `release/v1.0.0-fixed/project_management/judge/20261013T174157Z/results.json` |

## Status

Not fixable by this evaluation — the scorer cannot pass a value that
was never stated by the platform. This is an omission in the platform's
response, consistently reproducible across four domains and the same
question shape in each. Something for the platform's own answer
generation to address: when a trend comparison states Q1, Q2, and the
absolute change, it should also state the percentage change it's
evidently already computing internally to pick the direction word.
