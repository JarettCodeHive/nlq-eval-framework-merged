# Development Guide

This guide covers individual pipeline stages, validation gates, and
troubleshooting commands. For installation and the supported end-to-end
workflows, start with the project [README](README.md).

Run commands from the repository root with the Python environment activated.
Pass both `--domain` and `--profile` explicitly in repeatable workflows.

## Supported Domains

Dataset generation supports these domain values:

- `crm`
- `sales`
- `finance`
- `project_management`
- `logistics`

Choose one for your shell session; the examples below use this variable:

```bash
DOMAIN=crm
```

## End-to-End Pipeline

After the credentials pass `check-auth`, build the dataset and Q&A pairs,
replace the configured platform dataset, and run a three-question judge preview
with the currently supported CRM or Sales Q&A workflow:

```bash
python main.py check-auth --domain "$DOMAIN"
python main.py run-pipeline --domain "$DOMAIN" --profile dev
```

This runs `build-dataset`, `qa-build`, `dataset-delete`, `dataset-upload`, and
`judge` in that order. It confirms `dataset-delete` internally as if `--yes`
were passed, removes and re-uploads the selected domain/profile, and supplies
`--allow-uncalibrated --limit 3` to the judge stage. The result cannot certify a
release. The individual commands and their existing options remain available.

## Dataset Builds

Use `build-dataset` for the complete generation and validation workflow:

```bash
python main.py build-dataset --domain "$DOMAIN" --profile dev
python main.py build-dataset --domain "$DOMAIN" --profile full
```

The `dev` profile writes staged output under
`tmp/generated/<domain>/dev/`. The `full` profile writes a versioned release
under `release/<domain>/`, runs persisted-data and reproducibility checks, and
writes `manifest.json` last to seal the release. Corrections to a sealed release
require a new dataset version.

## Q&A Pair Workflow

The root CLI runs the Q&A authoring pipeline for CRM and Sales. Each Q&A command
consumes the selected profile's generated dataset; it does not regenerate the
golden dataset itself. Build the dataset first, then run `qa-build` for the same
domain and profile.

### Development Q&A profile

Generate the imperfect development CSVs, then stage, validate, generate, and
SQL-verify the Q&A pair set:

```bash
python main.py build-dataset --domain crm --profile dev
python main.py qa-build --domain crm --profile dev
```

Replace `crm` with `sales` to run the Sales workflow. Development outputs are
disposable and are written beneath:

```text
tmp/generated/<domain>/dev/qa_pairs/
```

This directory contains the final pair contract, its companion metadata CSV,
and verification logs.

### Full Q&A profile

The full workflow reads the selected domain's frozen dataset release. Build or
confirm that release first, then run:

```bash
python main.py qa-build --domain crm --profile full
```

Replace `crm` with `sales` for Sales. The Q&A package has its own version,
independent from the dataset version, and is written to the location configured
in `qa_pairs/generator/<domain>/config.json`:

```text
release/<domain>/qa-pairs-v<qa_version>/
```

`qa-build` runs these production stages in order:

1. `qa-stage-dataset` stages the selected generated dataset in DuckDB.
2. `qa-validate-dataset` validates the staged data and required joins.
3. `qa-generate-pairs` generates and SQL-verifies the complete pair set.

It does not generate review fixtures or rephrase variants.

### Advanced Q&A commands

Use the individual commands only when troubleshooting one production stage:

```bash
python main.py qa-stage-dataset --domain crm --profile dev
python main.py qa-validate-dataset --domain crm --profile dev
python main.py qa-generate-pairs --domain crm --profile dev
```

These three production-stage commands support both CRM and Sales; replace
`crm` with `sales` as needed.

These optional commands produce testing or review artifacts and are not part of
`qa-build`. They are currently CRM-only:

```bash
python main.py qa-author-fixtures --domain crm --profile dev
python main.py qa-generate-rephrases --domain crm --profile dev
```

Replace `dev` with `full` when troubleshooting a full-profile stage. The
original scripts under `qa_pairs/generator/` remain available for backward
compatibility, but `main.py` is the preferred project entry point.

## Judge and Scorecard Workflow

Scoring also runs through `main.py`. A `judge` run writes deterministic
exact-match results and judge scores side by side, never as a composite, plus a
per-domain scorecard. The separate `score` command combines completed domain
runs into one cross-domain scorecard.

### Credentials and connectivity

Live judging needs the judge-provider and Pulse credentials in `judge/.env`.
Copy the annotated example, fill in the required values, and preflight both
services before spending time on a run:

```bash
cp judge/.env.example judge/.env
python main.py check-auth --domain "$DOMAIN"
```

`check-auth` mints a real judge-provider token, tests the platform credential,
and reports whether the Pulse token has enough life for the estimated run. It
exits with code 2 if either side is unusable. An expired AppleConnect session
can look valid until the token is actually requested, which is why this check
should precede a long run.

