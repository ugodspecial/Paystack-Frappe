# Changelog

## 17.0.0 (unreleased)

### Renamed app

- The app is renamed `frappe_paystack` -> `paystack_frappe` (app title and module
  "Frappe Paystack" -> "Paystack Frappe", module folder `paystack_frappe/paystack_frappe`).
  Doctype names, their data, Payment Gateways, custom fields and ERPNext documents are untouched.
- The 15.x compatibility layer is gone: no `frappe_paystack.*` import paths, whitelisted
  endpoints or scheduled jobs remain. The `paystack_frappe.utils` facade, `paystack_frappe.events`,
  `paystack_frappe.setup`, the deprecated checkout endpoints (`validate_payment_link`,
  `start_hosted_checkout`) and the ERPNext shims in `paystack_frappe.api` are dropped; the
  endpoints now live at their real homes (`paystack_frappe.integrations.erpnext.api`,
  `.pos`, `.portal`, `.accounts`, `paystack_frappe.core.reconciliation_api`).
- One in-place patch (`paystack_frappe.patches.v17_0.rename_app`) renames the Module Def,
  repoints Patch Log entries and Scheduled Job Types, so a site keeps its data, patch
  history and scheduler state. CI proves it: the upgrade job installs upstream
  `mymi14s/frappe_paystack` 15.5.0, seeds data, adopts the renamed app and migrates.

### Fixed
- **"Pay with Paystack" no longer drops the port** of the site's address. When a
  proxy forwards `Host` without a port (frappe_docker's nginx, `proxy_set_header
  Host $host`) and sends no `X-Forwarded-Port`, the port is now read from the
  browser's `Origin`/`Referer` header, so ERPNext and LMS - which call
  `get_payment_url()` themselves, with no origin argument - send the payer to
  `http://learn.localhost:8080/paystack-checkout/<id>` instead of
  `http://learn.localhost/paystack-checkout/<id>`. A reported origin is still
  accepted only for one of the site's own hosts.
- **The webhook URL shown on a Paystack Gateway Setting keeps the port too.**
  The desk page now builds it from the address the administrator's browser is
  on (`http://erp.localhost:8080/api/method/...paystack_webhook`), instead of
  whatever the proxy left of `Host`. A pinned `host_name` on another host is
  still shown as-is - that is the public address Paystack must call - and an
  address on your own machine now says so, with the tunnel hint.
- Payments made with Paystack's **Pass fees automatically** (the customer pays the
  transaction fee on top of the listed amount, the merchant nets the listed amount)
  are marked **Paid** instead of *Needs Attention*: the verifier accepts a capture
  whose net is the payment's amount, Payment Requests settle at the net amount, and
  direct-link Payment Entries book the document's own amount.
- New desk action **Accept as Paid** on a payment under review (money managers):
  accept a capture checked against the Paystack dashboard, mark the payment Paid
  and notify the application - the remedy for payments flagged before this fix.

### Upgrading from frappe_paystack

1. `bench get-app paystack_frappe https://github.com/ugodspecial/Paystack-Frappe`
2. `bench --site your.site console`, then
   `from paystack_frappe.rename_app import adopt_installed_site; adopt_installed_site()`
3. `bench --site your.site migrate`
4. (optional) remove the old app: `rm -rf apps/frappe_paystack`

(`bench execute` cannot run step 2: frappe only resolves methods of apps the site
already lists, and the site still lists the former name.) The adopt step swaps the
site's installed-app record and `apps.txt`; `bench migrate` then renames the module
and repoints patch/scheduler rows. All `frappe_paystack.*` HTTP endpoints stop
working in this release - update integrations to the new dotted paths (see the
README table).
## 16.1.0 (unreleased)

### Architecture
- `required_apps = ["frappe/payments"]`: installing frappe_paystack installs Payments, and
  `bench get-app --resolve-deps` can fetch it. Build output (`public/dist`) is no longer committed.
