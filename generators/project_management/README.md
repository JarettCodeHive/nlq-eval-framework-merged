# Project Management Dataset Generator

This package contains Project Management-specific dataset generation behavior.
Shared deterministic generation, distribution, CSV, manifest, schema-contract,
and temporal utilities remain under `generators/core`.

The component descriptor is
`config/generation/project_management.json`. Use
`load_project_management_config()` to assemble its release, schema, generation,
and validation components. Call `validate_project_management_config()` before
generation so configuration, DDL, signed headers, relationships, profile
capacities, and semantic policies fail fast when they drift.

Generation stages are added incrementally by the implementation plan. Domain
enums, date rules, decimal behavior, and PM business mappings must remain in
the JSON components rather than being duplicated as Python constants.

`PROJECT_MANAGEMENT_COLUMN_CONTRACTS` in `generator.py` is the shared ordered
table/header mapping for all PM stages. It is derived from the assembled schema
component and must be reused instead of adding manual column arrays.

`ProjectManagementBaseEntityGenerator` builds the six clean in-memory tables in
dependency order using stable named RNG and Faker streams. It creates the
explicit task/resource many-to-many bridge, exact per-task allocation totals,
at least one milestone per project, and time entries restricted to declared
assignments. Controlled imperfections are not part of base generation.

Before returning, base generation runs
`validate_project_management_generated_tables()`. This immediate guard checks
shape and row counts, scalar/composite keys, required fields, physical and
semantic relationships, domain values, fixed decimal scales, chronology,
status/date rules, LEFT JOIN and many-to-many cases, exact allocations, USD
currency, and absence of controlled imperfections. Broader post-export and
release validation is provided by the consolidated pipeline documented in the
root `README.md`.

Primary commands:

```bash
python main.py build-dataset --domain project_management --profile dev
python main.py build-dataset --domain project_management --profile full
```

The dev command writes all three stages below
`tmp/generated/project_management/dev/` and validates the persisted
`imperfect` CSVs. The full command writes the versioned release under
`release/project_management/dataset-v1.0.0/`, runs every persisted validation
and clean-room reproducibility gate, and creates `manifest.json` last.

The manifest seals the release. Do not rerun a release-writing command against
that version after it exists. Use a new dataset version for corrections.

All PM currency fields are fixed to USD. Relative date logic uses the configured
reference date rather than the wall clock. Detailed distribution and
imperfection behavior is documented in
`Project_Management_Distributions_and_Imperfections_Matrix.md` at the repository
root.
