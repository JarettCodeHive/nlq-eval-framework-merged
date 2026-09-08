-- Project Management DDL - Draft v0.1
--
-- Conformance: ANSI SQL, intended to lint clean under
-- `sqlfluff lint --dialect ansi`.
-- Runs unmodified in DuckDB 0.10+.
--
-- Source ERD: schemas/project_management/project_management_er.dbml
--
-- Ambiguity definitions for Q&A authoring:
-- overdue = tasks.due_date < fixed TODAY AND tasks.completed_date IS NULL.
-- in_progress = tasks.status = 'InProgress'.
-- this_quarter = time_entries.entry_date in the current calendar quarter of
-- the fixed reference date.
--
-- NOT-NULL rules: never nullify a PK or INNER-JOIN FK.
-- INNER-JOIN FKs: tasks.project_id, task_resources.task_id,
-- task_resources.resource_id, milestones.project_id, time_entries.task_id,
-- time_entries.resource_id.
-- Nullable analytical fields: projects.end_date, tasks.due_date,
-- tasks.completed_date, milestones.actual_date, resources.hourly_rate.
--
-- Explicit many-to-many bridge: task_resources links tasks to resources while
-- preserving assignment attributes. time_entries remains the work-log fact
-- table and should reference valid task/resource combinations during
-- generation.

CREATE TABLE projects (
    project_id INTEGER NOT NULL,
    project_name VARCHAR(255) NOT NULL,
    project_code VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    priority VARCHAR(32) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE,
    budget_amount DECIMAL(15, 2),
    currency_code CHAR(3) NOT NULL DEFAULT 'USD',
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_projects PRIMARY KEY (project_id)
);

CREATE TABLE resources (
    resource_id INTEGER NOT NULL,
    resource_name VARCHAR(255) NOT NULL,
    role VARCHAR(32) NOT NULL, -- noqa: RF04
    department VARCHAR(64),
    location VARCHAR(128), -- noqa: RF04
    hourly_rate DECIMAL(10, 2),
    currency_code CHAR(3) NOT NULL DEFAULT 'USD',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_resources PRIMARY KEY (resource_id)
);

CREATE TABLE tasks (
    task_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    task_name VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL,
    task_type VARCHAR(32),
    start_date DATE NOT NULL,
    due_date DATE,
    completed_date DATE,
    estimate_hours DECIMAL(8, 2),
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_tasks PRIMARY KEY (task_id),
    CONSTRAINT fk_tasks_project
    FOREIGN KEY (project_id) REFERENCES projects (project_id)
);

CREATE TABLE task_resources (
    task_id INTEGER NOT NULL,
    resource_id INTEGER NOT NULL,
    assignment_role VARCHAR(32),
    allocation_pct DECIMAL(5, 2) NOT NULL DEFAULT 100,
    assigned_at DATE NOT NULL,
    released_at DATE,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_task_resources PRIMARY KEY (task_id, resource_id),
    CONSTRAINT fk_task_resources_task
    FOREIGN KEY (task_id) REFERENCES tasks (task_id),
    CONSTRAINT fk_task_resources_resource
    FOREIGN KEY (resource_id) REFERENCES resources (resource_id)
);

CREATE TABLE milestones (
    milestone_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    milestone_name VARCHAR(255) NOT NULL,
    milestone_type VARCHAR(32),
    planned_date DATE NOT NULL,
    actual_date DATE,
    status VARCHAR(32) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_milestones PRIMARY KEY (milestone_id),
    CONSTRAINT fk_milestones_project
    FOREIGN KEY (project_id) REFERENCES projects (project_id)
);

CREATE TABLE time_entries (
    entry_id INTEGER NOT NULL,
    task_id INTEGER NOT NULL,
    resource_id INTEGER NOT NULL,
    entry_date DATE NOT NULL,
    hours DECIMAL(6, 2) NOT NULL,
    billable BOOLEAN NOT NULL DEFAULT TRUE,
    work_type VARCHAR(32) NOT NULL,
    notes VARCHAR(1024),
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_time_entries PRIMARY KEY (entry_id),
    CONSTRAINT fk_time_entries_task
    FOREIGN KEY (task_id) REFERENCES tasks (task_id),
    CONSTRAINT fk_time_entries_resource
    FOREIGN KEY (resource_id) REFERENCES resources (resource_id)
);
