"""
Reference adapters for ERPNext DocTypes.

Registered in hooks.py under `paystack_reference_adapters`. A session can only
reference these DocTypes when ERPNext is installed, so the core never loads
this module on other sites.
"""

from __future__ import annotations

from typing import Any, Optional

import frappe
from frappe import _
from frappe.utils import flt

from frappe_paystack.core import money
from frappe_paystack.core.adapters import ReferenceAdapter
from frappe_paystack.core.constants import PAYMENT_LOG
from frappe_paystack.integrations.erpnext import accounts

POS_INVOICE = "POS Invoice"
PAYMENT_REQUEST = "Payment Request"

# Submitted-document statuses that still owe money (15.x list).
PAYABLE_STATUSES = (
    "Partly Paid",
    "Partly Paid and Discounted",
    "Unpaid",
    "Unpaid and Discounted",
    "Overdue",
    "Overdue and Discounted",
    "To Deliver and Bill",
    "To Bill",
    "To Deliver",
    "To Pay",
    "Unresolved",
)

OPEN_REQUEST_STATUSES = ("Draft", "Requested", "Initiated", "Partially Paid")


def charge_in_account_currency(request: dict, doc: Any, setting: Any) -> dict:
    """
    ERPNext charges Paystack the company-currency amount (15.x behaviour).

    A document in another currency is converted with its own conversion rate,
    so the Payment Entry booked later reconciles exactly.
    """
    account_currency = money.clean_currency(setting.currency)
    currency = money.clean_currency(request.get("currency") or doc.get("currency"))
    rate = flt(doc.get("conversion_rate")) or 1.0
    if currency and currency != account_currency and rate != 1.0:
        request["document_amount"] = request.get("amount")
        request["document_currency"] = currency
        request["amount"] = flt(flt(request.get("amount")) * rate, 2)
        request["currency"] = account_currency
    elif not currency:
        request["currency"] = account_currency
    return request


class ERPNextDocumentAdapter(ReferenceAdapter):
    """Sales Invoice, Sales Order and Dunning collected through a direct Paystack link."""

    def resolve_account(self, reference_doctype: str, reference_docname: str) -> Optional[str]:
        return accounts.setting_for_company(accounts.company_for_reference(reference_doctype, reference_docname))

    def prepare_request(self, request: dict, setting: Any) -> dict:
        doc = frappe.get_doc(request["reference_doctype"], request["reference_docname"])
        return charge_in_account_currency(request, doc, setting)

    def on_session_created(self, session: Any) -> None:
        company = accounts.company_for_reference(session.linked_doctype, session.linked_docname)
        accounts.set_if_field(PAYMENT_LOG, session.name, {"company": company or accounts.session_company(session)})

    def get_payer(self, session: Any) -> dict:
        customer = frappe.db.get_value(session.linked_doctype, session.linked_docname, "customer")
        if not customer:
            return {}
        return {"party_type": "Customer", "party": customer, "email": accounts.customer_email(customer) or session.payer_email,
                "name": customer}

    def is_payable(self, session: Any) -> tuple:
        doc = frappe.get_doc(session.linked_doctype, session.linked_docname)
        if doc.docstatus != 1:
            return False, _("The related document is not submitted or was cancelled.")
        if doc.get("status") not in PAYABLE_STATUSES:
            return False, _("This document has been settled and is no longer payable.")
        return True, None

    def get_checkout_context(self, session: Any) -> list:
        doc = frappe.get_doc(session.linked_doctype, session.linked_docname)
        rows = []
        if doc.get("customer"):
            rows.append({"label": _("Billed to"), "value": doc.get("customer_name") or doc.customer})
        rows.append({"label": _("Document"), "value": f"{_(doc.doctype)} {doc.name}"})
        data = frappe.parse_json(session.request_data or "{}")
        currency = money.clean_currency(doc.get("currency"))
        if currency and currency != money.clean_currency(session.currency):
            rows.append(
                {
                    "label": _("Document total"),
                    "value": f"{money.format_amount(data.get('amount') or 0, currency)} @ {flt(doc.get('conversion_rate'), 4)}",
                }
            )
        return rows


class SalesInvoiceAdapter(ERPNextDocumentAdapter):
    pass


class SalesOrderAdapter(ERPNextDocumentAdapter):
    pass