- Split into `frappe_paystack.core` (imports only Frappe, Payments and requests) and the optional
  `frappe_paystack.integrations.erpnext` adapter. `required_apps = ["payments"]`; ERPNext removed from
  `pyproject.toml`. A CI check fails if anything outside the adapter imports ERPNext.
- One codebase for Frappe version-15, version-16 and develop (Python >= 3.10; 3.14 for v16/develop).
- Reference DocType adapter registry (`paystack_reference_adapters`), account resolvers
  (`paystack_account_resolvers`) and broadcast hooks (`paystack_payment_succeeded`, `paystack_payment_failed`,
  `paystack_refund_updated`, `paystack_authorization_captured`, `paystack_settlement_recorded`,
  `paystack_session_created`, `paystack_checkout_context`, `paystack_manager_roles`).

### Payments
- Paystack Payment Log is a generic payment session for any reference DocType: payer, request data, run-as
  user, attempts (`<session>`, `<session>-2`, ...), expiry, notification state and audit fields.
- Server-initialised checkout (inline `resumeTransaction` / hosted), verification of reference, account,
  status, amount (subunits) and currency before any transition; one row-locked transition function.
- Consumer notification per the Payments v1 contract (`on_payment_authorized("Completed")`,
  `frappe.flags.data` with the consumer's kwargs and `order_id`), plus optional `on_payment_failed`.
  Exactly once, run as the initiating user (decision D5), savepoint-isolated, retried with back-off.
- Webhooks recorded as Integration Requests, deduplicated per event, processed in the background. An
  account whose secret cannot be decrypted (e.g. a site restored without its key) is skipped, not fatal.
- Currency rules: NGN, GHS, ZAR, KES, USD, XOF; minimum amounts; precision; no NGN fallback.
- Generic refunds (pending/processing/processed/failed), saved cards per payer, settlement facts and fee
  linkage, gateway-level reconciliation, scheduler sweeps.
- Multiple Paystack accounts: "Paystack" stays the default gateway; accounts may register `Paystack-<name>`.

### Checkout URLs
- Payment links, the Paystack `callback_url` and the webhook URL are built from the address the browser
  is on (`core.urls`): scheme, host and port. A dev site served on :8000 no longer hands out a link on
  port 80 ("... refused to connect", a blank page instead of the checkout), a site behind TLS keeps
  https, and nothing about the address has to be configured in the app. The checkout page, the desk
  actions, the POS till and the portal report their own `window.location.origin`; it is accepted only
  for this site's hosts, and a configured `host_name` stays canonical for every other host.
- Redirects after a payment are relative (`/payment-success?...`, `/payment-failed`), the way the gateways
  shipped with Payments do it, so the payer keeps the origin - and the port - they came from.
- The inline popup falls back to the hosted page of the same server-initialised transaction when it
  cannot open.

### ERPNext adapter
- All 15.x ERPNext features preserved; ERPNext-only settings are Custom Fields with the 15.x names.
- Booking Status separate from payment status; Webshop's `set_as_paid()` is never repeated.
- Refund reversal entries are booked when Paystack reports the refund processed.
- Fixes: settlement transactions endpoint (`/settlement/:id/transactions`), payout gross amount
  (`total_processed`), print formats now attached to their DocTypes (`doc_type`).

### Migration
- 16.1 patches map 15.x statuses (keeping `legacy_status`), move saved cards to `party_type/party`, map
  settlement and refund statuses, back-fill the Paystack account, and re-register Payment Gateways.

### Tooling
- New test suites (core without ERPNext, LMS end to end, ERPNext adapter, Education, upgrade from 15.5.0),
  a MariaDB CI matrix, and Vitest for the checkout page and ERPNext desk scripts.
- CI fresh-install job: assets built, only frappe_paystack installed, the running site smoke-tested over
  HTTP, uninstall/reinstall, and ERPNext installed afterwards and removed again.
- Removed the 15.x ERPNext-bound test suite, Cypress specs and docker job mirrors (they targeted the 15.x UI,
  statuses and CI jobs).

## 16.0.0 / 15.5.0
Upstream releases of [mymi14s/frappe_paystack](https://github.com/mymi14s/frappe_paystack), imported with history.
