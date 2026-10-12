# System Architecture

A system-level view of how this repository's pieces fit together: five
pipeline stages, one shared release-bundle contract between them, and where
the CLI and the platform-mutating module sit relative to that pipeline. This
document is deliberately thin — it links to the module that owns each
detail rather than restating it.

For the command surface (what to actually type), see the root
[`README.md`](../README.md) and [`DEVELOPMENT.md`](../DEVELOPMENT.md). This
page is the *system* those commands drive, not the commands themselves.

## The pipeline, end to end

```
generators/  --->  qa_pairs/  --->  studio/  --->  judge/  --->  scorecard/
 (dataset)        (Q&A pairs)      (upload)       (scoring)      (report)
```

1. **[`generators/`](../generators/README.md)** — seeded synthetic data for
   one domain (CRM, Sales, Finance, Logistics, Project Management).
   Deterministic from a single root seed (§HC-7): no wall-clock reads, no
   network calls, no unordered-iteration dependence. Produces the domain's
   CSVs, schema SQL, data dictionary, and manifest.
2. **[`qa_pairs/`](../qa_pairs/README.md)** (Track B) — stages those CSVs
   into a typed DuckDB, validates them (FK integrity, join paths,
   imperfection rates), then generates 160 question/answer pairs per domain
   from a declarative `families.json` + `catalog.json` + SQL templates
   (`qa_pairs/docs/ARCHITECTURE.md` is this stage's own architecture
   document — read that one for *how* a pair is generated; this page only
   says *where* the stage sits). Each pair carries `expected_answer`
   (machine-derived, for the deterministic scorer) and `judge_reference`
   (for the LLM judge).
3. **[`studio/`](../studio/README.md)** — the only module that mutates
   platform state. Uploads a domain's released CSVs to Claris Studio as
   tables, in dependency order, and can clean up afterward using an
   ownership record of exactly what it created (`--cleanup-platform-data`
   on `run-pipeline`; see `studio/README.md#upload-and-the-ownership-based-cleanup-model`).
   Everything upstream of this stage runs entirely locally.
4. **[`judge/`](../judge/README.md)** — submits each question to the
   platform (`--pulse live`) or replays its `reference_sql` locally
   (`--pulse sql`, verification-only), then scores the platform's answer
   two ways: a deterministic zero-tolerance exact-match comparison against
   `expected_answer` (`judge/exact_match.py`), and an independent
   LLM-as-judge pass against `judge_reference` on four rubric dimensions
   (factual correctness, completeness, format adherence, SQL plausibility).
   The two scores are never combined into a composite (§11.3).
5. **[`scorecard/`](../scorecard/README.md)** — aggregates one judge run
   into the stakeholder-facing deliverables: a per-domain/per-tier CSV
   summary, a per-question drill-down CSV, a Markdown summary, and a PDF.
   Compares against the domain's immutable accuracy baseline and flags a
   regression at ≥5 percentage points.

**[`orchestrator/`](../orchestrator/README.md)** is a sixth, deferred stage
— design-only, pending a client decision on an internal evaluation tool. No
implementation code belongs there until the engagement scope is formally
updated.

## Where the CLI sits

[`main.py`](../main.py) is the single entry point for every stage above —
there is no stage with its own separate CLI. Its `COMMANDS` table dispatches
one argument (`validate-config`, `build-dataset`, `qa-build`,
`dataset-upload`, `dataset-delete`, `judge`, `score`, `calibrate`, `rubric`,
and the rest) to the function that implements it, each taking `--domain` and
`--profile` as its primary interface.

`run-pipeline` is the convenience wrapper that drives stages 1, 2, 3
(delete-existing), 3 (upload), and 4 back to back for one domain — the
individual commands it calls remain usable on their own for iterating on a
single stage. Scorecard generation (stage 5) is invoked separately via
`score`, since it operates across whatever judge runs already exist rather
than producing exactly one new run.

