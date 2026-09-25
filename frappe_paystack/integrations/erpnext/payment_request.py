"""
ERPNext Payment Requests paid through Paystack.

A Payment Request's get_payment_url() calls the Paystack controller with
reference_doctype="Payment Request"; the session references the request. On
completion the request is settled with set_as_paid(), which books the Payment
Entry (and, on version-16, invoices a paid Sales Order).
"""

from __future__ import annotations

from typing import Any, Optional

import frappe
from frappe import _
from frappe.utils import flt

from frappe_paystack.core.constants import PAYMENT_LOG
from frappe_paystack.integrations.erpnext.accounts import set_if_field

PAYMENT_GATEWAY = "Paystack"
PAYMENT_REQUEST = "Payment Request"
BILLABLE_DOCTYPES = ("Sales Order", "Sales Invoice")
OPEN_REQUEST_STATUSES = ("Requested", "Initiated", "Partially Paid")
SETTLEMENT_TOLERANCE = 0.01


def can_bill_through_payment_request(doctype: str) -> bool:
    return doctype in BILLABLE_DOCTYPES


def resolve_payment_entry(payment_request: str) -> Optional[str]:
    """The submitted Payment Entry ERPNext booked for a Payment Request."""
    return frappe.db.get_value(
        "Payment Entry Reference", {"payment_request": payment_request, "docstatus": 1}, "parent"
    ) or frappe.db.get_value("Payment Entry", {"reference_no": payment_request, "docstatus": 1}, "name")


def gateway_account(company: Optional[str]) -> Optional[str]:
    if not company:
        return None
    return frappe.db.get_value("Payment Gateway Account", {"payment_gateway": PAYMENT_GATEWAY, "company": company}, "name")


def open_payment_request(doc: Any, amount: float) -> Optional[str]:
    return frappe.db.get_value(
        PAYMENT_REQUEST,
        {
            "reference_doctype": doc.doctype,
            "reference_name": doc.name,
            "docstatus": 1,
            "status": ["in", OPEN_REQUEST_STATUSES],
            "grand_total": flt(amount),
        },
        "name",
    )


def build_payment_request(doc: Any, amount: float, email: Optional[str] = None) -> Any:
    """A submitted Paystack Payment Request billing `amount` on `doc`."""
    from erpnext.accounts.doctype.payment_request.payment_request import make_payment_request

    args = {
        "dt": doc.doctype,
        "dn": doc.name,
        "party_type": "Customer",
        "party": doc.get("customer"),
        "recipient_id": email,
        "mute_email": True,
        "return_doc": True,
    }
    account = gateway_account(doc.get("company"))
    if account:
        args["payment_gateway_account"] = account

    request = make_payment_request(**args)
    if not request.payment_gateway:
        frappe.throw(
            _("No Payment Gateway Account is set up for {0}. Save the Paystack Gateway Setting to create one.").format(
                doc.get("company")
            )
        )
    if flt(amount) and flt(amount) < flt(request.grand_total):
        request.grand_total = flt(amount)
    request.payment_channel = "Email"
    request.flags.mute_email = True
    if request.reference_doctype == "Sales Order" and request.meta.has_field("make_sales_invoice"):
        request.make_sales_invoice = 1
    if request.get("__unsaved") or request.is_new():
        request.insert(ignore_permissions=True)
    if request.docstatus == 0:
        request.submit()
    return request


def payment_request_checkout_url(doc: Any, amount: float, email: Optional[str] = None) -> str:
    existing = open_payment_request(doc, amount)
    request = frappe.get_doc(PAYMENT_REQUEST, existing) if existing else build_payment_request(doc, amount, email)
    return request.get_payment_url()


def settlement_mismatch(session: Any, request: Any) -> Optional[str]:
    """A message when the capture and what the request bills differ beyond the tolerance."""
    if not flt(session.amount_paid):
        return None
    rate = 1.0
    if request.reference_doctype and request.reference_name:
        rate = flt(frappe.db.get_value(request.reference_doctype, request.reference_name, "conversion_rate")) or 1.0
    if money_currency(session) == money_currency_of(request):
        rate = 1.0
    captured = flt(flt(session.amount_paid) / rate, request.precision("grand_total"))
    expected = flt(request.get("outstanding_amount") or request.grand_total)
    if abs(captured - expected) <= SETTLEMENT_TOLERANCE:
        return None
    return _("Paystack captured {0} but Payment Request {1} bills {2}. Settle this payment manually.").format(
        captured, request.name, expected
    )


def money_currency(session: Any) -> str:
    return (session.currency_paid or session.currency or "").upper()


def money_currency_of(request: Any) -> str:
    return (request.get("currency") or "").upper()


def settle_payment_request(session: Any, request: Any) -> Any:
    """
    The Payment Request adapter's notification: settle the request once.

    Runs as Administrator inside the core's notification savepoint. Raising
    rolls back and the core retries with back-off.
    """
    request.reload()
    if request.docstatus != 1:
        set_if_field(PAYMENT_LOG, session.name, {"booking_status": "Needs Attention"})
        frappe.throw(_("Payment Request {0} is not submitted.").format(request.name))

    redirect = None
    if request.status != "Paid" and request.get("payment_channel") != "Phone":
        mismatch = settlement_mismatch(session, request)
        if mismatch:
            set_if_field(PAYMENT_LOG, session.name, {"booking_status": "Needs Attention"})
            frappe.db.set_value(PAYMENT_LOG, session.name, "errors", mismatch, update_modified=False)
            return None

    request.flags.ignore_permissions = True
    # Webshop's override defines on_payment_authorized (it returns a redirect and
    # calls set_as_paid itself); other apps may hook it through doc_events.
    redirect = request.run_method("on_payment_authorized", "Completed")
    request.reload()

    if request.get("payment_channel") == "Phone":
        from frappe_paystack.integrations.erpnext.pos import notify_phone_payment

        notify_phone_payment(session, request)
        set_if_field(PAYMENT_LOG, session.name, {"booking_status": "Not Required"})
        return redirect

    if request.status != "Paid" and not resolve_payment_entry(request.name):
        frappe.flags.ignore_account_permission = True
        request.set_as_paid()

    payment_entry = resolve_payment_entry(request.name)
    if payment_entry:
        set_if_field(PAYMENT_LOG, session.name, {"payment_entry": payment_entry, "booking_status": "Booked"})
    return redirect if isinstance(redirect, str) else None
