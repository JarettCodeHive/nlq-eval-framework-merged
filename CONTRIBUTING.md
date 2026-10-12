# Contributing

This repository is an internal client-delivery engagement, not an
open-source project, but it has multiple engineers working in parallel and
needs the same discipline. This file distills the Execution Scope's §13.2
standards (branching, review, CI, large files, secrets) into the repository
itself, since none of that was previously written down anywhere a new
engineer would actually read it — see `DOCUMENTATION_IMPLEMENTATION_PLAN.md`
§3.3.

## Branching

Trunk-based: `main` is the release branch, `development` is the integration
branch. No direct commits to either — work happens on a short-lived feature
branch cut from `development` (`fix_cleanup_dataset`,
`qa-pairs/sync-lint-gate-and-question-id`, `logs_and_refactoring`, and
similar are the convention already in use) and lands back via pull request.

## Review

At least one reviewer before merging. Any change touching data generation
(`generators/`), reference SQL (`schemas/*/*.sql`, `qa_pairs/*/templates/`),
or the deterministic judge (`judge/exact_match.py`) should be reviewed by
someone other than the author — those three are the places where a quiet
regression changes what the framework reports as correct, not just what it
outputs.

## Linting and formatting

Enforced in CI (`.github/workflows/ci.yml`, `lint` job), not just a local
convention:

```bash
ruff check .
black --check .
```

Reference DDL is linted separately with `sqlfluff` (`qa_pairs/schema/ddl.sql`
today; see the note in `.github/workflows/ci.yml` about this being CRM-only
so far). `qa_pairs/` pins a 100-character line length
(`qa_pairs/pyproject.toml`); everywhere else uses the project default (88).
Run `black .` / `ruff check --fix .` before pushing rather than relying on CI
to catch formatting.

## Tests

```bash
python -m pytest tests/            # generators, judge, scorecard
python -m pytest qa_pairs/tests/   # Q&A pair contract + scoring (CRM dataset)
```

CI runs three test-bearing jobs beyond lint: `qa-pairs` (builds the CRM full
release, then its pytest suite — a hard gate), `cross-domain-uniqueness`
(builds all five domains' full releases, then
`scripts/check_cross_domain_uniqueness.py` — a hard gate), and `full-suite`
(`pytest tests/` — currently **informational only**, `continue-on-error:
true`, because of pre-existing failures tracked in that job's own comment in
`ci.yml`; check the job summary rather than the red X). A new generator or
validator should come with unit tests under `tests/generators/<domain>/`
following the pattern of the existing domains, and a dataset change should
not introduce a new `full-suite` failure even though that job won't block
the merge today.

Per `DEVELOPMENT.md`/this project's own working convention: a pure
documentation change does not need a full `pytest tests/` run — `git status`
to confirm only `.md` files changed is enough. Anything touching `.py` files
does.

## Large files

No full-scale dataset CSV, DuckDB file, or release bundle is committed to
git — `release/`, `tmp/`, and the generator-local `qa_pairs/dataset/` are
gitignored for exactly this reason (see `.gitignore`). These are
regenerated deterministically from a seed (§HC-7); they are build output,
not source. There is currently no Git LFS configuration in this repository
(no `.gitattributes`) — if a binary deliverable ever needs to be checked in
(e.g. a signed-off rubric PDF snapshot), use LFS rather than committing it
as a regular blob, and add the LFS tracking rule at that point.

## Secrets

No credential is ever committed. `.env` is gitignored; `.env.example` is the
checked-in template and is kept current whenever a new variable is added.
Client-issued credentials (`PULSE_AUTH_TOKEN`, `PULSE_REFRESH_TOKEN`,
`PULSE_ORG_ID`, Floodgate tokens) live only in each engineer's local `.env`
or the CI secret store — never in code, test fixtures, logs, or a committed
screenshot. `judge/run_log.py`'s redaction (masking any key matching
token/key/secret/password/authorization/bearer, at any nesting depth) is the
backstop for *runtime* logs; it does not excuse committing a credential to
the repository in the first place.

## Where things live

Before adding a new module or domain, read the placement standard in
`DOCUMENTATION_IMPLEMENTATION_PLAN.md` §1 and the system view in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). In short: a module's own
`README.md` travels with its code; cross-cutting documentation goes in
`docs/`; dated audit findings go in `docs/findings/<domain>/`, never at repo
root. If you add a domain or a module, update its `README.md` in the same
pull request — the staleness in `DOCUMENTATION_IMPLEMENTATION_PLAN.md` §3.1
happened exactly because this didn't happen when Logistics and Project
Management were added.