class DunningAdapter(ERPNextDocumentAdapter):
    pass


class POSInvoiceAdapter(ERPNextDocumentAdapter):
    """A POS tender paid by link: the till books the sale when the invoice is submitted."""

    def is_payable(self, session: Any) -> tuple:
        docstatus = frappe.db.get_value(POS_INVOICE, session.linked_docname, "docstatus")
        if docstatus == 2:
            return False, _("{0} has been cancelled.").format(session.linked_docname)
        return True, None

    def prepare_request(self, request: dict, setting: Any) -> dict:
        request.setdefault("currency", frappe.db.get_value(POS_INVOICE, request["reference_docname"], "currency"))
        return request


class PaymentRequestAdapter(ReferenceAdapter):
    """
    ERPNext Payment Requests (Sales Order/Invoice checkout, Webshop, Education Fees, POS phone).

    Completion settles the request with PaymentRequest.set_as_paid(), which
    books the Payment Entry. Booking needs system rights, so the callback runs
    as Administrator (15.x behaviour); Webshop's on_payment_authorized, which
    calls set_as_paid() itself for signed-in users, runs first and the request
    is only settled here if it is still unpaid, so nothing is booked twice.
    """

    def resolve_account(self, reference_doctype: str, reference_docname: str) -> Optional[str]:
        return accounts.setting_for_company(frappe.db.get_value(PAYMENT_REQUEST, reference_docname, "company"))

    def prepare_request(self, request: dict, setting: Any) -> dict:
        pr = frappe.get_doc(PAYMENT_REQUEST, request["reference_docname"])
        if pr.reference_doctype and pr.reference_name and frappe.db.exists(pr.reference_doctype, pr.reference_name):
            reference = frappe.get_doc(pr.reference_doctype, pr.reference_name)
            return charge_in_account_currency(request, reference, setting)
        return request

    def on_session_created(self, session: Any) -> None:
        accounts.set_if_field(
            PAYMENT_LOG,
            session.name,
            {"company": frappe.db.get_value(PAYMENT_REQUEST, session.linked_docname, "company"),
             "payment_request": session.linked_docname},
        )

    def get_payer(self, session: Any) -> dict:
        pr = frappe.db.get_value(PAYMENT_REQUEST, session.linked_docname, ["party_type", "party", "email_to"], as_dict=True)
        if not pr or not pr.party:
            return {}
        return {"party_type": pr.party_type, "party": pr.party, "email": pr.email_to or session.payer_email, "name": pr.party}

    def is_payable(self, session: Any) -> tuple:
        pr = frappe.db.get_value(PAYMENT_REQUEST, session.linked_docname,
                                 ["docstatus", "status", "reference_doctype", "reference_name"], as_dict=True)
        if pr.docstatus == 2 or pr.status in ("Paid", "Cancelled", "Completed"):
            return False, _("This payment request is no longer payable.")
        if pr.reference_doctype and pr.reference_name and frappe.db.exists(pr.reference_doctype, pr.reference_name):
            reference = frappe.db.get_value(pr.reference_doctype, pr.reference_name, ["docstatus", "status"], as_dict=True)
            if pr.reference_doctype == POS_INVOICE:
                return (reference.docstatus != 2), _("The POS Invoice was cancelled.")
            if reference.docstatus != 1:
                return False, _("The related document is not submitted or was cancelled.")
        return True, None

    def get_checkout_context(self, session: Any) -> list:
        pr = frappe.get_doc(PAYMENT_REQUEST, session.linked_docname)
        rows = []
        if pr.party:
            rows.append({"label": _("Billed to"), "value": pr.party})
        if pr.reference_doctype and pr.reference_name:
            rows.append({"label": _("Document"), "value": f"{_(pr.reference_doctype)} {pr.reference_name}"})
        return rows

    def notification_user(self, session: Any) -> Optional[str]:
        return "Administrator"

    def notify_success(self, session: Any, reference_doc: Any) -> Any:
        from frappe_paystack.integrations.erpnext.payment_request import settle_payment_request

        return settle_payment_request(session, reference_doc)

    def request_for_payment(self, controller: Any, **kwargs) -> Any:
        from frappe_paystack.integrations.erpnext.pos import request_phone_payment

        return request_phone_payment(controller, **kwargs)
