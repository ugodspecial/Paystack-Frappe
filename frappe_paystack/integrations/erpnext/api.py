"""
Whitelisted ERPNext endpoints (the 15.x dotted paths in frappe_paystack.api
delegate here): payment links for ERPNext documents, company checks and saved
cards charged against Customers.
"""

from __future__ import annotations

from typing import Any, Optional

import frappe
from frappe import _
from frappe.utils import flt

from frappe_paystack.core import money
from frappe_paystack.core.constants import AUTHORIZATION, PAYMENT_LOG
from frappe_paystack.integrations.erpnext import is_available
from frappe_paystack.integrations.erpnext.accounts import customer_email, payable_amount, setting_for_company


def require_erpnext() -> None:
    if not is_available():
        frappe.throw(_("This action needs ERPNext."))


def _controller():
    from payments.utils import get_payment_gateway_controller

    return get_payment_gateway_controller("Paystack")


def create_payment_link(doctype: str, docname: str, amount: Any = None, currency: Optional[str] = None) -> str:
    """A checkout URL for an ERPNext document. Sales Orders and Invoices bill through a Payment Request."""
    from frappe_paystack.core.session import checkout_url, create_session
    from frappe_paystack.integrations.erpnext.payment_request import (
        can_bill_through_payment_request,
        payment_request_checkout_url,
    )

    require_erpnext()
    doc = frappe.get_doc(doctype, docname)
    doc.check_permission("read")
    if not setting_for_company(doc.get("company")):
        frappe.throw(_("Paystack not enabled for {0}").format(doc.get("company") or ""))

    payable = payable_amount(doc)
    if payable <= 0:
        frappe.throw(_("There is nothing left to pay on {0} {1}.").format(doctype, docname))
    if amount in (None, ""):
        amount = payable
    elif flt(amount) <= 0:
        frappe.throw(_("A payment link amount must be greater than zero."))
    amount = min(flt(amount), payable)
    if currency:
        money.ensure_supported(currency)

    email = customer_email(doc.get("customer")) or doc.get("contact_email")
    if can_bill_through_payment_request(doctype):
        money.ensure_supported(doc.get("currency"))
        return payment_request_checkout_url(doc, amount, email)

    session = create_session(
        _controller(),
        {"reference_doctype": doctype, "reference_docname": docname, "amount": amount,
         "currency": doc.get("currency") or currency, "payer_email": email,
         "payer_name": doc.get("customer_name") or doc.get("customer"), "title": f"{_(doctype)} {docname}"},
    )
    return checkout_url(session.name)


def is_enabled_for_company(company: str) -> bool:
    return bool(is_available() and setting_for_company(company))


def saved_cards(customer: str, company: Optional[str] = None) -> list:
    from frappe_paystack.core.authorizations import usable_authorizations

    require_erpnext()
    if not frappe.has_permission("Customer", "read", doc=customer):
        frappe.throw(_("Not permitted to read Customer {0}.").format(customer), frappe.PermissionError)
    return usable_authorizations(party_type="Customer", party=customer, setting=setting_for_company(company) if company else None)


def charge_saved_card(doctype: str, docname: str, authorization: str, amount: Any = None) -> str:
    """Charge a Customer's saved card for a document and return the payment (session) name."""
    from frappe_paystack.core.authorizations import charge_session
    from frappe_paystack.core.permissions import check_money_permission

    require_erpnext()
    check_money_permission(_("You are not permitted to charge saved Paystack cards."))
    doc = frappe.get_doc(doctype, docname)
    doc.check_permission("read")
    card = frappe.get_doc(AUTHORIZATION, authorization)
    if card.party_type != "Customer" or card.party != doc.get("customer"):
        frappe.throw(_("That saved card belongs to another customer."), frappe.PermissionError)
    if not card.is_usable():
        frappe.throw(_("That saved card can no longer be charged."))

    url = create_payment_link(doctype, docname, amount, doc.get("currency"))
    session = url.rstrip("/").rsplit("/", 1)[-1]
    if not frappe.db.exists(PAYMENT_LOG, session):
        frappe.throw(_("Could not open a payment for {0}.").format(docname))
    return charge_session(session, card.name).name
