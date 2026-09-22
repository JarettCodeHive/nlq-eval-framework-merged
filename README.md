# NLQ Evaluation Framework

Independent evaluation framework for the Cleris Pulse NLQ platform.

This repository does not build or modify the Pulse runtime. It contains the
assets and code needed to produce reproducible synthetic datasets, verified
Q&A evaluation pairs, judge scoring, and regression scorecards.

## Scope

- Generate golden synthetic relational CSV datasets for five locked domains:
  CRM, Sales, Finance, Project Management, and Logistics.
- Author and verify 750+ Q&A evaluation pairs, with an internal target of 800.
- Verify expected answers by executing reference SQL against released CSVs in
  DuckDB.
- Score platform responses with deterministic exact-match checks and an
  LLM-as-Judge module.
- Produce regression scorecards for release-over-release comparison.
- Document the Evaluation Orchestrator architecture only. Do not implement the
  orchestrator unless the scope is formally reopened.

## Hard Constraints

- No PII: all data is synthetic.
- Platform agnostic: schemas must not be optimized for Pulse-specific behavior.
- Reproducible: a clean run from the committed seed must regenerate
  byte-identical CSV outputs.
- Numeric exactness: numeric answers have zero tolerance for drift.
- Non-production only: staging and QA environments are the only allowed
  platform targets.
- Ground truth comes from exported CSVs, not in-memory generation state.

## Repository Layout

```text
nlq-eval-framework/
  config/        Per-domain generation, schema, and judge configs.
  generators/    Seeded generation, transformation, export, and validation modules.
  schemas/       DDL, ER diagram sources, and CSV header specs.
  qa_pairs/      Q&A authoring templates, SQL templates, and verification.
  judge/         LLM-as-Judge prompts, calibration anchors, and scoring code.
  scorecard/     Deterministic scoring, aggregation, CSV and PDF reporting.
  orchestrator/  Design documentation and config contract only.
  tests/         Pytest suites for generation, integrity, and scoring behavior.
  release/       Versioned generated outputs and manifests.
  docs/          Data dictionaries, rubric, audit reports, and handover docs.
```

### Generation Configuration

`config/generation/base.json` contains shared deterministic defaults. CRM,
Sales, and Finance use `config/generation/<domain>.json` as stable entry-point
descriptors.
Each descriptor assembles four focused component files:

- `<domain>/release.json`: dataset identity, fixed values, output paths, table
  order, and release rules.
- `<domain>/schema.json`: tables, fields, row targets, keys, and relationships.
- `<domain>/generation.json`: domain values, generation rules, business
  mappings, distributions, and imperfection targets.
- `<domain>/validation.json`: join-path and consistency rules.

Generator code reads each assembled configuration through its domain loader;
component files are not consumed independently. Shared presets remain in
`base.json`, while domain-specific selections and overrides remain in the
domain's `generation.json`.

## Python Environment

Use Python 3.11. Create the virtual environment from the repository root so the
same environment can support generators, schema validation, Q&A verification,
judge scoring, scorecards, and tests.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Dependencies are pinned so seeded generation remains reproducible across
developer machines and clean-room regeneration checks.

To verify the environment:

```bash
python --version
python -m pip freeze
```

To leave the virtual environment:

```bash
deactivate
```

## CLI Workflows

Run commands from the repository root with the Python environment activated.
CRM, Sales, and Finance are implemented generation domains. Pass `--domain`
explicitly in repeatable workflows. Omitting it continues to select CRM for
backward compatibility.

### CRM Dataset Build

Use one command per profile. These workflows generate CSV files and run the
required post-generation checks against the persisted final CSVs rather than
requiring separate in-memory validation commands.

Development build:

```bash
python main.py build-dataset --domain crm --profile dev
```

This writes `base/`, `distributed/`, and `imperfect/` under
`tmp/generated/crm/dev/`, then validates row caps, DDL and foreign keys, join
paths, and imperfection rates from `imperfect/*.csv`.

Full release build:

```bash
python main.py build-dataset --domain crm --profile full
```

This generates `release/crm/dataset-v1.0.0/`, validates the written CSVs,
computes hashes, generates the data dictionary and schema SQL, performs a
clean-room reproducibility check, and writes `manifest.json` last. The manifest
seals the release; corrections require a new dataset version.

Both commands print numbered pipeline progress plus table-level generation and
CSV export progress.

### Sales Dataset Build

Use one command per profile. The Sales workflow applies the same persisted-data
gates and release ordering as the CRM workflow.

Development build:

```bash
python main.py build-dataset --domain sales --profile dev
```

This writes `base/`, `distributed/`, and `imperfect/` under
`tmp/generated/sales/dev/`, then validates row caps, DDL and foreign keys, join
paths, and imperfection rates from `imperfect/*.csv`.