## The release bundle — what connects the stages

Every stage after `generators/` reads output that a prior stage wrote and
writes output the next stage will read. The contract between them is one
directory layout, owned by `qa_pairs/utils/release_bundle.py`:

```
release/<version>/<domain>/
  dataset/     generators/   — CSVs, schema SQL, data dictionary, manifest
  qa_pairs/    qa_pairs/     — the 160+ Q&A pairs, rephrase groups
  platform/    studio/       — upload/cleanup manifest (entity IDs, timestamps)
  judge/       judge/        — results.json, prompts_log.jsonl, pulse_raw/, run_log.jsonl
  scorecard/   scorecard/    — scorecard_summary.csv, question_results.csv, .md, .pdf
  logs/        main.py       — one text log per CLI command invoked against this version
```

`<version>` is one directory-safe name (`v1.0.0`, `v1.0.1-full`, ...),
selected once per CLI invocation via `--version` and made visible to every
component for that invocation through a `ContextVar`
(`use_release_version()`/`selected_release_version()`) rather than threaded
through every function call individually. `<domain>` is one of `crm`,
`sales`, `finance`, `logistics`, `project_management`. A retired
domain-first layout (`release/<domain>/<version>/`) is still read for
backward compatibility but never written.

This is the one piece of connective tissue no single module's README
documents on its own, because no single module owns it — `generators/`
writes `dataset/`, but has no reason to know that `qa_pairs/` reads it from
there next.

## Shared infrastructure, not a pipeline stage

- **[`config/`](../config/README.md)** — per-domain JSON configuration
  (`config/generation/<domain>/`, `config/judge/<domain>.json`,
  `config/scorecard/`) that every stage above reads rather than
  hard-coding. Platform-agnostic; no business logic.
- **[`schemas/`](../schemas/README.md)** — the canonical DDL and semantics
  documents each domain's generator and Q&A templates implement against.
- **[`scripts/`](../scripts/README.md)** — standalone utilities, including
  `check_cross_domain_uniqueness.py`, a hard CI dependency
  (`.github/workflows/ci.yml`'s `cross-domain-uniqueness` job), not just a
  local convenience script.
- **`.env`** (repo root; see `.env.example`) — one credential file for the
  judge provider, the Pulse platform client, and the Studio uploader. See
  [`CONTRIBUTING.md`](../CONTRIBUTING.md) for the secrets-handling rule.

## Why this split, not a monolith

Each stage above runs as its own CLI command and reads/writes only the
release-bundle paths above — nothing imports across stage boundaries except
through those files and `qa_pairs/utils/`'s domain-agnostic helpers. That
means:

- `generators/`, `qa_pairs/`, and `judge --pulse sql` need no platform
  credential at all and can run offline, in CI, deterministically.
- `studio/` is the only module that can mutate the shared org, which is why
  it is also the only module reviewed with that risk in mind
  (`CONTRIBUTING.md`'s review rule).
- Any stage can be re-run in isolation against an existing release bundle
  without re-running the stages before it — rescoring a judge run doesn't
  require regenerating the dataset.

## Further reading

- [`qa_pairs/docs/ARCHITECTURE.md`](../qa_pairs/docs/ARCHITECTURE.md) — Track
  B's own architecture, one level deeper than this page covers.
- [`judge/README.md`](../judge/README.md) — judge module layout, the two
  scoring methods in detail, PREVIEW vs. RELEASE semantics, calibration.
- [`studio/README.md`](../studio/README.md) — the ownership-based cleanup
  model behind `--cleanup-platform-data`.
- [`docs/judge_flow.md`](judge_flow.md) — mermaid flow diagrams for the
  judge module specifically.
- [`docs/judge_runbook.md`](judge_runbook.md) — operational runbook for
  running a judge pass.
- [`docs/PROJECT_GUIDE.md`](PROJECT_GUIDE.md) — a reading path through all
  of this repository's documentation, if you need more than this page.
