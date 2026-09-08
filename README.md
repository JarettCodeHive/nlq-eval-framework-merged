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
  generators/    Seeded data generation modules, one domain at a time.
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
CRM is currently the implemented generation domain.

### Development Profile

The `dev` profile uses smaller row counts for fast local generation and
validation. It never writes versioned release artifacts.

1. Validate the generation config against the canonical CRM schema, then show
   the resolved deterministic settings:

```bash
python main.py validate-config --profile dev
python main.py show-config --profile dev
```

2. Inspect each generation stage in memory:

```bash
python main.py generate-base --profile dev
python main.py apply-distributions --profile dev
python main.py apply-imperfections --profile dev
```

Each stage regenerates its required preceding stages deterministically; it does
not consume CSV output from an earlier command.

3. Optionally write stage CSVs under `tmp/generated/crm/dev/` for local
   inspection and notebooks:

```bash
python main.py generate-base --profile dev --write-preview
python main.py apply-distributions --profile dev --write-preview
python main.py apply-imperfections --profile dev --write-preview
```

Preview writes are refused for the `full` profile.

4. Run development validation against newly generated data:

```bash
python main.py validate-relations --profile dev
python main.py validate-row-caps --profile dev
python main.py validate-row-caps --profile dev --generated
python main.py validate-fk --profile dev --generated
python main.py validate-join-paths --profile dev --generated
python main.py validate-imperfection-rates --profile dev --generated
```

### Full Release Profile

The `full` profile creates and validates the delivery-scale release. Run these
commands in order. If the target release already contains `manifest.json`,
create a new dataset version rather than modifying the sealed release.

1. Validate and inspect the resolved full-profile configuration:

```bash
python main.py validate-config --profile full
python main.py show-config --profile full
```

2. Validate the full generated dataset before writing release files:

```bash
python main.py validate-relations --profile full
python main.py validate-row-caps --profile full
python main.py validate-row-caps --profile full --generated
```

3. Generate the complete imperfect dataset and export the release CSVs:

```bash
python main.py export-csvs --profile full
```

`export-csvs` regenerates the base data, distributions, and imperfections. It
does not consume dev preview CSVs.

4. Validate the exported release CSVs:

```bash
python main.py validate-row-caps --profile full --exported
python main.py validate-fk --profile full
python main.py validate-join-paths --profile full
python main.py validate-imperfection-rates --profile full
```

5. Compute and inspect the exported CSV hashes:

```bash
python main.py compute-sha256 --profile full
```

6. Generate the release documentation and canonical schema copy:

```bash
python main.py generate-data-dictionary --profile full
python main.py generate-schema-sql --profile full
```

7. Regenerate the CSVs in temporary storage and confirm that their SHA-256
   hashes are byte-identical to the exported release:

```bash
python main.py validate-reproducibility --profile full
```

8. Generate the immutable manifest as the final release command:

```bash
python main.py generate-manifest --profile full
```

The manifest seals the release directory. No CSV, documentation, schema, or
other release-writing command should run afterward.

## First Build Track

CRM is the reference implementation. Build and freeze the CRM schema and
dataset before authoring CRM Q&A pairs. Other domains should inherit the
conventions proven in CRM.

## Current Status

The engagement-focused CRM implementation is complete through config, base
generation, distributions, imperfections, validation, signed CSV headers,
data-dictionary generation, and the Step 18 release packaging pipeline. Q&A
assets and notebook migration remain deferred in Steps 19 and 20; the full CRM
test-suite migration is complete through Step 21. Dev inspection and final
release regeneration follow in Steps 22 onward of
`CRM_Engagement_Schema_Implementation_Plan.md`.

The remaining domains currently provide schema and design assets and will
follow the CRM reference implementation.
