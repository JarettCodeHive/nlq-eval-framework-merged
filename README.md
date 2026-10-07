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

Prerequisite: configure `.env` at the repository root and verify both the
judge-provider and Pulse credentials before running the pipeline:

```bash
python main.py check-auth --domain crm
```

Build the dataset and Q&A pairs, refresh the platform dataset, and run question judge pass with one command:

```bash
python main.py run-pipeline --domain crm --profile full --version v1.0.0
```

`--domain` also accepts `sales`, `finance`, `project_management`, and
`logistics`; `crm` above is only the example.

Platform entities uploaded by `run-pipeline` are temporary and are deleted by
entity ID after the judge finishes. To retain the uploaded entities for
debugging, opt out explicitly:

```bash
python main.py run-pipeline --domain crm --profile full --version v1.0.0 --keep-platform-data
```

`--version` is the single evaluation-release version shared by the dataset,
Q&A pairs, judge evidence, and scorecard. A full run writes all artifacts under
one bundle root:

```text
release/v1.0.0/
  crm/
    dataset/
    qa_pairs/
    judge/<run_id>/
    scorecard/<run_id>/
    platform/
  scorecard/<combined_run_id>/
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
00:00:00.000 | INFO  | pipeline        | [1/6] START  Build and validate dataset
00:00:00.142 | INFO  | dataset         | Step 1/6: Validate CRM configuration and expected row caps
00:00:02.918 | INFO  | pipeline        | [1/6] DONE   Build and validate dataset (00:00:02.918)
...
00:00:41.204 | INFO  | pipeline        | SUCCESS domain=crm profile=dev elapsed=00:00:41.204
```

Every non-empty line carries elapsed time, severity, and component. The six
top-level stages emit `START`, `DONE`, or `FAILED`, while detailed output from
older subcommands is normalized under the active component. The format is plain
text by design, so it remains readable in terminals, CI systems, redirected log
files, and demo recordings. A failure names the stage and elapsed time before
the command exits non-zero.

> **Warning:** `run-pipeline` confirms `dataset-delete` internally. It deletes
> and re-uploads the selected domain/profile before judging. After judging, it
> deletes only entity IDs created by that upload invocation; it never discovers
> cleanup targets by table name. Cleanup also runs after upload or judge
> failures. A cleanup failure makes the command fail and remains recorded in
> the platform manifest. Use `dataset-delete --yes` for deliberate manual
> cleanup of retained data.

### Judge and Scorecard Workflow

The grading sheet deliberately carries no judge scores — a grader shown the
judge's 4 hands back a 4, and the measurement is agreement between two
independent opinions. Import refuses a set a constant-scoring judge would pass.

To combine completed per-domain judge runs into a preview scorecard, use:

```bash
python main.py score --domains "crm, sales, finance, project_management, logistics" --version v1.0.0
```

Use `python main.py judge --help` and `python main.py score --help` for the full
flag surfaces. See the [Judge Runbook](docs/judge_runbook.md) for release
requirements and provider setup.

### Individual Stages and Troubleshooting

For individual generation stages, validation gates, full-release
troubleshooting, advanced Q&A commands, and detailed judge operations, see the
[Development Guide](DEVELOPMENT.md).
