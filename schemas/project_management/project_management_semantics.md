# Project Management Semantic Contract

Status: Contract v1.0 - frozen for implementation; external sign-off pending

This document is the canonical source for Project Management date, decimal,
metric, and currency semantics. Generation, validation, the data dictionary,
manifest metadata, and later reference SQL must implement these definitions
without introducing alternative interpretations.

## Fixed Reference Date

- Source: `config/generation/base.json:reference_today`.
- Contract v1.0 value: `2026-08-01`.
- Generation and validation must not read the wall clock.
- All relative-date calculations derive from this fixed value.

## Date Definitions

### Overdue Task

```sql
due_date < reference_today AND completed_date IS NULL
```

A task with no due date is open-ended and is not overdue. A completed task is
not currently overdue, even when it was completed after its due date.

### Completed-Late Task

```sql
completed_date IS NOT NULL
AND due_date IS NOT NULL
AND completed_date > due_date
```

### In-Progress Task

```sql
status = 'InProgress'
```

In-progress status is never inferred from dates.

### This Quarter

The current quarter is the half-open calendar interval containing
`reference_today`:

```sql
entry_date >= DATE '2026-07-01'
AND entry_date < DATE '2026-10-01'
```

The lower bound is inclusive and the next-quarter bound is exclusive.

### Active Assignment

```sql
assigned_at <= reference_today
AND (released_at IS NULL OR released_at >= reference_today)
```

The assignment is active on its `released_at` date. A NULL `released_at` means
the assignment remains open-ended.

### Open-Ended Ranges

- `projects.end_date IS NULL`: no scheduled project end.
- `tasks.due_date IS NULL`: no scheduled task due date.
- `task_resources.released_at IS NULL`: assignment is not released.
- NULL end dates remain empty in CSV and are not replaced by sentinel dates.

## Milestone Completion Percentage

A milestone is complete only when `actual_date IS NOT NULL`. Status text alone
does not count it as complete.

Canonical result semantics:

```sql
CAST(
    ROUND(
        100.00
        * SUM(CASE WHEN actual_date IS NOT NULL THEN 1 ELSE 0 END)
        / NULLIF(COUNT(*), 0),
        2
    ) AS DECIMAL(5, 2)
)
```

- Output type: `DECIMAL(5, 2)`.
- Rounding mode: `ROUND_HALF_UP`.
- Multiply and divide first, then round once to scale 2.
- A zero denominator returns NULL; it is never treated as zero percent.
- Generation must give every project at least one milestone, while reference
  SQL retains `NULLIF` defensively.

## Decimal Policy

| Field or output | Precision | Scale | Quantizer |
|---|---:|---:|---|
| `projects.budget_amount` | 15 | 2 | `0.01` |
| `resources.hourly_rate` | 10 | 2 | `0.01` |
| `tasks.estimate_hours` | 8 | 2 | `0.01` |
| `task_resources.allocation_pct` | 5 | 2 | `0.01` |
| `time_entries.hours` | 6 | 2 | `0.01` |
| Milestone completion percentage | 5 | 2 | `0.01` |

- Python uses `decimal.Decimal` or integer hundredths for exact arithmetic.
- Binary floating-point values must not be used for stored or calculated
  contract values.
- Every rounding operation uses `ROUND_HALF_UP`.
- Calculations use full fixed-point operands and round once at the final output
  scale.
- Stored decimal strings always retain exactly two fractional digits.
- Allocation values for a task must be positive and sum exactly to `100.00`;
  deterministic remainder allocation resolves indivisible hundredths.
- Logged hours are positive. Monetary amounts, rates, and estimates are
  non-negative when populated.

## Currency Policy

- Project Management Contract v1.0 is USD-only.
- `projects.currency_code = 'USD'`.
- `resources.currency_code = 'USD'`.
- The DDL enforces both fixed values.
- No FX table, live rate lookup, or currency conversion belongs to this domain.
- Cross-currency evaluation remains a Finance-domain responsibility.

## Chronology Invariants

- Completed tasks have `completed_date`; other tasks do not.
- Task completion cannot precede task start.
- Completed milestones have `actual_date`; incomplete milestones do not.
- `assigned_at` cannot precede its task or project start.
- Populated `released_at` cannot precede `assigned_at`.
- Time entries belong to a valid task/resource assignment and fall inside its
  permitted window, except for an explicitly configured and jointly adjusted
  boundary-date scenario.
