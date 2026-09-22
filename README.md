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

`config/generation/base.json` contains shared deterministic defaults. CRM uses
`config/generation/crm.json` as its stable entry point; that small descriptor
assembles four focused files:

- `config/generation/crm/release.json`: dataset identity, fixed values, output
  paths, table order, and release rules.
- `config/generation/crm/schema.json`: tables, fields, row targets, keys, and
  relationships.
- `config/generation/crm/generation.json`: domain values, generation rules,
  business mappings, distributions, and imperfection targets.
- `config/generation/crm/validation.json`: join-path and consistency rules.

Generator code reads the assembled configuration through `load_crm_config()`;
component files are not consumed independently.

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

### Q&A Pair Workflow

The root CLI also runs the CRM Q&A authoring pipeline. Each Q&A command consumes
the selected profile's generated dataset; it does not regenerate the golden
dataset itself.

#### Development Q&A Profile

First generate the imperfect dev CSVs, then run the Q&A stages in order:

```bash
python main.py apply-imperfections --profile dev --write-preview
python main.py qa-stage-dataset --profile dev
python main.py qa-validate-dataset --profile dev
python main.py qa-author-fixtures --profile dev
python main.py qa-generate-pairs --profile dev
python main.py qa-generate-rephrases --profile dev
```

The 160 final dev pairs, companion CSV, verification logs, and rephrase
artifacts are written under:

```text
tmp/generated/crm/dev/qa_pairs/
```

#### Full Q&A Profile

The full Q&A pipeline reads the frozen CRM dataset from
`release/crm/dataset-v1.0.0`. Generate and validate that dataset first, then run:

```bash
python main.py qa-stage-dataset --profile full
python main.py qa-validate-dataset --profile full
python main.py qa-author-fixtures --profile full
python main.py qa-generate-pairs --profile full
python main.py qa-generate-rephrases --profile full
```

The final full pair package is written to the independently versioned Q&A
release directory configured in `qa_pairs/generator/config.json`:

```text
release/crm/qa-pairs-v0.3.0/
```

`qa-author-fixtures` writes review-only seed fixtures under
`qa_pairs/fixtures/<profile>/`; these fixtures are not part of the 160-pair
release. The original scripts under `qa_pairs/generator/` remain available for
backward compatibility, but `main.py` is the preferred project entry point.

### Judge and Scorecard Workflow

Scoring runs through `main.py` as well. There is no separate scorecard command:
a scoring run writes the scorecard artifacts itself, because §11.3 requires
exact-match accuracy and judge scores to be reported side by side from the same
run.

First join the Q&A release into a single judge input. The §9.3 contract file
carries no identifiers and the companion carries no answers, so the judge needs
them joined on `natural_language_question`:

```bash
python main.py judge-build-input --profile full
```

That writes `<qa-release>/crm_judge_input.csv`. Then score. `score` owns its own
flag surface — everything after it goes to the judge's parser:

```bash
python main.py score --help          # the judge's full flag surface
```

An offline run, executing each pair's `reference_sql` in DuckDB instead of
calling the platform. Use this for CI and for validating the pipeline:

```bash
python main.py score \
  --judge heuristic \
  --pulse sql \
  --input-csv release/crm/qa-pairs-v0.3.0/crm_judge_input.csv \
  --pulse-data release/crm/dataset-v1.0.0 \
  --limit 3 \
  --allow-uncalibrated
```

A real run against the platform, scored by the LLM judge. Requires the
`PULSE_*` and provider credentials in `judge/.env` — copy `judge/.env.example`
and fill it in:

```bash
python main.py score --judge llm --pulse live \
  --input-csv release/crm/qa-pairs-v0.3.0/crm_judge_input.csv \
  --allow-uncalibrated
```

Each run writes two directories under `release/<domain>/`, sharing one
`run_id` so a scorecard is always traceable to the evidence behind it:

