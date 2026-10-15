# "Overdue" Counts Inflated by Reference-Date Drift (7 questions — Project Management only)

7 "overdue" questions come back with a count that's consistently
12–15% higher than expected — not a wrong computation, a different
"today."

## What's happening

Every "overdue" fact in this dataset (`due_date < reference_today AND
completed_date IS NULL`) was generated against a **frozen** reference
date of `2026-08-01`. This evaluation run happened on `2026-10-13` —
over ten weeks later. If "overdue" is evaluated against the actual
current date rather than the dataset's frozen reference point, every
count comes out higher than the ground truth, because more tasks have
crossed their due date in the time since the dataset was built.

| Question | Scope | Expected | Answer |
|---|---|---|---|
| `PM-T3-03-11` / `PM-RG-06-V01` / `PM-RG-06-V02` | Active projects | 1,328 | 1,509 |
| `PM-T3-03-12` | Cancelled projects | 186 | 210 |
| `PM-T3-03-13` | Completed projects | 747 | 860 |
| `PM-T3-03-15` | Planning projects | 349 | 390 |
| `PM-T5-04-12` | by task type | Bug 598 / Testing 591 / Deployment 576 | Bug 677 / Testing 670 / Deployment 638 |

Every instance is inflated by a similar relative margin, consistent
with one shared cause rather than six unrelated miscounts. Unchanged
from the previous release — all 7 still fail for the same reason, since
the gap between the dataset's frozen date and the live run only grows
with time.

## Where to look these up

Search by `question_id` in `release/v1.0.0-fixed/project_management/qa_pairs/project_management_qa_pairs.csv`
(question text and expected answer) and
`release/v1.0.0-fixed/project_management/judge/20261013T174157Z/results.json`
(platform's actual answer, `exact_match_detail`, judge scores).

## Status

A timing mismatch, not a defect in either the dataset or the response:
the dataset's "today" and the evaluation's "today" are now over ten
weeks apart, and "overdue" is a moving target. Closing this gap means
keeping the dataset's reference date and the evaluation run close
together in time, or rebuilding the dataset close to when the
evaluation actually runs — not a correctness fix on either side. Still
deferred, pending an explicit decision on dataset-regeneration cadence.
