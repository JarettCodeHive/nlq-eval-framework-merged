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
```

> **Warning:** `run-pipeline` confirms `dataset-delete` internally. It deletes
> and re-uploads the selected domain/profile on the configured platform before
> judging. Its judge stage uses `--allow-uncalibrated --limit 3`, so the result
> is a preview and cannot certify a release.

Before running it, configure `.env` at the repository root and verify both the judge-provider and
Pulse credentials:

```bash
python main.py check-auth --domain crm
```

### Dataset Builds

Use `build-dataset` for the complete generation and validation workflow:

```bash
python main.py build-dataset --domain crm --profile dev
python main.py build-dataset --domain crm --profile full
```

Replace `crm` with `sales`, `finance`, `project_management`, or `logistics`.
The `dev` profile writes staged output under `tmp/generated/<domain>/dev/`.
The `full` profile writes a versioned release under `release/<domain>/`, runs
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
python main.py qa-build --domain crm --profile full
```

Replace `crm` with `sales` for the Sales Q&A workflow. The final pair package is
written to the independently versioned Q&A release directory configured in
`qa_pairs/generator/<domain>/config.json`:

```text
release/<domain>/qa-pairs-v<qa_version>/
```

`qa-build` stages the selected generated dataset, validates it, and generates
the complete SQL-verified pair set. It does not create review fixtures or
rephrase variants.

### Judge and Scorecard Workflow

Before a live judge run, copy `.env.example` to `.env` **at the repository root**
— one file configures the judge, the Pulse client and the Studio uploader — then
add the credentials and verify both connections:

```bash
python main.py check-auth --domain crm
```

#### Getting the Pulse credentials

There is no API-key page; every value is captured from a browser session. Sign in
to Claris Studio QA in Chrome, open a chat, open DevTools → Network. **Two
requests carry everything.**

**1. `GET https://api-qa.platform.claris.com/org/<ORG_ID>/chat?query=<base64>`**
— the chat-history request the UI fires whenever a chat is open.

- `PULSE_AUTH_TOKEN` — the `Authorization: Bearer …` request header (~1 hour life)
- `PULSE_ORG_ID` — the integer path segment, `/org/<ORG_ID>/chat` (QA: `4104`)

**2. `POST https://api-qa.platform.claris.com/auth/token`** — read the **request
body**, not a header, and not the response.

- `PULSE_REFRESH_TOKEN` — the long-lived Cognito refresh token. With it the client
  re-mints hour-long tokens by itself, which is what makes a 2-hour run possible.
- `PULSE_COGNITO_CLIENT_ID` — the `clientID` field in the same body.

Verify the chain before a long run: `python judge/pulse_auth.py --probe`

> **Corporate network — three hosts must be reachable**, each a separate
> allowlist entry: `cognito-idp.us-west-2.amazonaws.com` (Cognito refresh),
> `studio-qa.platform.claris.com` (token exchange), and
> `api-qa.platform.claris.com` (the chat endpoint). A blocked `studio-qa` is the
> one that bites — it is only reached after Cognito succeeds, so the failure reads
> as a platform fault rather than a missing entry. `floodgate.g.apple.com` is
> needed for the judge, separately.

Run an offline check that replays each pair's `reference_sql` in DuckDB, or a
three-question live preview:

```bash
python main.py judge --domain crm --profile dev --pulse sql
python main.py judge --domain crm --profile dev --pulse live \
  --limit 3 --allow-uncalibrated
```

Anthropic via Floodgate is the only judge backend, so `--pulse sql` still spends
provider quota — it verifies that pairs and dataset agree (§14.2) and never
scores the platform, which is why its runs are PREVIEW-only.

An uncalibrated run is a preview and is labelled `calibrated: false` on every
artifact. Calibration needs ≥10 human-graded anchors per domain (§10.2); export
candidates from a scored run, grade them, import them back:

```bash
python main.py anchors-export --domain crm --profile full
# fill the human_* columns, reconcile between BOTH graders, then:
python main.py anchors-import --domain crm --sheet <the filled sheet>
python main.py calibrate --domain crm
python main.py rubric
```

The grading sheet deliberately carries no judge scores — a grader shown the
judge's 4 hands back a 4, and the measurement is agreement between two
independent opinions. Import refuses a set a constant-scoring judge would pass.

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