Full release build:

```bash
python main.py build-dataset --domain sales --profile full
```

This generates `release/sales/dataset-v1.0.0/`, validates the persisted CSVs,
computes SHA-256 hashes, generates the data dictionary and schema SQL, performs
a clean-room reproducibility check, and writes `manifest.json` last. An existing
manifest prevents the release from being modified in place.

Sales generation produces `leads`, `deals`, `products`, `quotations`, and
`targets`. The `quotations` table is both the quote-line fact and the explicit
Deal-to-Product bridge.

Sales Q&A authoring is not implemented yet. The `qa-*` commands remain
explicitly CRM-only.

### Finance Dataset Build

Finance generates `accounts`, `transactions`, `ledger_entries`, `budgets`, and
`fx_rates`. Use one command per profile to run generation and persisted-data
validation in the required order.

Development build:

```bash
python main.py build-dataset --domain finance --profile dev
```

This writes `base/`, `distributed/`, and `imperfect/` under
`tmp/generated/finance/dev/`, then validates row caps, DDL and foreign keys,
accounting balance, join and FX paths, and imperfection rates from the persisted
`imperfect/*.csv` files.

Full release build:

```bash
python main.py build-dataset --domain finance --profile full
```

This generates `release/finance/dataset-v1.0.0/`, validates persisted CSVs and
Finance-specific accounting/FX contracts, computes hashes, writes the data
dictionary and schema SQL, performs clean-room reproducibility checks, and
writes `manifest.json` last.

Finance uses multiple source currencies and USD as its sole reporting currency.
Its rates are deterministic synthetic test values generated locally. No live
rate service or production financial data is read.

### Advanced CRM Commands

Use these commands when investigating a specific stage or validation gate.
They are not required when `build-dataset` succeeds.

Inspect resolved configuration or write one development stage:

```bash
python main.py validate-config --domain crm --profile dev
python main.py show-config --domain crm --profile dev
python main.py generate-base --domain crm --profile dev --write-preview
python main.py apply-distributions --domain crm --profile dev --write-preview
python main.py apply-imperfections --domain crm --profile dev --write-preview
```

Run one generated-data validation gate without relying on saved previews:

```bash
python main.py validate-relations --domain crm --profile dev
python main.py validate-row-caps --domain crm --profile dev --generated
python main.py validate-fk --domain crm --profile dev --generated
python main.py validate-join-paths --domain crm --profile dev --generated
python main.py validate-imperfection-rates --domain crm --profile dev --generated
```

Run or troubleshoot individual full-release stages before the manifest exists:

```bash
python main.py validate-config --domain crm --profile full
python main.py show-config --domain crm --profile full
python main.py validate-relations --domain crm --profile full
python main.py validate-row-caps --domain crm --profile full
python main.py validate-row-caps --domain crm --profile full --generated
python main.py export-csvs --domain crm --profile full
python main.py validate-row-caps --domain crm --profile full --exported
python main.py validate-fk --domain crm --profile full
python main.py validate-join-paths --domain crm --profile full
python main.py validate-imperfection-rates --domain crm --profile full
python main.py compute-sha256 --domain crm --profile full
python main.py generate-data-dictionary --domain crm --profile full
python main.py generate-schema-sql --domain crm --profile full
python main.py validate-reproducibility --domain crm --profile full
python main.py generate-manifest --domain crm --profile full
```

The final command above seals the release and must not be followed by another
release-writing command.

### Advanced Sales Commands

Use these commands to investigate an individual Sales generation stage or
validation gate. They are not required when `build-dataset` succeeds.

Inspect configuration, write dev stages, or validate generated data:

```bash
python main.py validate-config --domain sales --profile dev
python main.py show-config --domain sales --profile dev
python main.py generate-base --domain sales --profile dev --write-preview
python main.py apply-distributions --domain sales --profile dev --write-preview
python main.py apply-imperfections --domain sales --profile dev --write-preview
python main.py validate-relations --domain sales --profile dev
python main.py validate-row-caps --domain sales --profile dev --generated
python main.py validate-fk --domain sales --profile dev --generated
python main.py validate-join-paths --domain sales --profile dev --generated
python main.py validate-imperfection-rates --domain sales --profile dev --generated
```

Run or troubleshoot individual full-release stages before the manifest exists:

```bash
python main.py validate-config --domain sales --profile full
python main.py show-config --domain sales --profile full
python main.py validate-relations --domain sales --profile full
python main.py validate-row-caps --domain sales --profile full
python main.py validate-row-caps --domain sales --profile full --generated
python main.py export-csvs --domain sales --profile full
python main.py validate-row-caps --domain sales --profile full --exported
python main.py validate-fk --domain sales --profile full
python main.py validate-join-paths --domain sales --profile full
python main.py validate-imperfection-rates --domain sales --profile full
python main.py compute-sha256 --domain sales --profile full
python main.py generate-data-dictionary --domain sales --profile full
python main.py generate-schema-sql --domain sales --profile full
python main.py validate-reproducibility --domain sales --profile full
python main.py generate-manifest --domain sales --profile full
```

