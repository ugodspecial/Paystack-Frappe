#!/usr/bin/env bash
# ERPNext installed on a site that already runs frappe_paystack: the adapter
# must activate itself (after_app_install), the whole suite must pass, and
# uninstalling ERPNext must deactivate it cleanly (before_app_uninstall).
#   erpnext-later.sh <bench-dir> <site> <erpnext-branch>
set -Eeuo pipefail
BENCH_DIR="$1"; SITE="$2"; BRANCH="$3"
SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
cd "${BENCH_DIR}"

bench get-app --skip-assets erpnext https://github.com/frappe/erpnext --branch "${BRANCH}"
# The site has served requests and had apps removed and re-added; start ERPNext's
# install from fresh caches, as after any `bench get-app`.
bench --site "${SITE}" clear-cache
bench --site "${SITE}" install-app erpnext
bash "${SCRIPTS}/assert-apps.sh" "${BENCH_DIR}" "${SITE}" frappe payments frappe_paystack erpnext
(cd sites && ../env/bin/python "${SCRIPTS}/check_adapter.py" "${SITE}" active)

bench --site "${SITE}" set-config allow_tests true
bash "${SCRIPTS}/run-tests.sh" "${BENCH_DIR}" "${SITE}"

bench --site "${SITE}" uninstall-app erpnext --yes --no-backup
bash "${SCRIPTS}/assert-apps.sh" "${BENCH_DIR}" "${SITE}" frappe payments frappe_paystack
(cd sites && ../env/bin/python "${SCRIPTS}/check_adapter.py" "${SITE}" inactive)
echo "ERPNext installed later and removed again: adapter followed"
