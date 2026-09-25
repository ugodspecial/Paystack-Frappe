#!/usr/bin/env bash
# Build a bench for one CI environment and install the apps it needs.
#
# Environment:
#   FRAPPE_BRANCH, PAYMENTS_BRANCH   required
#   EXTRA_APPS   space-separated "app@branch" pairs installed before frappe_paystack,
#                e.g. "erpnext@version-15 webshop@version-15 education@version-15.2"
#   PAYSTACK_SOURCE   git URL/branch "url@branch" to install instead of this checkout
#   PYTHON_BIN   interpreter for the bench virtualenv
set -Eeuo pipefail

BENCH_DIR="${BENCH_DIR:-$HOME/frappe-bench}"
SITE="${SITE:-test_site}"

pip install --quiet frappe-bench

bench init --skip-redis-config-generation --skip-assets --frappe-branch "${FRAPPE_BRANCH}" \
  --python "${PYTHON_BIN:-$(command -v python)}" "${BENCH_DIR}"

mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "SET GLOBAL character_set_server = 'utf8mb4'"
mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "SET GLOBAL collation_server = 'utf8mb4_unicode_ci'"

cd "${BENCH_DIR}"
bench get-app --skip-assets payments https://github.com/frappe/payments --branch "${PAYMENTS_BRANCH}"
for spec in ${EXTRA_APPS:-}; do
  app="${spec%@*}"; branch="${spec#*@}"
  bench get-app --skip-assets "${app}" "https://github.com/frappe/${app}" --branch "${branch}"
done

if [ -n "${PAYSTACK_SOURCE:-}" ]; then
  bench get-app --skip-assets frappe_paystack "${PAYSTACK_SOURCE%@*}" --branch "${PAYSTACK_SOURCE#*@}"
else
  # actions/checkout leaves a detached HEAD; a branch names the commit under test.
  git -C "${GITHUB_WORKSPACE}" checkout -B ci-run
  bench get-app --skip-assets frappe_paystack "${GITHUB_WORKSPACE}" --branch ci-run
fi

bench new-site --db-root-password root --admin-password admin "${SITE}"
bench --site "${SITE}" install-app payments
for spec in ${EXTRA_APPS:-}; do
  bench --site "${SITE}" install-app "${spec%@*}"
done
bench --site "${SITE}" install-app frappe_paystack
bench --site "${SITE}" set-config allow_tests true
bench --site "${SITE}" list-apps

# Assets are not built in CI; pages render against an empty bundle map.
mkdir -p sites/assets
[ -f sites/assets/assets.json ] || echo '{}' > sites/assets/assets.json
[ -f sites/assets/assets-rtl.json ] || echo '{}' > sites/assets/assets-rtl.json
