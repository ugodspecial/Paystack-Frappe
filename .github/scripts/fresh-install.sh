#!/usr/bin/env bash
# Install frappe_paystack the way a user does on a new site: assets are built,
# and only frappe_paystack is installed explicitly (Frappe installs Payments
# because hooks.required_apps names it).
#
# Environment: FRAPPE_BRANCH, PAYMENTS_BRANCH, PYTHON_BIN
set -Eeuo pipefail
BENCH_DIR="${BENCH_DIR:-$HOME/frappe-bench}"
SITE="${SITE:-test_site}"

pip install --quiet frappe-bench
bench init --skip-redis-config-generation --frappe-branch "${FRAPPE_BRANCH}" \
  --python "${PYTHON_BIN:-$(command -v python)}" "${BENCH_DIR}"
mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "SET GLOBAL character_set_server = 'utf8mb4'"
mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "SET GLOBAL collation_server = 'utf8mb4_unicode_ci'"

cd "${BENCH_DIR}"
bench get-app payments https://github.com/frappe/payments --branch "${PAYMENTS_BRANCH}"
git -C "${GITHUB_WORKSPACE}" checkout -B ci-run
bench get-app frappe_paystack "${GITHUB_WORKSPACE}" --branch ci-run

bench new-site --db-root-password root --admin-password admin "${SITE}"
bench --site "${SITE}" install-app frappe_paystack
bench --site "${SITE}" migrate
