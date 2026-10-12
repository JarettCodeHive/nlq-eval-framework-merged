# Project Guide — Reading Path Through the Documentation

A suggested order to
read the documentation that already exists in this repository.

## Path A — "What is this project, and how do I run it?"

1. [`../README.md`](../README.md) — scope, hard constraints, repository
   layout, Python environment, the end-to-end CLI workflow. Start here.
2. [`ARCHITECTURE.md`](ARCHITECTURE.md) (this folder) — the five pipeline
   stages (`generators → qa_pairs → studio → judge → scorecard`), how
   `main.py` dispatches to them, and the `release/<version>/<domain>/`
   bundle that connects every stage to the next. Read this to
   understand *why* the repository is organized the way README's layout
   table describes.
3. [`../DEVELOPMENT.md`](../DEVELOPMENT.md) — the practical companion to
   #1: actual commands for dataset builds, Q&A generation, the judge, and
   the scorecard, plus troubleshooting. Treat this as the thing you come
   back to while actually running commands.

That's enough to run the pipeline and know what each stage produced.

## Path B — "I need to understand one pipeline stage in depth"

Each stage has its own README; read the stage you care about, in this
order if you're going deep on more than one (it follows the data, not file
alphabetization):

1. **Dataset generation** — [`../generators/README.md`](../generators/README.md)
   (shared generation rules + per-domain summary), then
   [`../schemas/README.md`](../schemas/README.md) and the specific domain's
   `schemas/<domain>/*_semantics.md` / `*_csv_header_spec.md` for the exact
   contract a generator implements against.
2. **Q&A pair authoring (Track B)** —
   [`../qa_pairs/README.md`](../qa_pairs/README.md) (current five-domain
   state, folder layout, the pair CSV contract), then
   [`../qa_pairs/docs/ARCHITECTURE.md`](../qa_pairs/docs/ARCHITECTURE.md)
   (how a question family + SQL template becomes a pair — this is the
   detailed version of what the top-level `ARCHITECTURE.md` only summarizes),
   then [`../qa_pairs/generator/README.md`](../qa_pairs/generator/README.md)
   for the shared per-domain file shape (`families.json`/`catalog.json`/etc.)
   and the per-domain `id_prefix` table. CRM-specific design history lives
   under `qa_pairs/taxonomy/` if you need it, but it's CRM-only, not a
   pattern the other four domains repeat.
3. **Platform upload/cleanup** —
   [`../studio/README.md`](../studio/README.md) — the only module that
   mutates platform state, and the ownership-based cleanup model behind
   `run-pipeline --cleanup-platform-data`.
4. **Scoring** — [`../judge/README.md`](../judge/README.md) is long but
   dense with decisions, not filler: what gets scored, the two scoring
   methods (deterministic exact-match vs. LLM judge) and why they're never
   combined, PREVIEW vs. RELEASE semantics, and several "open items" the
   client's spec left for this project to resolve (worth reading even if
   you only care about one of the two scorers). Supplement with
   [`judge_flow.md`](judge_flow.md) (diagrams) and
   [`judge_runbook.md`](judge_runbook.md) (how to actually operate a run)
   in this folder.
5. **Reporting** — [`../scorecard/README.md`](../scorecard/README.md) —
   the deliverables, and the baseline/regression comparison rule.

## Path C — "I'm about to touch code, not just read" (`CONTRIBUTING.md`)

[`../CONTRIBUTING.md`](../CONTRIBUTING.md) — branching, review, lint/test
gates actually enforced by `.github/workflows/ci.yml`, large-file and
secrets handling. Read this before your first pull request, not after.

## Path D — "I need the configuration surface, not the code"

[`../config/README.md`](../config/README.md) — what lives in
`config/generation/<domain>/`, `config/judge/`, `config/scorecard/`,
`config/orchestrator/`, and how each pipeline stage reads it instead of
hard-coding values. Pairs with `schemas/` from Path B #1 — config is the
*tunable* half of a domain's contract, schema is the *fixed* half.

## Path E — "I only need operational/reference material, not architecture"

These don't require Path A/B first — they're reference documents, not
narrative:

- [`judge_runbook.md`](judge_runbook.md) — operational steps for a judge run.
- [`judge_flow.md`](judge_flow.md) — mermaid diagrams of the judge flow.
- [`pulse_api_reference.md`](pulse_api_reference.md) /
  [`pulse_api_brief.md`](pulse_api_brief.md) — the platform API surface
  `judge/` and `studio/` talk to.
- [`client_demo_walkthrough.md`](client_demo_walkthrough.md) — the
  client-facing demo script.
- [`judge_rubric.pdf`](judge_rubric.pdf) — the §14.1 rubric deliverable
  (regenerate with `python -m judge.rubric`, never hand-edit).

## Path F — "Where do audit/findings records live?"

[`findings/<domain>/`](findings) — dated judge-failure root-cause analyses,
one subfolder per domain, regenerated each time a new failure batch is
investigated. Historical record, not something to action directly.

## What this guide deliberately doesn't cover

Test suites (`tests/README.md`, `tests/generators/README.md`,
`tests/schemas/README.md`, `qa_pairs/tests/`) aren't in a path above —
they're read when you're verifying or extending coverage, not when
building a mental model of the system, and each one is already scoped to
one stage covered in Path B.

---

## Where this file lives

**`docs/`**, alongside `ARCHITECTURE.md` — not repo root. This guide is
cross-cutting (it indexes every module, not one) and has ongoing reference
value rather than being a one-time decision record, so it doesn't belong at
repo root, which is reserved for disposable, one-time planning docs. It
also isn't a module's own README, so it doesn't belong co-located with one
piece of code either. `docs/` — reserved for exactly this kind of
cross-cutting, lasting material — is the correct home, and keeping it next
to `ARCHITECTURE.md` (the system view this guide points at first) keeps the
two obviously related.

It will go stale the same way any navigation doc does if a new module is
added without a line here — treat updating this file as part of the normal
"update the relevant README" review habit whenever a domain or module is
added.
