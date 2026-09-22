#!/usr/bin/env bash
# Re-run only the CRM pairs whose verdict could change after the dataset
# re-upload, then diff the outcome against the previous run.
#
#   ./scripts/rerun_crm_failures.sh
#
# Run it AFTER release/crm/dataset-v1.0.0 is loaded into org 4104. Re-running
# before the upload measures nothing — the whole point is to see which failures
# were the dataset and which were the platform.
#
# 55 of the 99 failures, ~7 minutes. The other 44 are skipped because their
# failure is a question-side specification ambiguity (ground truth and platform
# computed different aggregates, or joined through different paths) that no data
# change can fix. Pass --include-ambiguous to build_rerun_input.py to override.
#
# No token needed: PULSE_REFRESH_* is configured, so an expired credential is
# re-minted at the first request.

set -euo pipefail

BASE_RUN="${1:-20260921T124500Z}"
DOMAIN="${DOMAIN:-crm}"
# Platform and judge have separate limits because they are separate resources —
# see _collect_and_score. Raising only one just moves the bottleneck: at platform
# concurrency 8 an answer lands every ~4s, and the judge needs roughly 4 slots to
# keep up with that (~12s per verdict). Keep them in step.
PULSE_CONC="${PULSE_CONC:-4}"
JUDGE_CONC="${JUDGE_CONC:-4}"
cd "$(dirname "$0")/.."

RERUN_CSV="release/${DOMAIN}/qa-pairs-v0.3.0/${DOMAIN}_rerun_${BASE_RUN}.csv"

echo "==> preflight"
python main.py check-auth --domain "$DOMAIN"

echo
echo "==> selecting the pairs worth asking again (from run ${BASE_RUN})"
python judge/build_rerun_input.py --run "$BASE_RUN" --domain "$DOMAIN"

echo
echo "==> scoring (platform concurrency ${PULSE_CONC}, judge concurrency ${JUDGE_CONC})"
python main.py score \
  --judge llm \
  --pulse live \
  --domain "$DOMAIN" \
  --input-csv "$RERUN_CSV" \
  --pulse-concurrency "$PULSE_CONC" \
  --concurrency "$JUDGE_CONC" \
  --allow-uncalibrated

echo
echo "==> comparing against ${BASE_RUN}"
NEW_RUN="$(ls -t "release/${DOMAIN}/eval-runs" | head -1)"
python judge/compare_runs.py --base "$BASE_RUN" --new "$NEW_RUN" --domain "$DOMAIN"

echo
echo "==> merged scorecard for the whole domain"
python -m judge.merge_runs --base "$BASE_RUN" --new "$NEW_RUN" --domain "$DOMAIN"

echo
echo "artefacts:"
echo "  release/${DOMAIN}/scorecards/${NEW_RUN}/"
echo "  release/${DOMAIN}/eval-runs/${NEW_RUN}/"
