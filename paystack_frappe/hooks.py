app_name = "paystack_frappe"
app_title = "Paystack Frappe"
app_publisher = "Anthony Emmanuel"
app_description = "Paystack payment gateway for Frappe Payments (ERPNext optional)"
app_email = "hackacehuawei@gmail.com"
app_license = "mit"

# Only Payments is required ("org/repo" so that `bench get-app --resolve-deps`
# can fetch it). ERPNext, LMS, Education and Webshop are optional
# consumers; ERPNext features are provided by paystack_frappe.integrations.erpnext,
# which activates itself when ERPNext is installed.
required_apps = ["frappe/payments"]

# ---------------------------------------------------------------------------
# Install / migrate lifecycle
# ---------------------------------------------------------------------------
after_install = "paystack_frappe.install.after_install"
after_migrate = "paystack_frappe.install.after_migrate"
before_uninstall = "paystack_frappe.install.before_uninstall"
# Another app installed or removed later (ERPNext activates/deactivates the adapter).
after_app_install = "paystack_frappe.install.after_app_install"
before_app_uninstall = "paystack_frappe.install.before_app_uninstall"

before_tests = "paystack_frappe.tests.support.fixtures.before_tests"

# ---------------------------------------------------------------------------
# Web
# ---------------------------------------------------------------------------
website_route_rules = [{"from_route": "/paystack-checkout/<reference>", "to_route": "paystack-checkout"}]

has_website_permission = {
    "Paystack Payment Log": [
        "paystack_frappe.core.portal.has_payment_log_website_permission",
        # Customer ownership; answers False unless ERPNext is installed.
        "paystack_frappe.integrations.erpnext.portal.has_payment_log_website_permission",
    ],
}

# Import-safe without ERPNext: returns immediately for other documents.
update_website_context = ["paystack_frappe.integrations.erpnext.portal.apply_payment_state"]

jinja = {
    "methods": [
        "paystack_frappe.core.portal.paystack_payment_link",
        "paystack_frappe.core.portal.paystack_payment_qr",
    ]
}

# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
scheduler_events = {
    "cron": {
        "*/10 * * * *": [
            # Webhook re-drive, verification of silent checkouts, notification retries.
            "paystack_frappe.core.sweeps.every_ten_minutes",
            # ERPNext: Payment Entries for captures not yet booked (no-op without ERPNext).
            "paystack_frappe.integrations.erpnext.payment_entry.retry_stuck_bookings",
        ],
    },
    "hourly_long": [
        "paystack_frappe.core.sweeps.sync_refunds",
        # ERPNext: Journal Entries for payouts not yet booked (no-op without ERPNext).
        "paystack_frappe.integrations.erpnext.settlement.retry_unposted_settlements",
    ],
    "daily_long": [
        "paystack_frappe.core.sweeps.run_daily_reconciliation",
        "paystack_frappe.core.sweeps.poll_settlements",
        # ERPNext Subscription invoices charged to saved cards (no-op without ERPNext).
        "paystack_frappe.integrations.erpnext.subscriptions.collect_subscription_payments",
    ],
}

# ---------------------------------------------------------------------------
# Extension points (see paystack_frappe/core/constants.py for the contracts).
# Any installed app can register adapters and subscribers in its own hooks.py.
# ---------------------------------------------------------------------------

# Behaviour beyond the Payments v1 contract, per reference DocType. These are
# ERPNext DocTypes: the adapters are only ever loaded for sessions that
# reference them, which can only exist when ERPNext is installed.
paystack_reference_adapters = {
    "Payment Request": "paystack_frappe.integrations.erpnext.adapters.PaymentRequestAdapter",
    "Sales Invoice": "paystack_frappe.integrations.erpnext.adapters.SalesInvoiceAdapter",
    "Sales Order": "paystack_frappe.integrations.erpnext.adapters.SalesOrderAdapter",
    "Dunning": "paystack_frappe.integrations.erpnext.adapters.DunningAdapter",
    "POS Invoice": "paystack_frappe.integrations.erpnext.adapters.POSInvoiceAdapter",
}

# Route a payment to the Paystack account of the document's company.
paystack_account_resolvers = ["paystack_frappe.integrations.erpnext.accounts.resolve_setting_for_reference"]

# Roles, besides System Manager and Paystack Manager, that may move money.
paystack_manager_roles = ["Accounts Manager"]

# Gateway events (each handler receives the document and must be idempotent).
paystack_payment_succeeded = ["paystack_frappe.integrations.erpnext.payment_entry.on_payment_succeeded"]
paystack_refund_updated = ["paystack_frappe.integrations.erpnext.refunds.on_refund_updated"]
paystack_settlement_recorded = ["paystack_frappe.integrations.erpnext.settlement.on_settlement_recorded"]

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
        "on_submit": "paystack_frappe.integrations.erpnext.sales_invoice.on_submit",
    },
    # ERPNext rules for an account (company currency, accounts of the same
    # company) and its Payment Gateway Account; no-ops without ERPNext.
    "Paystack Gateway Setting": {
        "validate": "paystack_frappe.integrations.erpnext.setup.validate_setting_event",
        "on_update": "paystack_frappe.integrations.erpnext.setup.on_setting_update_event",
    },
}
