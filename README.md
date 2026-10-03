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
CRM, Sales, Finance, Project Management, and Logistics are implemented
generation domains.
Pass `--domain` explicitly in repeatable workflows. Omitting it continues to
select CRM for backward compatibility.

### End-to-End Pipeline

Build the dataset and Q&A pairs, refresh the platform dataset, and run a
three-question judge pass with one command:

```bash
python main.py run-pipeline --domain crm --profile dev
python main.py run-pipeline --domain crm --profile full --version v1.0.0
```

`--version` is the single evaluation-release version shared by the dataset,
Q&A pairs, judge evidence, and scorecard. A full run writes all artifacts under
one bundle root:

```text
release/crm/v1.0.0/
  dataset/
  qa_pairs/
  judge/<run_id>/
  scorecard/<run_id>/
```

If `--version` is omitted, the domain's `release_version` in
`config/generation/<domain>/release.json` is used. Existing bundle versions are
immutable: changing either the dataset or the Q&A pairs requires a new release
version. The Claris/Pulse platform version remains separate and is recorded in
the judge and scorecard metadata so the same evaluation release can measure
multiple application versions.

The end-to-end command uses one console format across dataset generation, Q&A,
platform refresh, upload verification, and judging:

```text
00:00:00.000 | INFO  | pipeline        | [1/5] START  Build and validate dataset
00:00:00.142 | INFO  | dataset         | Step 1/6: Validate CRM configuration and expected row caps
00:00:02.918 | INFO  | pipeline        | [1/5] DONE   Build and validate dataset (00:00:02.918)
...
00:00:41.204 | INFO  | pipeline        | SUCCESS domain=crm profile=dev elapsed=00:00:41.204
```

Every non-empty line carries elapsed time, severity, and component. The five
top-level stages emit `START`, `DONE`, or `FAILED`, while detailed output from
older subcommands is normalized under the active component. The format is plain
text by design, so it remains readable in terminals, CI systems, redirected log
files, and demo recordings. A failure names the stage and elapsed time before
the command exits non-zero.

> **Warning:** `run-pipeline` confirms `dataset-delete` internally. It deletes
> and re-uploads the selected domain/profile on the configured platform before
> judging.

Before running it, configure `judge/.env` and verify both the judge-provider and
Pulse credentials:

```bash
python main.py check-auth --domain crm
```

### Dataset Builds

Use `build-dataset` for the complete generation and validation workflow:

```bash
python main.py build-dataset --domain crm --profile dev
python main.py build-dataset --domain crm --profile full --version v1.0.0
```

Replace `crm` with `sales`, `finance`, `project_management`, or `logistics`.
The `dev` profile writes staged output under `tmp/generated/<domain>/dev/`.
The `full` profile writes the dataset under
`release/<domain>/<version>/dataset/`, runs
the persisted-data and reproducibility checks, and writes `manifest.json`
last to seal the release.

### Q&A Pair Workflow

The root CLI runs the Q&A authoring pipeline for CRM and Sales. Each Q&A command
consumes the selected profile's generated dataset; it does not regenerate the
golden dataset itself.

#### Development Q&A Profile

First generate the imperfect dev CSVs, then run the complete production Q&A
workflow:

```bash
python main.py build-dataset --domain crm --profile dev
python main.py qa-build --domain crm --profile dev
```

Replace `crm` with `sales` to run the Sales Q&A workflow. The `dev` profile
writes the final pairs, companion CSV, and verification logs under:

```text
tmp/generated/<domain>/dev/qa_pairs/
```

#### Full Q&A Profile

The `full` profile reads the selected domain's frozen dataset release. After
that release is available, run:

```bash
python main.py qa-build --domain crm --profile full --version v1.0.0
```

Replace `crm` with `sales` for the Sales Q&A workflow. The final pair package is
written under the same evaluation-release version as its dataset:

```text
release/<domain>/<version>/qa_pairs/
```

The Q&A build refuses to select a different or "latest" dataset release. The
single CLI/config release version resolves both components, preventing a pair
set from being verified against the wrong dataset.

`qa-build` stages the selected generated dataset, validates it, and generates
the complete SQL-verified pair set. For CRM it also generates the required
rephrase-group variants; review-only seed fixtures remain a separate command.

### Judge and Scorecard Workflow

Before a live judge run, copy `judge/.env.example` to `judge/.env`, add the
judge-provider and Pulse credentials, and verify both connections:

```bash
python main.py check-auth --domain crm
```

After `qa-build`, the judge input can be built explicitly. This step is useful
for inspection but optional because `judge` builds the input on demand:

```bash
python main.py judge-build-input --domain crm --profile dev
```

Run an offline development check with the heuristic test double and local SQL,
or run a three-question live LLM preview:

```bash
python main.py judge --domain crm --profile dev --judge heuristic --pulse sql
python main.py judge --domain crm --profile dev --judge llm --pulse live \
  --limit 3 --allow-uncalibrated
```

The heuristic judge is for development only. An uncalibrated LLM run is also a
preview and must not be used for an official scorecard. Once independently
reviewed anchors are available, calibrate the configured judge before a release
run:

```bash
python main.py calibrate --domain crm
python main.py rubric
```

To combine completed per-domain judge runs into a preview scorecard, use:

```bash
python main.py score --domains crm
```

Use `python main.py judge --help` and `python main.py score --help` for the full
flag surfaces. See the [Judge Runbook](docs/judge_runbook.md) for release
requirements and provider setup.

For authentication, calibration, offline judging, output artifacts, and
scorecard operations, see the [Judge Runbook](docs/judge_runbook.md),
[judge documentation](judge/README.md), and
[scorecard documentation](scorecard/README.md).

### Individual Stages and Troubleshooting

For individual generation stages, validation gates, full-release
troubleshooting, advanced Q&A commands, and detailed judge operations, see the
[Development Guide](DEVELOPMENT.md).

## Tests

```bash
python -m pytest
```

See [tests/README.md](tests/README.md) for the suite layout and focused test
commands.

## First Build Track

CRM remains the reference implementation. Sales, Finance, Project Management,
and Logistics reuse the shared deterministic core and release conventions
while retaining their own business semantics. Finance alone adds synthetic FX
conversion; the other implemented domains are USD-only.

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

Project Management and Logistics are implemented through configuration,
deterministic generation, distributions, controlled imperfections, persisted
validation, release artifacts, clean-room reproducibility, root CLI dispatch,
and domain-specific automated test suites. Their dev workflow verification and
final release generation remain separate acceptance steps.
