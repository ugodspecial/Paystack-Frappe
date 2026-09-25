"""
Activation of the ERPNext adapter (idempotent) and its removal.

The ERPNext-only settings keep the field names they had as standard fields in
15.x (company, suspense_account, mode_of_payment, settlement_bank_account,
paystack_fee_account, auto_refund_on_credit_note, auto_charge_subscriptions,
payment_request, payment_entry, reversal_payment_entry, journal_entry, ...),
now as Custom Fields. Frappe keeps a column when its field leaves the standard
schema and when a Custom Field is deleted, so existing data is preserved
across the upgrade and across deactivate/activate.
"""

from __future__ import annotations

import json
import os

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from frappe_paystack.core.constants import (
    AUTHORIZATION,
    GATEWAY_SETTING,
    PAYMENT_LOG,
    RECONCILIATION_LOG,
    REFUND_LOG,
    SETTLEMENT,
)

MODE_OF_PAYMENT = "Paystack"
PAYMENT_GATEWAY = "Paystack"
PAYMENT_TYPES = "Cash\nBank\nGeneral\nPhone\nEmail"
ACCOUNTS_MANAGER = "Accounts Manager"
ACCOUNTS_USER = "Accounts User"

BOOKING_STATUSES = "\nPending\nBooked\nNeeds Attention\nNot Required"

CUSTOM_FIELDS = {
    GATEWAY_SETTING: [
        {"fieldname": "paystack_erpnext_section", "fieldtype": "Section Break", "label": "ERPNext",
         "insert_after": "separate_payment_gateway"},
        {"fieldname": "company", "fieldtype": "Link", "label": "Company", "options": "Company",
         "insert_after": "paystack_erpnext_section", "in_list_view": 1,
         "description": "The company this Paystack account collects for. Payments for its documents are routed here."},
        {"fieldname": "suspense_account", "fieldtype": "Link", "label": "Suspense Account", "options": "Account",
         "insert_after": "company",
         "description": "Captured money is booked here until Paystack pays it out."},
        {"fieldname": "mode_of_payment", "fieldtype": "Link", "label": "Mode of Payment", "options": "Mode of Payment",
         "insert_after": "suspense_account"},
        {"fieldname": "auto_refund_on_credit_note", "fieldtype": "Check", "label": "Auto Refund on Credit Note",
         "default": "0", "insert_after": "mode_of_payment",
         "description": "Refund through Paystack when a credit note is submitted against a Paystack-paid invoice."},
        {"fieldname": "paystack_erpnext_column", "fieldtype": "Column Break", "insert_after": "auto_refund_on_credit_note"},
        {"fieldname": "settlement_bank_account", "fieldtype": "Link", "label": "Settlement Bank Account",
         "options": "Account", "insert_after": "paystack_erpnext_column",
         "description": "The bank account Paystack pays out to. With the fee account set, each payout posts a Journal Entry clearing the suspense account."},
        {"fieldname": "paystack_fee_account", "fieldtype": "Link", "label": "Paystack Fee Account", "options": "Account",
         "insert_after": "settlement_bank_account"},
        {"fieldname": "auto_charge_subscriptions", "fieldtype": "Check", "label": "Auto Charge Subscription Invoices",
         "default": "0", "insert_after": "paystack_fee_account",
         "description": "Charge a customer's saved card for invoices raised by an ERPNext Subscription (requires Save Reusable Cards)."},
    ],
    PAYMENT_LOG: [
        {"fieldname": "paystack_erpnext_section", "fieldtype": "Section Break", "label": "ERPNext",
         "insert_after": "expires_at", "collapsible": 0},
        {"fieldname": "company", "fieldtype": "Link", "label": "Company", "options": "Company", "read_only": 1,
         "insert_after": "paystack_erpnext_section", "in_standard_filter": 1},
        {"fieldname": "payment_request", "fieldtype": "Link", "label": "Payment Request", "options": "Payment Request",
         "read_only": 1, "no_copy": 1, "insert_after": "company"},
        {"fieldname": "payment_entry", "fieldtype": "Link", "label": "Payment Entry", "options": "Payment Entry",
         "read_only": 1, "no_copy": 1, "insert_after": "payment_request"},
        {"fieldname": "paystack_erpnext_column", "fieldtype": "Column Break", "insert_after": "payment_entry"},
        {"fieldname": "booking_status", "fieldtype": "Select", "label": "Booking Status", "options": BOOKING_STATUSES,
         "read_only": 1, "no_copy": 1, "insert_after": "paystack_erpnext_column", "in_standard_filter": 1,
         "description": "Whether ERPNext has booked the captured money (Payment Entry)."},
        {"fieldname": "retry_count", "fieldtype": "Int", "label": "Booking Attempts", "read_only": 1, "no_copy": 1,
         "insert_after": "booking_status"},
        {"fieldname": "last_retry", "fieldtype": "Datetime", "label": "Last Booking Attempt", "read_only": 1,
         "no_copy": 1, "insert_after": "retry_count"},
    ],
    REFUND_LOG: [
        {"fieldname": "paystack_erpnext_section", "fieldtype": "Section Break", "label": "ERPNext",
         "insert_after": "raw_response"},
        {"fieldname": "company", "fieldtype": "Link", "label": "Company", "options": "Company", "read_only": 1,
         "insert_after": "paystack_erpnext_section"},
        {"fieldname": "reversal_payment_entry", "fieldtype": "Link", "label": "Reversal Payment Entry",
         "options": "Payment Entry", "read_only": 1, "no_copy": 1, "insert_after": "company"},
    ],
    SETTLEMENT: [
        {"fieldname": "paystack_erpnext_section", "fieldtype": "Section Break", "label": "ERPNext",
         "insert_after": "integration_request"},
        {"fieldname": "company", "fieldtype": "Link", "label": "Company", "options": "Company", "read_only": 1,
         "insert_after": "paystack_erpnext_section"},
        {"fieldname": "journal_entry", "fieldtype": "Link", "label": "Journal Entry", "options": "Journal Entry",
         "read_only": 1, "no_copy": 1, "insert_after": "company"},
        {"fieldname": "booking_status", "fieldtype": "Select", "label": "Booking Status", "options": BOOKING_STATUSES,
         "read_only": 1, "no_copy": 1, "insert_after": "journal_entry"},
    ],
    RECONCILIATION_LOG: [
        {"fieldname": "company", "fieldtype": "Link", "label": "Company", "options": "Company", "read_only": 1,
         "insert_after": "gateway_setting"},
    ],
}