On a corporate network, this is also the quickest TLS and proxy check because
it exercises both network paths. If it fails with
`CERTIFICATE_VERIFY_FAILED` or an immediate `403`, see
[Corporate network: TLS and proxies](docs/judge_runbook.md#3a-corporate-network-tls-and-proxies).
The OS trust store is used automatically through `truststore`; explicit
`PULSE_CA_BUNDLE` and `FLOODGATE_CA_BUNDLE` values are escape hatches. HTTP
proxy variables are inherited from the shell and are not configured in this
project.

The browser-sourced Pulse JWT normally lasts about one hour. Without refresh,
a run that cannot fit within its remaining lifetime is refused before it
starts. Configure the `PULSE_REFRESH_*` values described in
`judge/.env.example` to let the client renew during a run. The example also
documents how to probe the refresh chain and the currently unconfirmed response
shape; do not assume refresh works until that probe passes.

### Resolved inputs and judge input construction

`judge` accepts the same `--domain` and `--profile` arguments as
`build-dataset` and `qa-build`. From them it resolves the Q&A package, the
combined dataset/Q&A version tag, and the CSV table directory used by
`--pulse sql`. Each has a dedicated override flag when an explicit path or
version is required:

```bash
python main.py judge --domain crm --profile full
python main.py judge --help
```

The §9.3 contract CSV contains answers but no identifiers, while the companion
CSV contains identifiers but no answers. The judge joins them on the unique
`natural_language_question` value when the judge input file is absent. Build it
deliberately after changing the pairs when you want to inspect the result:

```bash
python main.py judge-build-input --domain crm --profile full
```

The explicit dispatcher command is currently CRM-only; normal `judge` runs
perform the join on demand.

### Offline and live runs

Use the heuristic test double with local DuckDB execution to validate the
pipeline without calling Pulse or a semantic judge:

```bash
python main.py judge --domain crm --profile full \
  --judge heuristic \
  --pulse sql \
  --limit 3
```

The heuristic judge is deterministic development/CI scaffolding and is never
release-eligible. `--pulse sql` executes each pair's `reference_sql` against
the selected CSV dataset; it verifies the pairs and does not evaluate the live
platform.

For a live LLM preview against Pulse:

```bash
python main.py judge --domain crm --profile full \
  --judge llm \
  --pulse live \
  --limit 3 \
  --allow-uncalibrated
```

After the smoke test succeeds, omit `--limit` to score the complete selected
profile:

```bash
python main.py judge --domain crm --profile full \
  --judge llm --pulse live \
  --allow-uncalibrated
```

Until calibration passes, an LLM run without `--allow-uncalibrated` is refused.
The override always produces a `PREVIEW` labelled `calibrated=false`; it cannot
certify a release or establish an official baseline.

### Calibration and rubric

Calibration scores the independently reviewed anchors in
`judge/anchors/<domain>.json` and writes
`judge/.calibration/<domain>.passed.json` only when agreement passes:

```bash
python main.py calibrate --domain crm
python main.py rubric
```

The CRM anchors are currently quarantined as `crm.provisional.json` pending the
§10.2 Platform Owner session, so CRM calibration intentionally cannot produce a
release marker yet. `rubric` renders `docs/judge_rubric.pdf` from the same Jinja
templates used by the judge, preventing the signed document from drifting from
the scored prompt.

### Run artifacts

Each judge run uses one `run_id` across two append-only directories. Earlier
runs are never rewritten:

```text
release/<domain>/
├── dataset-v<version>/             sealed dataset package
├── qa-pairs-v<version>/            sealed Q&A package
├── scorecards/<run_id>/            per-domain §11 deliverables
│   ├── scorecard.pdf               stakeholder summary
│   ├── scorecard.md                Markdown summary
│   ├── scorecard_summary.csv       domain and tier aggregates
│   └── question_results.csv        one row per question
└── eval-runs/<run_id>/             evidence and provenance
    ├── results.json                machine-readable results
    ├── run_manifest.json           commit, input hash, and arguments
    ├── run_log.jsonl               timestamped event stream
    ├── prompts_log.jsonl           complete judge prompt/response log
    └── pulse_raw/                  untouched live platform responses
```

The roots come from `report_output_root` in `config/scorecard/` and
`run_output_root` in `config/judge/`. They are separate so stakeholder
deliverables can be handled independently from the much larger raw platform
payloads, which can be roughly 72 KB per question. A run directory is created
before the first network request, so a failed or interrupted run retains the
evidence already collected.

### Combined scorecards

Combine the latest completed runs for selected domains into a preview
scorecard under `release/scorecards/<run_id>/`:

```bash
python main.py score --domains crm,sales
```

Pin a specific run with repeatable `--run DOMAIN=RUN_ID` arguments. Add
`--release` only when every input is a calibrated live LLM run for the same
platform version:

```bash
python main.py score --domains crm,sales \
  --run crm=<crm-run-id> \
  --run sales=<sales-run-id> \
  --release
python main.py score --help
```

### Judge model precedence

The configured model is selected in this order:

1. `model` in `config/judge/<domain>.json`
2. `FLOODGATE_MODEL`, `OPENAI_MODEL`, or `LLM_MODEL` in the environment
3. `model` in `config/judge/default.json`

The per-domain pin makes a run reproducible from committed configuration.
Credentials remain in `judge/.env`; they must not be committed.

## Individual Stages and Troubleshooting

The commands below break the dataset workflow into its component stages. They
are intended for diagnosis; use `build-dataset` for a normal build.

### Generation configuration

`config/generation/base.json` contains shared deterministic defaults. Each
domain uses `config/generation/<domain>.json` as its stable entry-point
descriptor, assembling these component files:

- `<domain>/release.json`: dataset identity, fixed values, output paths, table
  order, and release rules.
- `<domain>/schema.json`: tables, fields, row targets, keys, and relationships.
- `<domain>/generation.json`: domain values, generation rules, business
  mappings, distributions, and imperfection targets.
- `<domain>/validation.json`: join-path and consistency rules.

Generator code reads the assembled configuration through its domain loader;
component files are not consumed independently. Shared presets remain in
`base.json`, while domain-specific selections and overrides remain in the
domain's `generation.json`.

Validate the assembled configuration or print its resolved generation metadata:

```bash
python main.py validate-config --domain "$DOMAIN" --profile dev
python main.py show-config --domain "$DOMAIN" --profile dev
```

Replace `dev` with `full` to inspect the release profile.

### Individual generation stages

```bash
python main.py generate-base --domain "$DOMAIN" --profile dev --write-preview
python main.py apply-distributions --domain "$DOMAIN" --profile dev --write-preview
python main.py apply-imperfections --domain "$DOMAIN" --profile dev --write-preview
```

Preview output is allowed only for non-release profiles.

### Validation gates

Run a validation gate against freshly generated data without relying on saved
preview files:

```bash
python main.py validate-relations --domain "$DOMAIN" --profile dev
python main.py validate-row-caps --domain "$DOMAIN" --profile dev --generated
python main.py validate-fk --domain "$DOMAIN" --profile dev --generated
python main.py validate-join-paths --domain "$DOMAIN" --profile dev --generated
python main.py validate-imperfection-rates --domain "$DOMAIN" --profile dev --generated
```

### Full-release stages

When troubleshooting a full build, run its stages individually in this order:

```bash
python main.py validate-config --domain "$DOMAIN" --profile full
python main.py show-config --domain "$DOMAIN" --profile full
python main.py validate-relations --domain "$DOMAIN" --profile full
python main.py validate-row-caps --domain "$DOMAIN" --profile full
python main.py validate-row-caps --domain "$DOMAIN" --profile full --generated
python main.py export-csvs --domain "$DOMAIN" --profile full
python main.py validate-row-caps --domain "$DOMAIN" --profile full --exported
python main.py validate-fk --domain "$DOMAIN" --profile full
python main.py validate-join-paths --domain "$DOMAIN" --profile full
python main.py validate-imperfection-rates --domain "$DOMAIN" --profile full
python main.py compute-sha256 --domain "$DOMAIN" --profile full
python main.py generate-data-dictionary --domain "$DOMAIN" --profile full
python main.py generate-schema-sql --domain "$DOMAIN" --profile full
python main.py validate-reproducibility --domain "$DOMAIN" --profile full
python main.py generate-manifest --domain "$DOMAIN" --profile full
```

`generate-manifest` seals the release and must remain last. Once a manifest
exists, corrections require a new dataset version rather than modifying the
release in place.

## Testing

Run the complete suite from the repository root:

```bash
python -m pytest
python -m pytest judge/tests tests/scorecard
```

Test suites live beside the components they cover. See [tests/README.md](tests/README.md)
for the test layout and more focused commands.

At the time this guide was updated, five CRM compatibility-baseline tests fail
because `CRM_Config_Simplification_Pre_Migration_Baseline.json` is absent from
the repository. This is unrelated to judge or scorecard behavior. The earlier
SQLFluff configuration failure is no longer current: `qa_pairs/.sqlfluff`
exists and the authoritative DDL test is configured with a dialect. Re-run the
commands above rather than relying on a historical total because the suite will
continue to evolve.

## Release and Platform Safety

- Use only staging and QA platform targets. This project is not for production
  data or environments.
- The `full` profile writes immutable, versioned release artifacts.
- `generate-manifest` must be the final release-writing command.
- `run-pipeline` confirms `dataset-delete` internally, so it deletes and then
  re-uploads the selected domain/profile on the configured platform.
- Runs using `--allow-uncalibrated` are previews and cannot certify a release.

## Additional Runbooks

- [Judge runbook](docs/judge_runbook.md)
- [Judge flow](docs/judge_flow.md)
- [Judge module documentation](judge/README.md)
- [Q&A module documentation](qa_pairs/README.md)
- [Generator documentation](generators/README.md)
