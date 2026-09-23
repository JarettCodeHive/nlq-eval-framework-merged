#!/usr/bin/env bash
# Score every domain that has authored Q&A pairs — one `main.py judge` run per
# domain, because calibration markers, configs, caches and output dirs are all
# keyed on domain.
#
#   ./scripts/score_domains.sh                     # every domain with pairs
#   ./scripts/score_domains.sh crm sales           # just these
#   PROFILE=dev ./scripts/score_domains.sh crm     # the disposable tmp package
#   DRY_RUN=1 ./scripts/score_domains.sh           # show the plan, spend nothing
#   ALLOW_UNCALIBRATED=1 ./scripts/score_domains.sh sales
#
# Domains run SEQUENTIALLY on purpose. Every domain questions the same platform
# org, so running them concurrently multiplies load against one shared rate
# limit instead of finishing sooner. Tune PULSE_CONC/JUDGE_CONC instead, and
# keep them in step — see the note in scripts/rerun_crm_failures.sh.
#
# Exits non-zero if any domain did not come back clean, so CI can gate on it.

# Deliberately no `-e`: one domain failing must not abort the domains after it.
set -uo pipefail

cd "$(dirname "$0")/.."

PULSE_CONC="${PULSE_CONC:-4}"
JUDGE_CONC="${JUDGE_CONC:-4}"
PULSE_SOURCE="${PULSE_SOURCE:-live}"
PROFILE="${PROFILE:-full}"
DRY_RUN="${DRY_RUN:-}"

if [ "$#" -gt 0 ]; then
  DOMAINS=("$@")
else
  DOMAINS=(crm sales finance project_management logistics)
fi

# Ask the resolver `judge` itself uses, rather than re-deriving the release
# layout in bash — two implementations of "which package holds the pairs" is
# how a nightly ends up scoring last month's release.
resolve_csv() {
  python -c "
import sys
from judge.resolve import ResolutionError, judge_input_csv
try:
    print(judge_input_csv(sys.argv[1], sys.argv[2], build_if_missing=False))
except (ResolutionError, SystemExit) as exc:
    print(exc, file=sys.stderr)
    raise SystemExit(1)
" "$1" "$PROFILE" 2>/dev/null
}

describe_exit() {
  case "$1" in
    0) printf 'OK' ;;
    1) printf 'completed WITH JUDGE ERRORS' ;;
    2) printf 'REFUSED (calibration gate, config or input)' ;;
    4) printf 'scored, REGRESSION flagged' ;;
    *) printf 'exit %s' "$1" ;;
  esac
}

echo "==> preflight (credentials are shared across domains, so once is enough)"
if [ -z "$DRY_RUN" ]; then
  if ! python main.py check-auth; then
    echo "!! preflight failed — fix credentials before scoring" >&2
    exit 2
  fi
else
  echo "   (skipped: DRY_RUN)"
fi

RESULTS=()
FAILED=0

for domain in "${DOMAINS[@]}"; do
  echo
  echo "=============================================================="
  echo "==> ${domain}"
  echo "=============================================================="

  # `judge` resolves this itself; resolving it here too is only so a domain with
  # no pairs reads as SKIPPED in the summary instead of as a failure.
  if ! CSV="$(resolve_csv "$domain")"; then
    echo "   no ${PROFILE}-profile Q&A pairs for ${domain} — skipping"
    RESULTS+=("${domain}|SKIPPED|no Q&A pairs")
    continue
  fi
  # csv module, not `wc -l`: reference_sql holds newlines inside quoted fields,
  # so a line count overreports a 160-pair file as ~4700.
  ROWS="$(python -c "
import csv, sys
print(sum(1 for _ in csv.DictReader(open(sys.argv[1], newline=''))))
" "$CSV" 2>/dev/null || echo '?')"
  echo "   input: ${CSV} (${ROWS} pairs)"

  # Surface the gate before spending anything, so a refusal is legible rather
  # than an exit 2 forty lines into the output.
  CAL="$(python -c "
from judge.calibration import check_calibrated
print('CALIBRATED' if check_calibrated('${domain}').calibrated else 'UNCALIBRATED')
" 2>/dev/null || echo UNKNOWN)"
  echo "   calibration: ${CAL}"

  # --domain and --profile are the whole interface; the input CSV, the dataset
  # version tag and the sql table directory all follow from them.
  ARGS=(judge --domain "$domain" --profile "$PROFILE"
        --judge llm --pulse "$PULSE_SOURCE"
        --pulse-concurrency "$PULSE_CONC" --concurrency "$JUDGE_CONC")
  [ -n "${ALLOW_UNCALIBRATED:-}" ] && ARGS+=(--allow-uncalibrated)

  if [ -n "$DRY_RUN" ]; then
    echo "   would run: python main.py ${ARGS[*]}"
    RESULTS+=("${domain}|DRY_RUN|${CAL}")
    continue
  fi

  python main.py "${ARGS[@]}"
  code=$?
  echo "   -> $(describe_exit "$code")"
  RESULTS+=("${domain}|${code}|$(describe_exit "$code")")
  [ "$code" -eq 0 ] || FAILED=1
done

echo
echo "=============================================================="
echo "==> summary"
echo "=============================================================="
printf '%-20s %-10s %s\n' "DOMAIN" "STATUS" "DETAIL"
for row in "${RESULTS[@]}"; do
  IFS='|' read -r d s detail <<<"$row"
  printf '%-20s %-10s %s\n' "$d" "$s" "$detail"
done

echo
if [ "$FAILED" -ne 0 ]; then
  echo "!! at least one domain did not come back clean — see above"
  exit 1
fi
echo "all requested domains clean"
