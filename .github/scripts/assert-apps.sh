#!/usr/bin/env bash
# Assert which apps are installed on the CI site.
#   assert-apps.sh <bench-dir> <site> <expected apps...>  (exact set, order-insensitive)
set -Eeuo pipefail
BENCH_DIR="$1"; SITE="$2"; shift 2
cd "${BENCH_DIR}"
# list-apps prints blank lines and version columns; keep the first word of each app line.
INSTALLED="$(bench --site "${SITE}" list-apps | awk 'NF {print $1}' | sort | xargs)"
EXPECTED="$(printf '%s\n' "$@" | sort | xargs)"
echo "installed: ${INSTALLED}"
echo "expected:  ${EXPECTED}"
if [ "${INSTALLED}" != "${EXPECTED}" ]; then
  echo "::error::Installed apps (${INSTALLED}) differ from the expected set (${EXPECTED})"
  exit 1
fi
