# Project Management — Category 5: A Stated Scope Gets Dropped (2 questions)

2 questions ask for a breakdown scoped to one resource role or one
project status, and get back the totals for everything instead.

## What's happening

- **"For analyst resources, how does task assignment count break down by
  task status?"** Expected `Completed | 1960; InProgress | 1879;
  NotStarted | 958; Blocked | 670` (analyst-only, ~5,467 total). Answer:
  `Completed 10,813 / InProgress 10,196 / NotStarted 5,310 / Blocked
  3,681` — a 30,000-total answer covering every resource role, not just
  analysts. (`10,196` and `5,310` are themselves the correct answers to
  two other, unscoped questions elsewhere in this same run.)
- **"...for planning projects..."** — same shape: expected a ~16,000
  total for one project status; the answer gives a 151,500-entry grand
  total, an order of magnitude too large to be one status's slice.

## The problem

In both cases the scoping condition named in the question (one resource
role, one project status) doesn't make it into the answer — the response
reflects the full, unfiltered dataset instead.
