# CI/CD Status — One-Pager

*Last updated 2026-10-06. See PR #13 for the work this reflects.*

## What exists today

### CI — runs automatically on every push/PR to `development` and `main`
| Job | What it checks | Gate type |
|---|---|---|
| `lint` | ruff, black, sqlfluff (CRM reference DDL) | **Hard** |
| `qa-pairs` | Builds CRM full dataset + Q&A release, runs `qa_pairs/tests/` (63 tests: contract shape, ground-truth re-execution, scoring, rephrase) | **Hard** |
| `dataset-gates` | Builds all 5 domains full, then **FK integrity gate**, **row-cap assertion**, **cross-domain question uniqueness** — each a named, independently-reported step | **Hard** |
| `reproducibility` | **Clean-room rebuild + byte-compare** per domain (HC-7) — real double-build, not a shortcut | **Hard** |
| `full-suite` | Full `pytest tests/` (generators, judge, scorecard) | **Hard** (was soft/ignored until today — all 17 pre-existing failures fixed at root cause) |

### CD — `release.yml`, manually triggered only (`workflow_dispatch`)
```
build-dataset → qa-build → check-auth → dataset-upload → judge (--pulse live) → score
```
- Never fires on push/PR — it spends real LLM API cost and writes to the real shared Pulse org. A human clicks "Run workflow."
- `skip_judge` input: dry-runs just build+upload, no LLM/Pulse cost.
- `release` input: produces an official baseline-comparison scorecard — **gated on calibration, which hasn't happened for any domain yet, so this must stay unchecked today.**
- Outputs (scorecard CSV+PDF, judge logs, Pulse raw responses) are uploaded as a GitHub Actions artifact, since `release/` is gitignored and nothing in this flow is committed to git anywhere else.

## What's pending — and who it needs

| # | Item | Blocks | Owner |
|---|---|---|---|
| 1 | Add `PULSE_BASE_URL` / `PULSE_AUTH_TOKEN` / `PULSE_ORG_ID` to repo Actions secrets | CD workflow can't run at all — fails fast at `check-auth` | You (pull fresh token via Chrome DevTools, per `docs/judge_runbook.md`) |
| 2 | Add `FLOODGATE_NARRATIVE_CERT` / `FLOODGATE_NARRATIVE_KEY` to repo Actions secrets | Same — judge step needs server/CI auth, not the local Mac OIDC flow | You / whoever owns Floodgate service credentials |
| 3 | Judge calibration — ≥10 real human-graded anchors per domain | `release`-flagged runs (official baseline) stay blocked without it | You + Platform Owner (CRM calibration session) |
| 4 | Branch protection + CODEOWNERS on `development`/`main` | Nothing today stops a direct push bypassing review | Repo admin (GitHub Settings, not code) |
| 5 | Decide on Git LFS vs. accepting artifact-only persistence | No release artifact is versioned in git today — CD's uploaded Actions artifacts are the only record, and they expire (90-day default) | You / Engagement Lead |
| 6 | A fresh, current baseline once 1–3 are done | The one existing baseline (CRM 52.84%, Sales 64.42%) predates recent fixes and the 5-domain pair expansion — it's stale | You |

## How to actually run it, once secrets exist

1. Go to the repo's **Actions** tab → **release** workflow → **Run workflow**.
2. Pick a domain, leave `release` unchecked (until calibration is done), leave `skip_judge` unchecked.
3. Watch the job; download the artifact at the end for the scorecard PDF.
4. Repeat per domain, or extend the workflow to loop all 5 in one dispatch once this is proven out.

## The honest caveat

CI is now fully real — every gate in it, including the expensive reproducibility one, actually fails if something's wrong, and nothing is skipped or soft-gated anymore. CD exists as code but has never been run end-to-end, because items 1–2 above aren't done yet. Everything downstream of "does Pulse say the answer is right" (calibration, a real baseline, the accuracy gate) is still blocked on those two secrets plus a real calibration pass — that's the actual critical path, not more CI/CD engineering.
