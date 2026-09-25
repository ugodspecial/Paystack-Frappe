"""
The ERPNext customer portal: /my-payments, receipts, and the pay-button state on
ERPNext's order and invoice pages. Every entry point is import-safe without
ERPNext and returns early when it is not installed.
"""

from __future__ import annotations

from typing import Any, Optional

import frappe
from frappe import _

from frappe_paystack.core.constants import CAPTURED_STATUSES, PAYMENT_LOG, REFUND_LOG
from frappe_paystack.integrations.erpnext import is_available

SALES_INVOICE = "Sales Invoice"
PAID_DOCTYPES = (SALES_INVOICE, "Sales Order", "POS Invoice")
PAYABLE_STATUSES = ["Unpaid", "Partly Paid", "Overdue"]
RECEIPT_PRINT_FORMAT = "Paystack Payment Receipt"
PAYMENT_STATE_PROCESSING = "processing"
PAYMENT_STATE_SETTLED = "settled"
PAYMENT_STATE_OPEN = "open"
PROCESSING_LABEL = "Processing Payment"
PROCESSING_COLOUR = "orange"
PAYMENT_HISTORY_LIMIT = 20
PORTAL_ROUTES = {SALES_INVOICE: "invoices", "Sales Order": "orders"}

PAYMENT_FIELDS = ["name", "payment_reference as reference", "linked_doctype", "linked_docname", "amount", "currency",
                  "status", "modified"]
REFUND_FIELDS = ["name", "payment_log", "refund_reference", "refund_amount", "currency", "status", "modified"]
INVOICE_FIELDS = ["name", "posting_date", "due_date", "outstanding_amount", "currency"]


def customer_for(user: str) -> Optional[str]:
    if not is_available() or not user or user == "Guest":
        return None
    contact = frappe.db.get_value("Contact", {"email_id": user}, "name")
    if not contact:
        return None
    return frappe.db.get_value("Dynamic Link", {"parenttype": "Contact", "parent": contact, "link_doctype": "Customer"},
                               "link_name")


def payment_state(doctype: str, docname: str) -> str:
    """'processing' while a capture awaits booking, 'settled' once booked with nothing owed, else 'open'."""
    rows = frappe.get_all(PAYMENT_LOG, filters={"linked_doctype": doctype, "linked_docname": docname,
                                                "status": ["in", list(CAPTURED_STATUSES)]},
                          fields=["name", "booking_status"] if frappe.get_meta(PAYMENT_LOG).has_field("booking_status")
                          else ["name"])
    if not rows:
        return PAYMENT_STATE_OPEN
    if any(row.get("booking_status") in ("Pending", "Needs Attention") for row in rows):
        return PAYMENT_STATE_PROCESSING
    from erpnext.accounts.doctype.payment_request.payment_request import get_amount

    if get_amount(frappe.get_doc(doctype, docname)) > 0:
        return PAYMENT_STATE_OPEN
    return PAYMENT_STATE_SETTLED


def apply_payment_state(context: Any) -> None:
    """`update_website_context`: hide the Pay button while a payment is under way or settled."""
    doc = context.get("doc") if hasattr(context, "get") else None
    if not doc or not hasattr(doc, "get") or doc.get("doctype") not in PAID_DOCTYPES or not is_available():
        return
    try:
        state = payment_state(doc.get("doctype"), doc.get("name"))
    except Exception:
        return
    if state == PAYMENT_STATE_OPEN:
        return
    context.paystack_payment_state = state
    context.show_pay_button = False
    context.enabled_checkout = False
    if state == PAYMENT_STATE_PROCESSING:
        doc.indicator_title = PROCESSING_LABEL
        doc.indicator_color = PROCESSING_COLOUR


def invoices_for(customer: str) -> list:
    invoices = frappe.get_all(SALES_INVOICE, filters={"customer": customer, "status": ["in", PAYABLE_STATUSES]},
                              fields=INVOICE_FIELDS)
    for invoice in invoices:
        invoice.payment_state = payment_state(SALES_INVOICE, invoice.name)
    return invoices


def payments_for(customer: str) -> list:
    payments = []
    for doctype in PAID_DOCTYPES:
        documents = frappe.get_all(doctype, filters={"customer": customer}, pluck="name")
        if documents:
            payments += frappe.get_all(PAYMENT_LOG, filters={"linked_doctype": doctype, "linked_docname": ["in", documents]},
                                       fields=PAYMENT_FIELDS, order_by="modified desc", limit=PAYMENT_HISTORY_LIMIT)
    for payment in payments:
        payment.route = PORTAL_ROUTES.get(payment.linked_doctype)
        payment.has_receipt = payment.status in CAPTURED_STATUSES
    payments.sort(key=lambda payment: payment.modified, reverse=True)
    return payments[:PAYMENT_HISTORY_LIMIT]


def refunds_for(payments: list) -> list:
    names = [payment.name for payment in payments]
    if not names:
        return []
    return frappe.get_all(REFUND_LOG, filters={"payment_log": ["in", names]}, fields=REFUND_FIELDS, order_by="modified desc",
                          limit=PAYMENT_HISTORY_LIMIT)


def owns_payment_log(customer: Optional[str], log: str) -> bool:
    if not customer:
        return False
    linked = frappe.db.get_value(PAYMENT_LOG, log, ["linked_doctype", "linked_docname"], as_dict=True)
    if not linked or linked.linked_doctype not in PAID_DOCTYPES:
        return False
    return frappe.db.get_value(linked.linked_doctype, linked.linked_docname, "customer") == customer


def has_payment_log_website_permission(doc: Any, ptype: str = "read", user: Optional[str] = None, verbose: bool = False) -> bool:
    if not is_available():
        return False
    return owns_payment_log(customer_for(user or frappe.session.user), doc.name)


@frappe.whitelist()
def download_payment_receipt(reference: str) -> None:
    if not frappe.db.exists(PAYMENT_LOG, reference):
        frappe.throw(_("That payment does not exist."), frappe.DoesNotExistError)
    if not owns_payment_log(customer_for(frappe.session.user), reference):
        frappe.throw(_("That payment is not yours to download."), frappe.PermissionError)
    if frappe.db.get_value(PAYMENT_LOG, reference, "status") not in CAPTURED_STATUSES:
        frappe.throw(_("A receipt is only issued once a payment is captured."))
    frappe.local.response.filename = f"{reference}.pdf"
    frappe.local.response.filecontent = frappe.get_print(PAYMENT_LOG, reference, print_format=RECEIPT_PRINT_FORMAT,
                                                         as_pdf=True, no_letterhead=True)
    frappe.local.response.type = "pdf"


def my_payments_context(context: Any) -> Any:
    if not is_available():
        raise frappe.PageDoesNotExistError
    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("You need to be logged in"), frappe.PermissionError)
    customer = customer_for(frappe.session.user)
    context.customer = customer
    context.invoices = []
    context.payments = []
    context.refunds = []
    context.processing_label = PROCESSING_LABEL
    if not customer:
        return context
    context.invoices = invoices_for(customer)
    context.payments = payments_for(customer)
    context.refunds = refunds_for(context.payments)
    return context
