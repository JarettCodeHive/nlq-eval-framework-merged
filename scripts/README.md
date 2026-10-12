# Scripts

Standalone operational tooling that doesn't belong inside any one pipeline
stage. Run from the repository root with the virtual environment active.

- **`check_cross_domain_uniqueness.py`** — enforces Execution Spec §9.4
  rule 9 / §13.2's "CI checks: ... cross-domain question uniqueness hash":
  no `natural_language_question` may appear in more than one domain's
  released Q&A set. Normalizes text (lowercase, strip punctuation/whitespace)
  before hashing so near-identical phrasing doesn't hide a real duplicate.
  Checks every domain's `full`-profile release only. **This is a CI
  dependency, not just a local utility** — `.github/workflows/ci.yml`'s
  `cross-domain-uniqueness` job runs it directly and fails the build on any
  collision.

  ```bash
  python scripts/check_cross_domain_uniqueness.py
  ```

- **`reconstruct_run_from_scorecard.py`** — rebuilds a judge run's
  `results.json` from the scorecard CSVs it produced. Use this when a run's
  evidence (`release/<version>/<domain>/judge/<run_id>/results.json`,
  `prompts_log.jsonl`, `pulse_raw/`) has been lost but its scorecard
  (`release/<version>/<domain>/scorecard/<run_id>/`) survives — it recovers
  enough to re-render scorecards and to feed `main.py score`. Per-question
  judge rationales and raw platform payloads are **not** recoverable this
  way; only what the scorecard's `question_results.csv` drill-down already
  carries is reconstructed.

- **`rerun_crm_failures.sh`** — re-runs only the CRM Q&A pairs whose verdict
  could change after a dataset re-upload, then diffs the outcome against
  the previous run. Run it **after** the target release's dataset has
  actually been (re)loaded into the platform org — running it before the
  upload measures nothing, since the point is distinguishing which failures
  were the dataset and which were the platform.

  ```bash
  ./scripts/rerun_crm_failures.sh
  ```

- **`score_domains.sh`** — runs one `main.py judge` pass per domain that has
  authored Q&A pairs, because calibration markers, configs, caches, and
  output directories are all keyed on domain.

  ```bash
  ./scripts/score_domains.sh                     # every domain with pairs
  ./scripts/score_domains.sh crm sales            # just these
  PROFILE=dev ./scripts/score_domains.sh crm      # the disposable tmp package
  DRY_RUN=1 ./scripts/score_domains.sh            # show the plan, spend nothing
  ALLOW_UNCALIBRATED=1 ./scripts/score_domains.sh sales
  ```

See each script's own header comment for the full flag/environment-variable
surface — this file is the index, not a duplicate of that detail.
