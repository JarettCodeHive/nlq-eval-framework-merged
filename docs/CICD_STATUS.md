# CI/CD — One-Pager

*Last updated 2026-10-07 · PR #13*

---

## 1. CI — automatic, on every push/PR to `development` and `main`

```
                              push / pull request
                                      │
                                      ▼
                              ┌───────────────┐
                              │     lint      │   ruff · black · sqlfluff
                              └───────┬───────┘
                                      │ (must pass before anything else runs)
                   ┌──────────────────┼──────────────────┬──────────────────┐
                   ▼                  ▼                  ▼                  ▼
            ┌─────────────┐   ┌──────────────┐   ┌───────────────┐  ┌──────────────┐
            │  qa-pairs   │   │ dataset-gates│   │reproducibility│  │  full-suite  │
            └─────────────┘   └──────────────┘   └───────────────┘  └──────────────┘
                   │                  │                  │                  │
                   ▼                  ▼                  ▼                  ▼
            all 4 jobs must pass  →  PR shows green  →  safe to merge
```

### What each job actually checks

| Job | What runs | What it proves | Gate |
|---|---|---|---|
| **lint** | `ruff check .` · `black --check .` · `sqlfluff lint` | Code style and SQL syntax are clean | 🔒 Hard |
| **qa-pairs** | Builds CRM full dataset + Q&A release, runs 63 tests | Contract shape, ground-truth re-execution, scoring, rephrase groups all correct | 🔒 Hard |
| **dataset-gates** | Builds all 5 domains full, then: **FK integrity gate** · **row-cap assertion** · **cross-domain question uniqueness** | No orphan foreign keys, no table over 250K rows, no question repeated across domains | 🔒 Hard |
| **reproducibility** | Rebuilds every domain **a second time from scratch** and byte-compares it to the first build | The "clean-room re-run = byte-identical output" guarantee (HC-7) is real, not assumed | 🔒 Hard |
| **full-suite** | Full `pytest tests/` — generators, judge, scorecard | No regression anywhere else in the codebase | 🔒 Hard |

**Everything above is a hard gate today.** As of this week, there are no skipped checks, no `continue-on-error`, no `--ignore` flags — a real failure anywhere fails the PR, full stop. (That wasn't true until this PR: 17 tests were previously failing-but-ignored; all fixed at the root cause, not papered over — see PR #13 for exactly what each one was.)

---

## 2. CD — the release flow (`release.yml`), manual only

**This never runs automatically.** It costs real LLM API money and writes to the shared Pulse platform — a human clicks "Run workflow" on purpose, every time.

```
 GitHub Actions tab → "Run workflow" → pick domain + options
                              │
                              ▼
        ┌─────────────────────────────────────────────────┐
        │         fresh, temporary cloud VM (GitHub's)     │
        │                                                   │
        │   1. check-auth        confirm Pulse + judge      │
        │                        credentials work            │
        │                        (fails fast if missing)     │
        │                                                   │
        │   2. build-dataset     generate the domain's       │
        │                        CSVs (deterministic, seed)  │
        │                                                   │
        │   3. qa-build          stage CSVs into DuckDB,     │
        │                        execute reference SQL,      │
        │                        produce the Q&A release     │
        │                                                   │
        │   4. dataset-upload    push CSVs to the real       │
        │                        Pulse platform ─────────────┼──► Pulse's servers
        │                                                   │     (external, their infra)
        │   5. judge             ask Pulse the real          │
        │      (--pulse live)    questions, score the        │
        │                        real answers                │
        │                                                   │
        │   6. score             combine into a              │
        │                        regression scorecard        │
        │                                                   │
        └──────────────────────┬────────────────────────────┘
                                │  VM is destroyed here — everything on
                                │  its disk vanishes UNLESS uploaded first
                                ▼
                  scorecard + judge logs + Pulse raw responses
                  uploaded as a GitHub Actions artifact
                  (downloadable zip, 90-day retention)
```

### Inputs you choose when triggering it

| Input | What it does | Default |
|---|---|---|
| `domain` | Which of the 5 domains to evaluate | *(required, pick one)* |
| `skip_judge` | Stop after step 4 — dry-run build+upload only, **zero LLM/Pulse cost** | off |
| `release` | Produce an *official* baseline-comparison scorecard | off — **must stay off until calibration is done (see below)** |
| `platform_version` | Tag this run for baseline comparison | optional |

### Why nothing here touches git

Dataset generation is **fully deterministic** (fixed seed, no wall-clock, no network calls) — the same code always produces the same bytes. So git only needs to store the *recipe* (generator code + config), never the *output* (CSVs, DuckDB file). Those are regenerated fresh, identically, every single run — that's exactly what the `reproducibility` CI job (above) proves continuously. The only thing that **can't** be regenerated is the judge/scorecard result, because it reflects Pulse's real answer at that moment in time — that's the one thing the workflow explicitly preserves as an artifact.

---

## 3. What's pending before CD can actually run

| # | Item | Blocks | Owner |
|---|---|---|---|
| 1 | `PULSE_BASE_URL` / `PULSE_AUTH_TOKEN` / `PULSE_ORG_ID` as repo Actions secrets | CD fails immediately at `check-auth` without these | You — pull a fresh token via Chrome DevTools (`docs/judge_runbook.md`) |
| 2 | `FLOODGATE_NARRATIVE_CERT` / `FLOODGATE_NARRATIVE_KEY` as repo Actions secrets | Judge step needs server/CI auth — the local Mac OIDC flow doesn't work on a cloud runner | You / Floodgate credential owner |
| 3 | Judge calibration — ≥10 real human-graded anchors per domain | `release`-flagged runs stay blocked without it | You + Platform Owner |
| 4 | Branch protection + CODEOWNERS | Nothing today stops a direct push bypassing review | Repo admin (GitHub setting, not code) |
| 5 | Git LFS vs. artifact-only decision | No scorecard/judge result is permanently versioned today — artifacts expire after 90 days | You / Engagement Lead |
| 6 | A fresh baseline once 1–3 are done | The one existing baseline (CRM 52.84%, Sales 64.42%) predates recent fixes and the 5-domain expansion — it's stale | You |

---

## Bottom line

**CI is fully real** — every check fails loudly if something's actually wrong, nothing is soft-gated. **CD is built and ready** but has never run, because it's waiting on two secrets (items 1–2) that only you or the Floodgate credential owner can provide. Once those exist, the rest of the evaluation loop (calibration, a real baseline, the accuracy gate) can finally start — that's the actual critical path, not more CI/CD engineering.