# Role -> rights, per DocType (15.x granted these as standard permissions).
ACCOUNTS_PERMISSIONS = {
    PAYMENT_LOG: {ACCOUNTS_MANAGER: ("read", "report", "export", "print"), ACCOUNTS_USER: ("read", "report", "print")},
    REFUND_LOG: {ACCOUNTS_MANAGER: ("read", "report", "export", "print"), ACCOUNTS_USER: ("read", "report", "print")},
    SETTLEMENT: {ACCOUNTS_MANAGER: ("read", "report", "export", "print"), ACCOUNTS_USER: ("read", "report")},
    RECONCILIATION_LOG: {ACCOUNTS_MANAGER: ("read", "write", "report", "export"), ACCOUNTS_USER: ("read", "report")},
    AUTHORIZATION: {ACCOUNTS_MANAGER: ("read", "write", "delete", "report"), ACCOUNTS_USER: ("read",)},
}

REPORTS = (
    "Paystack Transactions",
    "Paystack Activity",
    "Customer Paystack Volume",
    "Paystack Unsettled Payments",
    "Paystack Settlements vs Ledger",
)
PAGE = "paystack-reconciliation"

NUMBER_CARDS = [
    {
        "name": "Paystack Awaiting Booking",
        "document_type": PAYMENT_LOG,
        "filters_json": json.dumps([[PAYMENT_LOG, "booking_status", "in", ["Pending", "Needs Attention"]]]),
    },
    {
        "name": "Paystack Payouts Not Booked",
        "document_type": SETTLEMENT,
        "filters_json": json.dumps([[SETTLEMENT, "journal_entry", "is", "not set"]]),
    },
]

PRINT_FORMAT = "Paystack Invoice with Payment Link"
PRINT_FORMAT_FILE = os.path.join(os.path.dirname(__file__), "print_formats", "paystack_invoice_with_payment_link.json")

POS_INVOICE_FIELDS = [
    {"fieldname": "contact_mobile", "label": "Mobile No", "fieldtype": "Data", "read_only": 0},
    {"fieldname": "contact_email", "label": "Email", "fieldtype": "Data", "read_only": 0},
    {"fieldname": "request_for_payment", "label": "Request for Payment", "fieldtype": "Button", "read_only": 0},
]


def activate() -> None:
    create_custom_fields(CUSTOM_FIELDS, update=True)
    ensure_mode_of_payment()
    ensure_email_payment_type()
    ensure_permissions()
    ensure_custom_roles()
    ensure_print_format()
    from frappe_paystack.install import ensure_number_cards

    ensure_number_cards(NUMBER_CARDS)
    create_payment_gateway_accounts_for_existing_settings()
    for doctype in CUSTOM_FIELDS:
        frappe.clear_cache(doctype=doctype)


