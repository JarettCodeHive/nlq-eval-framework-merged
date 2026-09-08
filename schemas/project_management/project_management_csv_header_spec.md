# Project Management CSV Header Spec

Status: Draft v0.1 - pending sign-off

Source of truth:

- ERD: `schemas/project_management/project_management_er.dbml`
- DDL: `schemas/project_management/project_management_ddl.sql`

Purpose:

This document freezes the exact CSV file names, header names, and column order
for the Project Management golden dataset. Generated CSV headers must be
byte-identical to the columns below. Any header rename, reorder, addition, or
removal requires schema review before data generation or Q&A authoring proceeds.

## CSV Contract

- Format: RFC 4180 CSV.
- Encoding: UTF-8.
- Header row: required.
- Column names: lowercase snake_case.
- Column order: exactly as listed in this spec.
- NULL representation: empty CSV field.
- Boolean representation: `true` / `false`.
- Monetary precision: `DECIMAL(15, 2)`.
- Work-hour precision: `DECIMAL(6, 2)` for `time_entries.hours`.
- Allocation precision: `DECIMAL(5, 2)` for `task_resources.allocation_pct`.
- Maximum rows per table: `250000`.

## Tables

### `projects.csv`

Primary key: `project_id`

| Ordinal | Header | Type | Nullable | Key | References |
|---:|---|---|---|---|---|
| 1 | `project_id` | `INTEGER` | No | PK |  |
| 2 | `project_name` | `VARCHAR(255)` | No |  |  |
| 3 | `project_code` | `VARCHAR(64)` | No |  |  |
| 4 | `status` | `VARCHAR(32)` | No |  |  |
| 5 | `priority` | `VARCHAR(32)` | No |  |  |
| 6 | `start_date` | `DATE` | No |  |  |
| 7 | `end_date` | `DATE` | Yes |  |  |
| 8 | `budget_amount` | `DECIMAL(15, 2)` | Yes |  |  |
| 9 | `currency_code` | `CHAR(3)` | No |  |  |
| 10 | `created_at` | `TIMESTAMP` | No |  |  |

Header row:

```csv
project_id,project_name,project_code,status,priority,start_date,end_date,budget_amount,currency_code,created_at
```

### `resources.csv`

Primary key: `resource_id`

| Ordinal | Header | Type | Nullable | Key | References |
|---:|---|---|---|---|---|
| 1 | `resource_id` | `INTEGER` | No | PK |  |
| 2 | `resource_name` | `VARCHAR(255)` | No |  |  |
| 3 | `role` | `VARCHAR(32)` | No |  |  |
| 4 | `department` | `VARCHAR(64)` | Yes |  |  |
| 5 | `location` | `VARCHAR(128)` | Yes |  |  |
| 6 | `hourly_rate` | `DECIMAL(10, 2)` | Yes |  |  |
| 7 | `currency_code` | `CHAR(3)` | No |  |  |
| 8 | `is_active` | `BOOLEAN` | No |  |  |
| 9 | `created_at` | `TIMESTAMP` | No |  |  |

Header row:

```csv
resource_id,resource_name,role,department,location,hourly_rate,currency_code,is_active,created_at
```

### `tasks.csv`

Primary key: `task_id`

| Ordinal | Header | Type | Nullable | Key | References |
|---:|---|---|---|---|---|
| 1 | `task_id` | `INTEGER` | No | PK |  |
| 2 | `project_id` | `INTEGER` | No | FK | `projects.project_id` |
| 3 | `task_name` | `VARCHAR(255)` | No |  |  |
| 4 | `status` | `VARCHAR(32)` | No |  |  |
| 5 | `task_type` | `VARCHAR(32)` | Yes |  |  |
| 6 | `start_date` | `DATE` | No |  |  |
| 7 | `due_date` | `DATE` | Yes |  |  |
| 8 | `completed_date` | `DATE` | Yes |  |  |
| 9 | `estimate_hours` | `DECIMAL(8, 2)` | Yes |  |  |
| 10 | `created_at` | `TIMESTAMP` | No |  |  |

