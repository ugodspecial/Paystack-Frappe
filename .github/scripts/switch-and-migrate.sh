#!/usr/bin/env bash
# Replace the installed upstream frappe_paystack with the renamed paystack_frappe
# (the commit under test), adopt the site onto the renamed app and migrate.
#
# The order matters: frappe.init imports every app listed in sites/apps.txt, so
# the old app stays on the bench until adopt_installed_site has rewritten that
# list; only then is it uninstalled and its directory removed. The migrate that
# follows runs paystack_frappe.patches.v17_0.rename_app, which moves the module
# and repoints patch and scheduler history in place.
#   switch-and-migrate.sh <bench-dir> <site>
set -Eeuxo pipefail
BENCH_DIR="$1"; SITE="$2"
OLD_APP_DIR="${BENCH_DIR}/apps/frappe_paystack"
NEW_APP_DIR="${BENCH_DIR}/apps/paystack_frappe"

git -C "${GITHUB_WORKSPACE}" checkout -B ci-run
# The bench clone of upstream is shallow and unrelated to this history: replace its files.
rm -rf "${NEW_APP_DIR}"
rsync -a --delete --exclude '.git' --exclude 'node_modules' "${GITHUB_WORKSPACE}/" "${NEW_APP_DIR}/"
find "${NEW_APP_DIR}" -name '__pycache__' -type d -prune -exec rm -rf {} +

# Both apps importable while the site still names the old one.
"${BENCH_DIR}/env/bin/python" -m pip install --quiet -e "${NEW_APP_DIR}"

# The documented upgrade flow, minus the interactive console: adopt the
# renamed app (installed apps + apps.txt), then migrate.
(cd "${BENCH_DIR}/sites" && ../env/bin/python "${GITHUB_WORKSPACE}/.github/scripts/adopt_app.py" "${SITE}")

# The old app is off the bench's app list now; take it off the bench.
"${BENCH_DIR}/env/bin/python" -m pip uninstall -y frappe_paystack
rm -rf "${OLD_APP_DIR}"

cd "${BENCH_DIR}"
bench --site "${SITE}" migrate
