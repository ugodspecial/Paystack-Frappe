"""
Install, migrate and uninstall lifecycle.

The core installs on any site with Frappe and Payments. The ERPNext adapter
is activated (custom fields, Mode of Payment, Payment Gateway Accounts,
permissions, number cards) only when ERPNext is installed, whether it was
installed before or after this app, and deactivated if ERPNext is removed.
"""

import json

import frappe

from frappe_paystack.core.constants import (
    CAPTURED_STATUSES,
    INTEGRATION_REQUEST,
    NEEDS_ATTENTION,
    PAYMENT_LOG,
    PAYSTACK_MANAGER,
    RECONCILIATION_LOG,
    REFUND_LOG,
)

NUMBER_CARD = "Number Card"
DASHBOARD_CHART = "Dashboard Chart"
ERROR_LOG = "Error Log"

# Number cards the workspace shows. ERPNext cards are added by the adapter.
NUMBER_CARDS = [
    {
        # Captured, not yet paid out by Paystack.
        "name": "Paystack Awaiting Settlement",
        "document_type": PAYMENT_LOG,
        "filters_json": json.dumps(
            [[PAYMENT_LOG, "status", "in", list(CAPTURED_STATUSES)], [PAYMENT_LOG, "settlement", "is", "not set"]]
        ),
    },
    {
        "name": "Paystack Failed Payments",
        "document_type": PAYMENT_LOG,
        "filters_json": json.dumps([[PAYMENT_LOG, "status", "=", "Failed"]]),
    },
    {
        "name": "Paystack Reconciliation Mismatches",
        "document_type": RECONCILIATION_LOG,
        "filters_json": json.dumps([[RECONCILIATION_LOG, "status", "=", "Mismatch"]]),
    },
    {
        "name": "Paystack Refunds Pending",
        "document_type": REFUND_LOG,
        "filters_json": json.dumps([[REFUND_LOG, "status", "in", ["Pending", "Processing"]]]),
    },
    {
        "name": "Paystack Failed API Calls",
        "document_type": INTEGRATION_REQUEST,
        "filters_json": json.dumps(
            [
                [INTEGRATION_REQUEST, "integration_request_service", "=", "Paystack"],
                [INTEGRATION_REQUEST, "status", "=", "Failed"],
            ]
        ),
    },
    {
        "name": "Paystack Errors Unreviewed",
        "document_type": ERROR_LOG,
        "filters_json": json.dumps([[ERROR_LOG, "method", "like", "Paystack%"], [ERROR_LOG, "seen", "=", 0]]),
    },
    {
        # Consumer callbacks that keep failing (see Consumer Notification on the payment).
        "name": "Paystack Notifications Failing",
        "document_type": PAYMENT_LOG,
        "filters_json": json.dumps([[PAYMENT_LOG, "notification_status", "in", ["Failed", "Needs Attention"]]]),
    },
    {
        "name": "Paystack Needs Attention",
        "document_type": PAYMENT_LOG,
        "filters_json": json.dumps([[PAYMENT_LOG, "status", "=", NEEDS_ATTENTION]]),
    },
]

DASHBOARD_CHARTS = [
    {
        "chart_name": "Paystack Payments Captured",
        "chart_type": "Sum",
        "document_type": PAYMENT_LOG,
        "based_on": "payment_date",
        "value_based_on": "amount_paid",
        "timespan": "Last Month",
        "time_interval": "Daily",
        "timeseries": 1,
        "type": "Line",
        "filters_json": json.dumps([[PAYMENT_LOG, "status", "in", list(CAPTURED_STATUSES)]]),
    },
    {
        "chart_name": "Paystack Payments by Status",
        "chart_type": "Group By",
        "document_type": PAYMENT_LOG,
        "group_by_based_on": "status",
        "group_by_type": "Count",
        "type": "Donut",
        "filters_json": "[]",
    },
]


def ensure_number_cards(cards=None) -> None:
    """Create missing number cards and keep the filters of the ones this app owns current."""
    for card in cards or NUMBER_CARDS:
        values = {
            "doctype": NUMBER_CARD,
            "type": "Document Type",
            "function": "Count",
            "is_public": 1,
            "show_percentage_stats": 0,
            "label": card["name"],
            **card,
        }
        if frappe.db.exists(NUMBER_CARD, card["name"]):
            frappe.db.set_value(
                NUMBER_CARD, card["name"], {"filters_json": card["filters_json"], "document_type": card["document_type"]},
                update_modified=False,
            )
            continue
        doc = frappe.get_doc(values)
        doc.flags.ignore_permissions = True
        doc.insert()


def ensure_dashboard_charts() -> None:
    for chart in DASHBOARD_CHARTS:
        if frappe.db.exists(DASHBOARD_CHART, chart["chart_name"]):
            frappe.db.set_value(DASHBOARD_CHART, chart["chart_name"], "filters_json", chart["filters_json"],
                                update_modified=False)
            continue
        doc = frappe.get_doc({"doctype": DASHBOARD_CHART, "is_public": 1, **chart})
        doc.flags.ignore_permissions = True
        doc.insert()


def create_dashboard_widgets() -> None:
    ensure_number_cards()
    ensure_dashboard_charts()


def ensure_roles() -> None:
    if not frappe.db.exists("Role", PAYSTACK_MANAGER):
        frappe.get_doc({"doctype": "Role", "role_name": PAYSTACK_MANAGER, "desk_access": 1}).insert(ignore_permissions=True)


def register_enabled_settings() -> None:
    """(Re-)register enabled accounts with Payments."""
    from frappe_paystack.core.accounts import register_setting
    from frappe_paystack.core.constants import GATEWAY_SETTING

    for name in frappe.get_all(GATEWAY_SETTING, filters={"enabled": 1}, pluck="name", order_by="creation asc"):
        try:
            register_setting(frappe.get_doc(GATEWAY_SETTING, name))
        except Exception:
            frappe.log_error(title=f"Paystack: could not register {name}", message=frappe.get_traceback())


def sync_erpnext_adapter() -> None:
    from frappe_paystack.integrations import erpnext

    if erpnext.is_available():
        erpnext.activate()


def after_install() -> None:
    ensure_roles()
    create_dashboard_widgets()
    register_enabled_settings()
    sync_erpnext_adapter()


def after_migrate() -> None:
    ensure_roles()
    create_dashboard_widgets()
    sync_erpnext_adapter()


def after_app_install(app_name: str) -> None:
    if app_name == "erpnext":
        from frappe_paystack.integrations import erpnext

        erpnext.activate()
        register_enabled_settings()


def before_app_uninstall(app_name: str) -> None:
    if app_name == "erpnext":
        from frappe_paystack.integrations import erpnext

        erpnext.deactivate()


def before_uninstall() -> None:
    from frappe_paystack.core.constants import GATEWAY_SETTING
    from frappe_paystack.integrations import erpnext

    if erpnext.is_available():
        erpnext.deactivate()
    for gateway in frappe.get_all("Payment Gateway", filters={"gateway_settings": GATEWAY_SETTING}, pluck="name"):
        try:
            frappe.delete_doc("Payment Gateway", gateway, force=True, ignore_permissions=True)
        except Exception:
            frappe.log_error(title=f"Paystack: could not remove Payment Gateway {gateway}", message=frappe.get_traceback())
