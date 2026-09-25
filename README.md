# Frappe Paystack

A [Paystack](https://paystack.com) payment gateway for **[Frappe Payments](https://github.com/frappe/payments)**.
Any Frappe app that takes payments through the Payments app can use Paystack. ERPNext
is **not** required. When ERPNext is installed, an adapter adds Payment Requests,
Payment Entries, POS, Dunning, subscriptions, refunds with reversal entries,
settlement journals and the customer portal.

```text
Your app (LMS, Education, Web Forms, ERPNext, ...) ──▶ Frappe Payments ──▶ frappe_paystack ──▶ Paystack
                                        ▲                                      │
                                        └──── on_payment_authorized ◀──────────┘
```

![Checkout page](img/payment_page.png)

## Compatibility

| | Supported |
|---|---|
| Frappe | version-15, version-16 and develop, from one codebase |
| Payments | the matching `payments` branch |
| Python | 3.10+ (Frappe version-16 and develop need 3.14) |
| ERPNext | optional; version-15 and version-16 are tested |
| LMS, Education, Webshop | optional; tested in CI |
| Currencies | NGN, GHS, ZAR, KES, USD, XOF ([Paystack's list](https://paystack.com/docs/api/#supported-currency)) |

`hooks.required_apps` is `["payments"]`. Nothing outside
`frappe_paystack/integrations/erpnext` imports ERPNext, and CI enforces this.

## Install (no ERPNext needed)

```bash
bench get-app payments https://github.com/frappe/payments --branch version-15   # or version-16 / develop
bench get-app frappe_paystack https://github.com/ugodspecial/Paystack-Frappe
bench --site your.site install-app frappe_paystack   # also installs payments (required_apps)
bench --site your.site list-apps                     # frappe, payments, frappe_paystack
```

`bench get-app --resolve-deps frappe_paystack <url>` can fetch Payments for you, because
`required_apps` names it as `frappe/payments`. Pass the Payments branch that matches your
Frappe version if you fetch it yourself. `bench get-app` builds the app's assets. If you
skipped that step, run `bench build --app frappe_paystack`.

On a site that already has ERPNext, or when ERPNext is installed later, the ERPNext
adapter activates itself. See [ERPNext features](#erpnext-features).

## Configure

Open **Paystack Gateway Setting** and create one record per Paystack account.

| Field | Meaning |
|---|---|
| Account Name, Enabled, Test Mode | Test mode requires `sk_test_`/`pk_test_` keys and refuses live keys, and the other way round. |
| Public Key, Secret Key | From the Paystack dashboard, under *Settings → API Keys & Webhooks*. |
| Webhook Secret (optional) | Paystack signs webhooks with the Secret Key. Set this only if you verify with a different secret. |
| Account Currency, Additional Currencies | What this account can charge (for example NGN, plus USD if Paystack enabled it). |
| Checkout Mode | **Inline** opens the Paystack popup on this site. **Hosted** redirects to Paystack. Both are initialised on the server. |
| Payment Link Validity (Hours) | 0 means links never expire. A payment that reaches Paystack after expiry still settles. |
| Allowed Webhook IPs (optional) | An allowlist of IPs or CIDR ranges. |
| Save Reusable Cards | Stores Paystack's reusable card authorization after a card payment (opt-in; get the payer's consent). |
| Register as its own Payment Gateway | Also exposes the account as the Payment Gateway `Paystack-<Account Name>`, so apps can pick it by name. |

The first enabled account is available as the Payment Gateway **Paystack**. The form's
**Test Connection** button checks the secret key with Paystack and shows the webhook
URL to paste into the Paystack dashboard:

```
https://your.site/api/method/frappe_paystack.api.paystack_webhook
```

## Using Paystack from any app

frappe_paystack implements the Payments gateway contract, so an app that works with
the Payments app works with Paystack without any Paystack-specific code:

```python
from payments.utils import get_payment_gateway_controller

controller = get_payment_gateway_controller("Paystack")
url = controller.get_payment_url(
    amount=5000, currency="NGN",
    title="Course fee", description="Frappe for Beginners",
    reference_doctype="My Enrolment", reference_docname=enrolment.name,
    payer_email=frappe.session.user, payer_name="Ada",
    redirect_to="/courses/frappe-for-beginners",
    my_key="anything",            # extra kwargs are handed back to you
)
# send the browser to `url` (/paystack-checkout/<payment>)
```

When the payment is verified, your document is notified, exactly once:

```python
class MyEnrolment(Document):
    def on_payment_authorized(self, status):          # status == "Completed"
        data = frappe.flags.data                      # your kwargs, plus the fields below
        # data.order_id, data.payment_gateway, data.payment_session,
        # data.paystack_reference, data.paystack_transaction_id,
        # data.amount_paid, data.currency_paid
        ...
        return "/my/receipt"                          # optional redirect for the payer

    def on_payment_failed(self, message):             # optional
        ...
```

Handlers registered through `doc_events` for `on_payment_authorized` fire as well. If
you return nothing, the payer goes to Payments' `/payment-success` page, which then
follows your `redirect_to`.

Consumers that already work this way:

* **LMS**: set *LMS Settings → Payment Gateway* to `Paystack`. Courses, batches and
  certificates are paid through Paystack, and the learner is enrolled.
* **Payments Web Forms** ("Accept Payment"): anonymous visitors are asked for an email
  and pay. On Frappe version-15, Payments attaches the Web Form payment method with
  `extend_doctype_class`, which only Frappe version-16 and later provide.
* **Education** (Student Applicant application fees), and **ERPNext** Payment Requests
  through the adapter.

For Paystack-specific features, a server-side API is available in
`frappe_paystack.core.api`: `create_session`, `get_checkout_url`, `verify_payment`,
`refund_payment` and `charge_saved_authorization`.

## Payment lifecycle

Each `get_payment_url` call creates a **Paystack Payment Log**, which is the payment
session. Its random name is the capability behind the public checkout link.

```
Pending ──▶ Paid ──▶ Partially Refunded ──▶ Refunded
   │  ▲ retry (new reference <session>-2, -3, ...)
   ├──┴─ Failed / Cancelled
   ├──── Expired      (a later capture still settles)
   └──── Needs Attention   (amount/currency mismatch, duplicate capture, reversal)
```

* **Server-initialised checkout.** The browser never sets the amount, currency or
  reference. Inline mode calls `resumeTransaction(access_code)`; hosted mode redirects.
* **Verify before trust.** The browser return, the popup, the webhook, the scheduler
  sweep and the desk **Verify with Paystack** button all end in one locked transition.
  That transition checks the reference, the account, the status, the amount in
  subunits and the currency.
* **Webhooks** are authenticated with HMAC-SHA512 of the raw body, recorded and
  acknowledged at once, then processed in the background. Redelivered events are
  deduplicated. Events whose processing failed are processed again on redelivery, and
  events left queued are re-driven.
* **Consumer notification** is tracked separately (*Notification Status*). It happens
  once, and a consumer error is rolled back, recorded and retried with back-off. After
  8 attempts it becomes *Needs Attention*; resolve it with **Retry Notification** or
  **Mark Notification Resolved**. The payment itself stays *Paid*.
* **Scheduler sweeps** run every 10 minutes: they verify checkouts that never reported
  back, retry notifications, and re-drive webhooks. Refunds are synced hourly;
  reconciliation and the settlement poll run daily.

### Who the application's callback runs as

The callback always runs as the user who started the payment (*Run As User* on the
payment), whichever path completes it:

* The payer's browser returns: it runs as the payer.
* The Paystack webhook (a Guest request): it runs as the payer, so LMS enrolls the
  learner, not Guest.
* An administrator clicks **Verify with Paystack** or **Retry Notification** in the
  desk: it runs as the payer. The administrator is recorded as *Completed By*, but is
  never enrolled or credited.
* An administrator impersonating a learner (Frappe's *Impersonate*) starts a payment:
  it runs as the learner, and *Impersonated By* records the administrator.
* An administrator enrolls the learner by hand before the payment completes: LMS
  course enrolment is idempotent, so the payment is simply recorded. LMS batch
  enrolment refuses duplicates. The payment stays *Paid*, LMS's partial writes are
  rolled back, and the notification is flagged for the administrator to resolve.
* Server code can start a payment on behalf of another user with
  `core.api.create_session(..., payer_user=...)`. This is allowed only for System
  Managers, and never with Administrator as the target.
* Anonymous payers run as Guest. The email a payer types never chooses the user.

The ERPNext Payment Request adapter books accounting as Administrator, as the 15.x
releases did.

## Refunds, saved cards, payouts, reconciliation

* **Refunds** can be full or partial, from the payment form, `initiate_refund_from_log`,
  or the controller's `refund_payment` (Razorpay-style names). Pending refunds hold
  part of the refundable balance. A refund completes when Paystack reports it
  *processed* (by webhook or the hourly poll). Refunds started on the Paystack
  dashboard are recorded too.
* **Saved cards** are stored per payer (a party such as an ERPNext Customer, or the
  signed-in user) when the account opts in. They are charged through the same state
  machine.
* **Payouts**: each Paystack settlement is recorded with its gross, fees, deductions and
  net, and the captures it paid out are linked with their fees.
* **Reconciliation** compares each payment with Paystack's record. It has a desk page,
  a daily job, and a manual override.

## ERPNext features

These activate automatically when ERPNext is installed.

* **Sales Invoice / Sales Order**: payment links, QR codes on the print format, and
  links sent by email. Payment Requests are created and settled with `set_as_paid()`.
* **Payment Requests** (including Webshop checkout and Education Fees). Webshop's own
  `on_payment_authorized` runs first; the request is settled only if it is still
  unpaid, so nothing is booked twice.
* **Direct links** for invoices and Dunning book a **Payment Entry** into the suspense
  account, with a back-off retry and a **Book Payment Entry** desk action.
* **POS**: payment links by email, and the Phone channel shown on the till.
* **Subscriptions**: invoices raised by an ERPNext Subscription are charged to the
  customer's saved card, daily, when enabled.
* **Credit notes** refund through Paystack automatically, if enabled. Processed refunds
  book a **reversal Payment Entry**.
* **Payouts** post a **Journal Entry**: net to the bank, fees to the fee account, gross
  out of suspense.
* **Company routing**: each company can have its own Paystack account.
* **Customer portal** at `/my-payments`, receipts, and the pay-button state on order
  and invoice pages.

The ERPNext settings keep their 15.x field names, now as Custom Fields, on the gateway
setting: Company, Suspense Account, Mode of Payment, Settlement Bank Account, Paystack
Fee Account, Auto Refund on Credit Note and Auto Charge Subscriptions. Payments also
carry Company, Payment Request, Payment Entry and a **Booking Status**
(Pending/Booked/Needs Attention/Not Required).

## Extension points for app developers

Declare these in your app's `hooks.py`:

```python
# Behaviour beyond the v1 contract for one of your DocTypes
paystack_reference_adapters = {"My DocType": "my_app.paystack.MyAdapter"}  # subclass core.adapters.ReferenceAdapter

# Pick the Paystack account for a document: fn(reference_doctype, reference_docname, default_setting) -> setting | None
paystack_account_resolvers = ["my_app.paystack.resolve_account"]

# Gateway events: fn(doc), after the fact, idempotent
paystack_session_created = [...]
paystack_payment_succeeded = [...]
paystack_payment_failed = [...]
paystack_refund_updated = [...]
paystack_authorization_captured = [...]
paystack_settlement_recorded = [...]
paystack_checkout_context = [...]      # fn(session, context): extra rows on the checkout page
paystack_manager_roles = ["My Role"]    # roles that may refund / charge saved cards
```

## Upgrading from 15.x (mymi14s/frappe_paystack)

`bench migrate` runs the 16.1 patches:

* 15.x statuses mixed up what Paystack did with what ERPNext booked. They are now
  split: `Processed` becomes *Paid* with booking *Pending*; `Completed` becomes *Paid*
  with booking *Booked*; `Needs Attention` (booking abandoned) becomes *Paid* with
  booking *Needs Attention*. The old value is kept in `legacy_status`. Captured
  payments are marked *Notified*, so nobody is notified twice.
* ERPNext fields become Custom Fields with the same names. Frappe keeps the columns, so
  no data moves.
* Refund Log `Completed` becomes `Processed`. Saved cards get `party_type/party` from
  `customer`. Settlements get `booking_status`. Records get their Paystack account.
* Unchanged: the webhook URL, `/paystack-checkout/<name>` links, the Payment Gateway
  name `Paystack`, DocType names, and every 15.x whitelisted method path (now shims).
* Behaviour changes:
  * an unsupported currency is refused instead of being charged as NGN;
  * inline checkout is initialised on the server;
  * refunds complete on Paystack's *processed*;
  * the settlement transactions endpoint and payout amounts now follow Paystack's API
    (`/settlement/:id/transactions`; gross is `total_processed`).

CI checks the upgrade: it installs upstream 15.5.0 with ERPNext, seeds its data,
switches to this code, migrates, and verifies every status and value.

## Development

```bash
bench --site test.site set-config allow_tests true
bench --site test.site run-tests --app frappe_paystack     # suites for absent apps skip themselves
npm ci && npm test                                         # Vitest (checkout page, ERPNext desk scripts)
```

* `tests/core`: install, settings, checkout, webhooks, notifications (run-as), refunds,
  saved cards, payouts, reconciliation, migration, legacy paths, and the import boundary.
  It needs only Frappe and Payments, and fakes Paystack at `PaystackClient._send`.
* `tests/lms`, `tests/erpnext`, `tests/education`: these run when those apps are installed.
* `.github/workflows/ci.yml` runs on MariaDB:
  * core and LMS on Frappe version-15, version-16 and develop;
  * ERPNext + Webshop + Education on version-15 and version-16;
  * the 15.5.0 upgrade.

  It also runs static checks (including the ERPNext import boundary) and Vitest.

## Security

* Webhooks: HMAC-SHA512 with `compare_digest`; optional IP allowlist; per-IP and global
  rate limits; alerts on sustained failures.
* The amount, currency and reference always come from the server-side session.
  Captures are verified with Paystack.
* Card authorization codes are stored encrypted and redacted from every Integration
  Request.
* Moving money (refunds, saved-card charges, manual booking) requires System Manager,
  Paystack Manager, or a role from `paystack_manager_roles` (the ERPNext adapter adds
  Accounts Manager).

## License

MIT. See [license.txt](license.txt).

This code is based on
[mymi14s/frappe_paystack](https://github.com/mymi14s/frappe_paystack) (versions 15.5.0
and 16.0.0) by Anthony Emmanuel and contributors. The upstream history is preserved in
this repository.
