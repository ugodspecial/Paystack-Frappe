#!/usr/bin/env bash
# Replace the installed upstream frappe_paystack with the commit under test and migrate.
#   switch-and-migrate.sh <bench-dir> <site>
set -Eeuxo pipefail
BENCH_DIR="$1"; SITE="$2"
APP_DIR="${BENCH_DIR}/apps/frappe_paystack"

git -C "${GITHUB_WORKSPACE}" checkout -B ci-run
# The bench clone of upstream is shallow and unrelated to this history: replace its files.
rsync -a --delete --exclude '.git' --exclude 'node_modules' "${GITHUB_WORKSPACE}/" "${APP_DIR}/"
find "${APP_DIR}" -name '__pycache__' -type d -prune -exec rm -rf {} +
"${BENCH_DIR}/env/bin/python" -m pip install --quiet -e "${APP_DIR}"
cd "${BENCH_DIR}"
bench --site "${SITE}" migrate
