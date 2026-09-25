#!/usr/bin/env bash
# Run the frappe_paystack suite and fail on any failed/erroring test.
#
# bench run-tests can exit 0 when tests fail, so the summaries decide the result.
# Frappe version-16+ runs each test category (integration, unit, unspecified)
# separately, each with its own "Ran N tests" and OK/FAILED lines: every one of
# them is checked, and the counts are added up.
#   run-tests.sh <bench-dir> <site>
set -Eeuo pipefail
BENCH_DIR="$1"; SITE="$2"
LOG="$(mktemp)"
trap 'rm -f "${LOG}"' EXIT
cd "${BENCH_DIR}"
set +e
bench --site "${SITE}" run-tests --app frappe_paystack 2>&1 | tee "${LOG}"
set -e

RAN_LINES="$(grep -E '^Ran [0-9]+ test' "${LOG}" || true)"
if [ -z "${RAN_LINES}" ]; then
  echo "::error::No test summary found; the suite did not run to completion."
  exit 1
fi
TOTAL="$(echo "${RAN_LINES}" | awk '{total += $2} END {print total + 0}')"
RESULTS="$(grep -E '^(OK|FAILED)' "${LOG}" || true)"
SUMMARY="$(echo "${RESULTS}" | tr '\n' ' ')"
echo "Categories: $(echo "${RAN_LINES}" | wc -l); tests: ${TOTAL}; results: ${SUMMARY}"
# Readable through the check-run annotations API, like the failure logs.
echo "::notice title=frappe_paystack tests::Ran ${TOTAL} tests in $(echo "${RAN_LINES}" | wc -l) categories: ${SUMMARY}"

if [ "${TOTAL}" -eq 0 ]; then
  echo "::error::The suite collected 0 tests."
  exit 1
fi
if [ "$(echo "${RESULTS}" | grep -c .)" -lt "$(echo "${RAN_LINES}" | grep -c .)" ] || echo "${RESULTS}" | grep -q '^FAILED'; then
  grep -E '^(FAIL|ERROR):' "${LOG}" || true
  echo "::error::Tests failed: ${SUMMARY:-no result line}"
  exit 1
fi