def deactivate() -> None:
    """Remove adapter customisations. Columns and their data stay in place."""
    for doctype, fields in CUSTOM_FIELDS.items():
        for field in fields:
            name = frappe.db.get_value("Custom Field", {"dt": doctype, "fieldname": field["fieldname"]})
            if name:
                frappe.delete_doc("Custom Field", name, force=True, ignore_permissions=True)
        frappe.clear_cache(doctype=doctype)
    for doctype, roles in ACCOUNTS_PERMISSIONS.items():
        frappe.db.delete("Custom DocPerm", {"parent": doctype, "role": ["in", list(roles)]})
    for card in NUMBER_CARDS:
        if frappe.db.exists("Number Card", card["name"]):
            frappe.delete_doc("Number Card", card["name"], force=True, ignore_permissions=True)
    if frappe.db.exists("Print Format", PRINT_FORMAT):
        frappe.delete_doc("Print Format", PRINT_FORMAT, force=True, ignore_permissions=True)


def ensure_mode_of_payment(pos_enabled: bool = False) -> str:
    if not frappe.db.exists("Mode of Payment", MODE_OF_PAYMENT):
        mop = frappe.get_doc(
            {"doctype": "Mode of Payment", "mode_of_payment": MODE_OF_PAYMENT, "enabled": 1,
             "type": "Phone" if pos_enabled else "General"}
        )
        mop.flags.ignore_permissions = True
        mop.insert()
    return MODE_OF_PAYMENT


def ensure_email_payment_type() -> None:
    frappe.make_property_setter(
        {"doctype": "Mode of Payment", "fieldname": "type", "property": "options", "value": PAYMENT_TYPES,
         "property_type": "Text"},
        is_system_generated=False,
    )
    frappe.clear_cache(doctype="Mode of Payment")


def ensure_permissions() -> None:
    from frappe.permissions import add_permission, update_permission_property

    for doctype, roles in ACCOUNTS_PERMISSIONS.items():
        for role, rights in roles.items():
            if not frappe.db.exists("Role", role):
                continue
            if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0}):
                add_permission(doctype, role, 0)
            for right in rights:
                update_permission_property(doctype, role, 0, right, 1)


def ensure_custom_roles() -> None:
    """Give the Accounts roles the reports and the reconciliation page (as 15.x did)."""
    targets = [("report", name) for name in REPORTS] + [("page", PAGE)]
    for kind, name in targets:
        if not frappe.db.exists("Report" if kind == "report" else "Page", name):
            continue
        filters = {kind: name}
        existing = frappe.db.get_value("Custom Role", filters, "name")
        doc = frappe.get_doc("Custom Role", existing) if existing else frappe.get_doc({"doctype": "Custom Role", **filters})
        if not existing:
            standard = frappe.get_all("Has Role", filters={"parent": name, "parenttype": "Report" if kind == "report" else "Page"},
                                      pluck="role")
            for role in standard:
                doc.append("roles", {"role": role})
        have = {row.role for row in doc.roles}
        for role in (ACCOUNTS_MANAGER, ACCOUNTS_USER) if kind == "report" else (ACCOUNTS_MANAGER,):
            if role not in have and frappe.db.exists("Role", role):
                doc.append("roles", {"role": role})
        doc.flags.ignore_permissions = True
        doc.save()


def ensure_print_format() -> None:
    if frappe.db.exists("Print Format", PRINT_FORMAT):
        return
    with open(PRINT_FORMAT_FILE) as handle:
        values = json.load(handle)
    values.pop("modified", None)
    doc = frappe.get_doc(values)
    doc.flags.ignore_permissions = True
    doc.insert()


def create_payment_gateway_account(company: str, suspense_account: str, currency: str) -> str:
    """Create or update the Payment Gateway Account ERPNext uses for Paystack in a company."""
    name = frappe.db.get_value("Payment Gateway Account", {"payment_gateway": PAYMENT_GATEWAY, "company": company}, "name")
    if name:
        account = frappe.get_doc("Payment Gateway Account", name)
        account.payment_account = suspense_account
        account.currency = currency
        account.flags.ignore_permissions = True
        account.save()
        return name
    account = frappe.get_doc(
        {"doctype": "Payment Gateway Account", "payment_gateway": PAYMENT_GATEWAY, "payment_account": suspense_account,
         "currency": currency, "company": company, "is_default": 1}
    )
    account.flags.ignore_permissions = True
    account.insert()
    return account.name


