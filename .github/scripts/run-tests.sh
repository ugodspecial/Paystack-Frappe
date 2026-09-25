#!/usr/bin/env bash
# Run the frappe_paystack suite and fail on any failed/erroring test.
# bench run-tests can exit 0 when tests fail, so the summary decides the result.
#   run-tests.sh <bench-dir> <site>
set -Eeuo pipefail
BENCH_DIR="$1"; SITE="$2"
LOG="$(mktemp)"
trap 'rm -f "${LOG}"' EXIT
cd "${BENCH_DIR}"
set +e
bench --site "${SITE}" run-tests --app frappe_paystack 2>&1 | tee "${LOG}"
set -e
if ! grep -qE '^Ran [0-9]+ test' "${LOG}"; then
  echo "::error::No test summary found; the suite did not run to completion."
  exit 1
fi
grep -E '^Ran [0-9]+ test' "${LOG}" | tail -1
if grep -qE '^Ran 0 tests' "${LOG}"; then
  echo "::error::The suite collected 0 tests."
  exit 1
fi
RESULT="$(grep -E '^(OK|FAILED)' "${LOG}" | tail -1 || true)"
echo "${RESULT}"
if [ -z "${RESULT}" ] || [[ "${RESULT}" == FAILED* ]]; then
  grep -E '^(FAIL|ERROR):' "${LOG}" || true
  echo "::error::Tests failed: ${RESULT:-no result line}"
  exit 1
fi
