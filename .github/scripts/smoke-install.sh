#!/usr/bin/env bash
# Smoke-test a site where frappe_paystack was installed from scratch, with assets built:
# the app set, built assets, the checkout page of a real payment, webhook
# signature enforcement, desk metadata, a report, and uninstall/reinstall.
#   smoke-install.sh <bench-dir> <site>
set -Eeuo pipefail
BENCH_DIR="$1"; SITE="$2"
SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
BASE="http://127.0.0.1:8000"
cd "${BENCH_DIR}"

fail() { echo "::error::$*"; exit 1; }

expect() {  # expect <method> <path> <status> [text the body must contain] [extra curl args...]
  local method="$1" path="$2" status="$3" text="${4:-}"; shift 4 || shift $#
  local code
  code="$(curl -s -o /tmp/smoke-body -w '%{http_code}' -X "${method}" "$@" "${BASE}${path}")"
  [ "${code}" = "${status}" ] || { head -c 600 /tmp/smoke-body; fail "${method} ${path}: HTTP ${code}, expected ${status}"; }
  if [ -n "${text}" ] && ! grep -q -- "${text}" /tmp/smoke-body; then
    head -c 600 /tmp/smoke-body; fail "${method} ${path}: response does not contain '${text}'"
  fi
  echo "ok ${method} ${path} -> ${code}"
}

echo "--- installed apps (payments came in through required_apps)"
bash "${SCRIPTS}/assert-apps.sh" "${BENCH_DIR}" "${SITE}" frappe payments frappe_paystack

echo "--- built assets"
python3 - "${BENCH_DIR}" <<'EOF'
import json, os, sys
bench = sys.argv[1]
assets = json.load(open(os.path.join(bench, "sites", "assets", "assets.json")))
missing = []
for bundle in ("paystack_actions.bundle.js", "paystack_pos.bundle.js", "paystack_cart_guard.bundle.js",
               "paystack_reconciliation.bundle.css"):
    path = assets.get(bundle)
    if not path or not os.path.exists(os.path.join(bench, "sites", path.lstrip("/"))):
        missing.append(f"{bundle} -> {path}")
if missing:
    sys.exit("missing built bundles: " + ", ".join(missing))
print("bundles:", {key: value for key, value in assets.items() if "paystack" in key})
EOF

echo "--- create an account and a real payment (before serving: the first encrypted secret creates the site's key)"
SMOKE="$(cd sites && ../env/bin/python "${SCRIPTS}/smoke_site.py" "${SITE}" 2>/dev/null | tail -1)"
SESSION="$(echo "${SMOKE}" | python3 -c "import json,sys;print(json.load(sys.stdin)['session'])")"
SECRET="$(echo "${SMOKE}" | python3 -c "import json,sys;print(json.load(sys.stdin)['secret'])")"

echo "--- serve the site"
bench --site "${SITE}" serve --port 8000 --noreload > /tmp/smoke-serve.log 2>&1 &
SERVER=$!
trap 'kill ${SERVER} 2>/dev/null || true' EXIT
for _ in $(seq 1 60); do
  curl -s -o /dev/null "${BASE}/api/method/ping" && break
  sleep 2
done

expect GET "/api/method/ping" 200 "pong"
expect GET "/assets/frappe_paystack/js/paystack_checkout.js" 200 "resumeTransaction"
expect GET "/assets/frappe_paystack/css/paystack_checkout.css" 200 "ps-checkout"
ACTIONS="$(python3 -c "import json;print(json.load(open('sites/assets/assets.json'))['paystack_actions.bundle.js'])")"
expect GET "${ACTIONS}" 200 "frappe_paystack.actions"

echo "--- the payment's checkout page"
expect GET "/paystack-checkout/${SESSION}" 200 "js.paystack.co/v2/inline.js"
grep -q "paystack_checkout.js" /tmp/smoke-body || fail "checkout page does not load paystack_checkout.js"
grep -q "ps-pay" /tmp/smoke-body || fail "checkout page has no Pay button"
expect GET "/paystack-checkout/does-not-exist" 200 "Payment link not found"
expect GET "/api/method/frappe_paystack.api.get_payment_status?reference=${SESSION}" 200 "is_payable"

echo "--- webhook signature enforcement"
BODY='{"event":"transfer.success","data":{"id":1}}'
expect POST "/api/method/frappe_paystack.api.paystack_webhook" 403 "" \
  -H "Content-Type: application/json" -H "x-paystack-signature: forged" --data "${BODY}"
SIGNATURE="$(python3 -c "import hmac,hashlib,sys;print(hmac.new(sys.argv[1].encode(),sys.argv[2].encode(),hashlib.sha512).hexdigest())" "${SECRET}" "${BODY}")"
expect POST "/api/method/frappe_paystack.api.paystack_webhook" 200 "" \
  -H "Content-Type: application/json" -H "x-paystack-signature: ${SIGNATURE}" --data "${BODY}"

echo "--- desk"
curl -s -c /tmp/smoke-cookies -o /dev/null -X POST "${BASE}/api/method/login" -d usr=Administrator -d pwd=admin
expect GET "/api/method/frappe.desk.form.load.getdoctype?doctype=Paystack+Payment+Log" 200 "Verify with Paystack" -b /tmp/smoke-cookies
expect GET "/api/method/frappe.desk.form.load.getdoctype?doctype=Paystack+Gateway+Setting" 200 "Test Connection" -b /tmp/smoke-cookies
expect GET "/app/paystack-gateway-setting" 200 "" -b /tmp/smoke-cookies
TODAY="$(date +%F)"
expect GET "/api/method/frappe.desk.query_report.run?report_name=Paystack+Activity&filters=%7B%22from_date%22%3A%22${TODAY}%22%2C%22to_date%22%3A%22${TODAY}%22%7D" 200 "Payment" -b /tmp/smoke-cookies

kill "${SERVER}" 2>/dev/null || true
trap - EXIT

echo "--- uninstall and reinstall"
bench --site "${SITE}" uninstall-app frappe_paystack --yes --no-backup
bash "${SCRIPTS}/assert-apps.sh" "${BENCH_DIR}" "${SITE}" frappe payments
bench --site "${SITE}" install-app frappe_paystack
bash "${SCRIPTS}/assert-apps.sh" "${BENCH_DIR}" "${SITE}" frappe payments frappe_paystack
echo "smoke test passed"