def create_payment_gateway_accounts_for_existing_settings() -> None:
    if not frappe.db.exists("Payment Gateway", PAYMENT_GATEWAY):
        return
    for row in frappe.get_all(GATEWAY_SETTING, filters={"enabled": 1}, fields=["name", "company", "suspense_account", "currency"]):
        if row.company and row.suspense_account:
            try:
                create_payment_gateway_account(row.company, row.suspense_account, row.currency)
            except Exception:
                frappe.log_error(title=f"Paystack: Payment Gateway Account for {row.company} failed", message=frappe.get_traceback())


def on_setting_update(setting) -> None:
    """Keep the company's Payment Gateway Account pointing at the suspense account."""
    if setting.enabled and setting.get("company") and setting.get("suspense_account"):
        create_payment_gateway_account(setting.company, setting.suspense_account, setting.currency)


def validate_setting(setting) -> None:
    """ERPNext rules for an account: company currency, accounts of the same company."""
    from frappe import _

    company = setting.get("company")
    if not company:
        return
    company_currency = frappe.db.get_value("Company", company, "default_currency")
    if company_currency and setting.currency != company_currency:
        frappe.throw(
            _("Account currency {0} is not the default currency of {1} ({2}). Paystack is charged the company-currency amount.").format(
                setting.currency, company, company_currency
            )
        )
    for fieldname in ("suspense_account", "settlement_bank_account", "paystack_fee_account"):
        account = setting.get(fieldname)
        if account:
            owner = frappe.db.get_value("Account", account, "company")
            if owner != company:
                frappe.throw(_("{0} {1} belongs to {2}, not {3}.").format(setting.meta.get_label(fieldname), account, owner, company))
    if setting.get("auto_charge_subscriptions") and not setting.get("save_card_authorizations"):
        frappe.throw(_("Auto Charge Subscription Invoices needs Save Reusable Cards to be enabled."))
    if setting.enabled and frappe.db.exists(GATEWAY_SETTING, {"enabled": 1, "company": company, "name": ["!=", setting.name]}):
        frappe.throw(_("Another enabled Paystack account already collects for {0}. Disable it first.").format(company))


def setup_pos_payment_mode(company: str, suspense_account: str, channel: str = "Email") -> str:
    """Wire the Paystack Mode of Payment up for the POS counter (15.x behaviour)."""
    from frappe import _

    if channel not in ("Phone", "Email"):
        frappe.throw(_("Unsupported POS payment channel: {0}").format(channel))
    ensure_email_payment_type()
    ensure_mode_of_payment(pos_enabled=True)
    mop = frappe.get_doc("Mode of Payment", MODE_OF_PAYMENT)
    mop.type = channel
    rows = [row for row in mop.accounts if row.company == company]
    if rows:
        for row in rows:
            row.default_account = suspense_account
    else:
        mop.append("accounts", {"company": company, "default_account": suspense_account})
    mop.flags.ignore_permissions = True
    mop.save()
    ensure_pos_invoice_fields()
    from frappe.custom.doctype.custom_field.custom_field import create_custom_field

    create_custom_field(
        "POS Invoice",
        {"fieldname": "contact_email", "label": "Email", "fieldtype": "Data", "options": "Email", "insert_after": "contact_mobile"},
        ignore_validate=True,
    )
    name = frappe.db.get_value("Payment Gateway Account", {"payment_gateway": PAYMENT_GATEWAY, "company": company}, "name")
    if name:
        frappe.db.set_value("Payment Gateway Account", name, "payment_channel", channel)
    return mop.name


def ensure_pos_invoice_fields() -> None:
    settings = frappe.get_single("POS Settings")
    ours = [field["fieldname"] for field in POS_INVOICE_FIELDS]
    kept = [row for row in settings.invoice_fields if row.fieldname not in ours]
    settings.invoice_fields = []
    for field in POS_INVOICE_FIELDS:
        settings.append("invoice_fields", field)
    for row in kept:
        settings.append("invoice_fields", {**row.as_dict(), "idx": None})
    settings.flags.ignore_permissions = True
    settings.save()


# ---------------------------------------------------------------------------
# doc_events on Paystack Gateway Setting (registered in hooks.py). The core
# controller never imports this module; these return at once without ERPNext.
# ---------------------------------------------------------------------------


def validate_setting_event(doc, method=None) -> None:
    from frappe_paystack.integrations.erpnext import is_available

    if is_available() and doc.meta.has_field("company"):
        validate_setting(doc)


def on_setting_update_event(doc, method=None) -> None:
    from frappe_paystack.integrations.erpnext import is_available

    if is_available() and doc.meta.has_field("company"):
        try:
            on_setting_update(doc)
        except Exception:
            frappe.log_error(title="Paystack: could not create the Payment Gateway Account", message=frappe.get_traceback())
