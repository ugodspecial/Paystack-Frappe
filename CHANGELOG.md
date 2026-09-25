# Changelog

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
