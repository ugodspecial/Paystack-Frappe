app_name = "frappe_paystack"
app_title = "Frappe Paystack"
app_publisher = "Anthony Emmanuel"
app_description = "Paystack payment gateway for Frappe Payments (ERPNext optional)"
app_email = "hackacehuawei@gmail.com"
app_license = "mit"

# Only Payments is required ("org/repo" so that `bench get-app --resolve-deps`
# can fetch it). ERPNext, LMS, Education and Webshop are optional
# consumers; ERPNext features are provided by frappe_paystack.integrations.erpnext,
# which activates itself when ERPNext is installed.
required_apps = ["frappe/payments"]

# ---------------------------------------------------------------------------
# Install / migrate lifecycle
# ---------------------------------------------------------------------------
after_install = "frappe_paystack.install.after_install"
after_migrate = "frappe_paystack.install.after_migrate"
before_uninstall = "frappe_paystack.install.before_uninstall"
# Another app installed or removed later (ERPNext activates/deactivates the adapter).
after_app_install = "frappe_paystack.install.after_app_install"
before_app_uninstall = "frappe_paystack.install.before_app_uninstall"

before_tests = "frappe_paystack.tests.support.fixtures.before_tests"

# ---------------------------------------------------------------------------
# Web
# ---------------------------------------------------------------------------
website_route_rules = [{"from_route": "/paystack-checkout/<reference>", "to_route": "paystack-checkout"}]

has_website_permission = {
    "Paystack Payment Log": [
        "frappe_paystack.core.portal.has_payment_log_website_permission",
        # Customer ownership; answers False unless ERPNext is installed.
        "frappe_paystack.integrations.erpnext.portal.has_payment_log_website_permission",
    ],
}

# Import-safe without ERPNext: returns immediately for other documents.
update_website_context = ["frappe_paystack.integrations.erpnext.portal.apply_payment_state"]

jinja = {
    "methods": [
        "frappe_paystack.core.portal.paystack_payment_link",
        "frappe_paystack.core.portal.paystack_payment_qr",
    ]
}

# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
scheduler_events = {
    "cron": {
        "*/10 * * * *": [
            # Webhook re-drive, verification of silent checkouts, notification retries.
            "frappe_paystack.core.sweeps.every_ten_minutes",
            # ERPNext: Payment Entries for captures not yet booked (no-op without ERPNext).
            "frappe_paystack.integrations.erpnext.payment_entry.retry_stuck_bookings",
        ],
    },
    "hourly_long": [
        "frappe_paystack.core.sweeps.sync_refunds",
        # ERPNext: Journal Entries for payouts not yet booked (no-op without ERPNext).
        "frappe_paystack.integrations.erpnext.settlement.retry_unposted_settlements",
    ],
    "daily_long": [
        "frappe_paystack.core.sweeps.run_daily_reconciliation",
        "frappe_paystack.core.sweeps.poll_settlements",
        # ERPNext Subscription invoices charged to saved cards (no-op without ERPNext).
        "frappe_paystack.integrations.erpnext.subscriptions.collect_subscription_payments",
    ],
}

# ---------------------------------------------------------------------------
# Extension points (see frappe_paystack/core/constants.py for the contracts).
# Any installed app can register adapters and subscribers in its own hooks.py.
# ---------------------------------------------------------------------------

# Behaviour beyond the Payments v1 contract, per reference DocType. These are
# ERPNext DocTypes: the adapters are only ever loaded for sessions that
# reference them, which can only exist when ERPNext is installed.
paystack_reference_adapters = {
    "Payment Request": "frappe_paystack.integrations.erpnext.adapters.PaymentRequestAdapter",
    "Sales Invoice": "frappe_paystack.integrations.erpnext.adapters.SalesInvoiceAdapter",
    "Sales Order": "frappe_paystack.integrations.erpnext.adapters.SalesOrderAdapter",
    "Dunning": "frappe_paystack.integrations.erpnext.adapters.DunningAdapter",
    "POS Invoice": "frappe_paystack.integrations.erpnext.adapters.POSInvoiceAdapter",
}

# Route a payment to the Paystack account of the document's company.
paystack_account_resolvers = ["frappe_paystack.integrations.erpnext.accounts.resolve_setting_for_reference"]

# Roles, besides System Manager and Paystack Manager, that may move money.
paystack_manager_roles = ["Accounts Manager"]

# Gateway events (each handler receives the document and must be idempotent).
paystack_payment_succeeded = ["frappe_paystack.integrations.erpnext.payment_entry.on_payment_succeeded"]
paystack_refund_updated = ["frappe_paystack.integrations.erpnext.refunds.on_refund_updated"]
paystack_settlement_recorded = ["frappe_paystack.integrations.erpnext.settlement.on_settlement_recorded"]

# ---------------------------------------------------------------------------
# ERPNext desk and portal assets. Hooks are static; each script is inert
# unless its ERPNext form or page is present.
# ---------------------------------------------------------------------------
app_include_css = "paystack_reconciliation.bundle.css"
app_include_js = ["paystack_actions.bundle.js", "paystack_pos.bundle.js"]
web_include_js = "paystack_cart_guard.bundle.js"

doctype_js = {
    "Sales Invoice": "public/js/erpnext/sales_invoice.js",
    "Sales Order": "public/js/erpnext/sales_order.js",
    "Dunning": "public/js/erpnext/dunning.js",
}

# doc_events are imported only when the event fires, which for these
# DocTypes can only happen when ERPNext is installed.
doc_events = {
    "Sales Invoice": {
        "on_submit": "frappe_paystack.integrations.erpnext.sales_invoice.on_submit",
    },
    # ERPNext rules for an account (company currency, accounts of the same
    # company) and its Payment Gateway Account; no-ops without ERPNext.
    "Paystack Gateway Setting": {
        "validate": "frappe_paystack.integrations.erpnext.setup.validate_setting_event",
        "on_update": "frappe_paystack.integrations.erpnext.setup.on_setting_update_event",
    },
}