The final command seals the Sales release and must remain last.

### Advanced Finance Commands

Use these commands to investigate individual Finance stages or validation
gates. They are not required when `build-dataset` succeeds.

Inspect configuration, write dev stages, or validate generated data:

```bash
python main.py validate-config --domain finance --profile dev
python main.py show-config --domain finance --profile dev
python main.py generate-base --domain finance --profile dev --write-preview
python main.py apply-distributions --domain finance --profile dev --write-preview
python main.py apply-imperfections --domain finance --profile dev --write-preview
python main.py validate-relations --domain finance --profile dev
python main.py validate-row-caps --domain finance --profile dev --generated
python main.py validate-fk --domain finance --profile dev --generated
python main.py validate-join-paths --domain finance --profile dev --generated
python main.py validate-imperfection-rates --domain finance --profile dev --generated
```

Run or troubleshoot individual full-release stages before the manifest exists:

```bash
python main.py validate-config --domain finance --profile full
python main.py show-config --domain finance --profile full
python main.py validate-relations --domain finance --profile full
python main.py validate-row-caps --domain finance --profile full
python main.py validate-row-caps --domain finance --profile full --generated
python main.py export-csvs --domain finance --profile full
python main.py validate-row-caps --domain finance --profile full --exported
python main.py validate-fk --domain finance --profile full
python main.py validate-join-paths --domain finance --profile full
python main.py validate-imperfection-rates --domain finance --profile full
python main.py compute-sha256 --domain finance --profile full
python main.py generate-data-dictionary --domain finance --profile full
python main.py generate-schema-sql --domain finance --profile full
python main.py validate-reproducibility --domain finance --profile full
python main.py generate-manifest --domain finance --profile full
```

The final command seals the Finance release and must remain last.

### Q&A Pair Workflow

The root CLI also runs the CRM Q&A authoring pipeline. Each Q&A command consumes
the selected profile's generated dataset; it does not regenerate the golden
dataset itself.

#### Development Q&A Profile

First generate the imperfect dev CSVs, then run the complete production Q&A
workflow:

```bash
python main.py build-dataset --domain crm --profile dev
python main.py qa-build --domain crm --profile dev
```

The 160 final dev pairs, companion CSV, and verification logs are written
under:

```text
tmp/generated/crm/dev/qa_pairs/
```

#### Full Q&A Profile

The full Q&A pipeline reads the frozen CRM dataset from
`release/crm/dataset-v1.0.0`. After the dataset release is available, run:

```bash
python main.py qa-build --domain crm --profile full
```

The final full pair package is written to the independently versioned Q&A
release directory configured in `qa_pairs/generator/config.json`:

```text
release/crm/qa-pairs-v1.0.0/
```

`qa-build` stages the selected generated dataset, validates it, and generates
the complete SQL-verified pair set. It does not create review fixtures or
rephrase variants.

#### Advanced Q&A Commands

Use the individual commands only when troubleshooting a specific production
stage:

```bash
python main.py qa-stage-dataset --domain crm --profile dev
python main.py qa-validate-dataset --domain crm --profile dev
python main.py qa-generate-pairs --domain crm --profile dev
```

The following optional commands produce testing/review artifacts and are not
part of `qa-build`:

```bash
python main.py qa-author-fixtures --domain crm --profile dev
python main.py qa-generate-rephrases --domain crm --profile dev
```

Replace `dev` with `full` when troubleshooting the full profile. The original
scripts under `qa_pairs/generator/` remain available for backward compatibility,
but `main.py` is the preferred project entry point.

## First Build Track

CRM remains the reference implementation. Sales and Finance reuse the shared
deterministic core and release conventions while retaining their own business
semantics. Finance adds fixed-point accounting, synthetic FX conversion, and
double-entry integrity rules without placing those rules in the shared core.

## Current Status

The engagement-focused CRM generator, one-command dataset build, and CRM Q&A
workflow are implemented.

Sales is implemented through configuration, generation, distributions,
imperfections, validation, release artifact generation, reproducibility, root
CLI dispatch, and its full automated test suite. Sales dev workflow verification
and full release generation remain the next steps in
`Sales_Dataset_Generation_Implementation_Plan.md`.

Finance is implemented through configuration, deterministic generation,
fixed-point accounting and FX behavior, validation, release artifacts,
reproducibility, root CLI dispatch, and its full automated test suite.

Project Management and Logistics currently provide schema and design
assets and will follow the shared conventions proven by CRM, Sales, and
Finance.