```text
release/crm/
├── dataset-v1.0.0/            sealed, one version
├── qa-pairs-v0.3.0/           sealed, one version
├── scorecards/<run_id>/       the §11 deliverables
│   ├── scorecard.pdf          stakeholder summary (§11.3)
│   ├── scorecard.md           GitHub-renderable summary
│   ├── scorecard_summary.csv  §11.1 per domain + per tier
│   └── question_results.csv   §11.2 one row per question
└── eval-runs/<run_id>/        provenance for that scorecard
    ├── results.json           question-level drill-down
    ├── run_manifest.json      git commit, input hash, argv
    ├── run_log.jsonl          timestamped event stream
    ├── prompts_log.jsonl      full judge prompt/response log
    └── pulse_raw/             untouched platform payloads (live runs)
```

The two roots are configured as `report_output_root` in `config/scorecard/` and
`run_output_root` in `config/judge/`, so either can move without a code change.
They are split because the scorecard is the deliverable §13.1 wants versioned in
`release/`, while `pulse_raw/` is ~72KB per question of raw org data that should
not be tracked.

Unlike the dataset and Q&A packages, both are append-only rather than a sealed
single version — each scoring pass adds a directory and never rewrites an
earlier one.

`--allow-uncalibrated` is required until calibration passes. Every run without
it is refused, and an uncalibrated run is always labelled `PREVIEW`: it cannot
establish a baseline or certify a release (§10.2, §11.3).

Before a long run on a new machine, preflight both credentials — the judge
provider and the platform — rather than discovering a missing one 18 minutes in:

```bash
python main.py check-auth --domain crm
```

It mints a real judge token (an expired AppleConnect session looks identical to a
working one until you ask it for one) and reports how much life the Pulse token
has left against the estimated run length. Exit code 2 if either side is unusable.

On a corporate network this is also the fastest TLS/proxy check: both paths are
exercised. If it fails with `CERTIFICATE_VERIFY_FAILED` or an instant `403`, see
**§3a Corporate network: TLS and proxies** in `docs/judge_runbook.md` — TLS is
configured with `PULSE_CA_BUNDLE` / `FLOODGATE_CA_BUNDLE`, while proxies are
inherited from the standard environment variables and are deliberately not
configured in this project.

The Pulse token lasts one hour and has no refresh by default, so a run longer
than its remaining life is refused up front. Setting `PULSE_REFRESH_*` in
`judge/.env` lets the client re-mint its own token during a run and removes that
ceiling — see `judge/.env.example`, and note the endpoint is not yet confirmed.

```bash
python main.py calibrate --domain crm
```

Calibration scores the human-agreed anchors in `judge/anchors/<domain>.json`
and writes the `judge/.calibration` marker when agreement passes. It currently
fails by design — the CRM anchors are quarantined as `crm.provisional.json`
pending the §10.2 Platform Owner session, so no run is release-eligible yet.

```bash
python main.py rubric
```

Renders the §14.1 rubric PDF deliverable from the same Jinja templates the
judge runs against, so the document cannot drift from the scored prompt.

The judge model comes from `config/judge/<domain>.json`, which outranks
`FLOODGATE_MODEL` / `OPENAI_MODEL` / `LLM_MODEL` in the environment, which in
turn outranks `config/judge/default.json`. Pinning it per domain is what keeps a
run's provenance reproducible from committed config rather than from whoever's
machine it ran on. Only credentials belong in `judge/.env`.

## Tests

```bash
python -m pytest                      # everything
python -m pytest judge/tests tests/scorecard   # judge + scorecard only (199)
```

Suites live next to what they cover: `judge/tests/` and `qa_pairs/tests/` inside
their packages, generator and schema suites under `tests/`.

Six failures are **pre-existing and unrelated to scoring**, so a clean checkout
does not start green. Know them before assuming a change broke something:

| Failing | Cause |
|---|---|
| `tests/generators/crm/test_config_compatibility_baseline.py` (5) | `CRM_Config_Simplification_Pre_Migration_Baseline.json` is not in the repo — the test reads it from the root and gets `FileNotFoundError` |
| `qa_pairs/tests/test_sqlfluff.py::test_authoritative_ddl_is_sqlfluff_clean` | sqlfluff has no configured dialect; needs `--dialect` or a `.sqlfluff` config |

Both belong to the generator and Q&A side. `judge/tests` and `tests/scorecard`
are green.

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