Header row:

```csv
task_id,project_id,task_name,status,task_type,start_date,due_date,completed_date,estimate_hours,created_at
```

### `task_resources.csv`

Primary key: `task_id`, `resource_id`

`task_resources` is the explicit many-to-many bridge between `tasks` and
`resources`, while also carrying assignment attributes. Generated
`time_entries` should reference valid task/resource combinations.

| Ordinal | Header | Type | Nullable | Key | References |
|---:|---|---|---|---|---|
| 1 | `task_id` | `INTEGER` | No | PK, FK | `tasks.task_id` |
| 2 | `resource_id` | `INTEGER` | No | PK, FK | `resources.resource_id` |
| 3 | `assignment_role` | `VARCHAR(32)` | Yes |  |  |
| 4 | `allocation_pct` | `DECIMAL(5, 2)` | No |  |  |
| 5 | `assigned_at` | `DATE` | No |  |  |
| 6 | `released_at` | `DATE` | Yes |  |  |
| 7 | `created_at` | `TIMESTAMP` | No |  |  |

Header row:

```csv
task_id,resource_id,assignment_role,allocation_pct,assigned_at,released_at,created_at
```

### `milestones.csv`

Primary key: `milestone_id`

| Ordinal | Header | Type | Nullable | Key | References |
|---:|---|---|---|---|---|
| 1 | `milestone_id` | `INTEGER` | No | PK |  |
| 2 | `project_id` | `INTEGER` | No | FK | `projects.project_id` |
| 3 | `milestone_name` | `VARCHAR(255)` | No |  |  |
| 4 | `milestone_type` | `VARCHAR(32)` | Yes |  |  |
| 5 | `planned_date` | `DATE` | No |  |  |
| 6 | `actual_date` | `DATE` | Yes |  |  |
| 7 | `status` | `VARCHAR(32)` | No |  |  |
| 8 | `created_at` | `TIMESTAMP` | No |  |  |

Header row:

```csv
milestone_id,project_id,milestone_name,milestone_type,planned_date,actual_date,status,created_at
```

### `time_entries.csv`

Primary key: `entry_id`

`time_entries` is the work-log fact table for Project-to-Task-to-Time_Entry
aggregation and resource utilization questions.

| Ordinal | Header | Type | Nullable | Key | References |
|---:|---|---|---|---|---|
| 1 | `entry_id` | `INTEGER` | No | PK |  |
| 2 | `task_id` | `INTEGER` | No | FK | `tasks.task_id` |
| 3 | `resource_id` | `INTEGER` | No | FK | `resources.resource_id` |
| 4 | `entry_date` | `DATE` | No |  |  |
| 5 | `hours` | `DECIMAL(6, 2)` | No |  |  |
| 6 | `billable` | `BOOLEAN` | No |  |  |
| 7 | `work_type` | `VARCHAR(32)` | No |  |  |
| 8 | `notes` | `VARCHAR(1024)` | Yes |  |  |
| 9 | `created_at` | `TIMESTAMP` | No |  |  |

Header row:

```csv
entry_id,task_id,resource_id,entry_date,hours,billable,work_type,notes,created_at
```

## Release File Order

The release package must include CSVs in this table order:

1. `projects.csv`
2. `resources.csv`
3. `tasks.csv`
4. `task_resources.csv`
5. `milestones.csv`
6. `time_entries.csv`

## Sign-Off

| Role | Name | Status | Date | Notes |
|---|---|---|---|---|
| Data Engineering Lead | TBD | Pending | TBD | Confirms DDL/header alignment. |
| Platform Owner DRI | TBD | Pending | TBD | Confirms ingestion contract and header names. |
| QA/Audit Reviewer | TBD | Pending | TBD | Confirms release CSV headers match this spec. |
