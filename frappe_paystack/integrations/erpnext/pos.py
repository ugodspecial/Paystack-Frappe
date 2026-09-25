"""POS: payment links by email and "Phone" payment requests shown on the till."""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import flt

from frappe_paystack.core.constants import CAPTURED_STATUSES, PAYMENT_LOG

MODE_OF_PAYMENT = "Paystack"
GATEWAY_TYPES = ("Phone", "Email")


def paystack_tender_amount(invoice: Any) -> float:
    return sum(flt(row.amount) for row in invoice.payments if row.mode_of_payment == MODE_OF_PAYMENT or row.type in GATEWAY_TYPES)


def resolve_email(invoice: Any, email: str = "") -> str:
    address = email or invoice.get("contact_email") or frappe.db.get_value("Customer", invoice.customer, "email_id")
    if not address:
        frappe.throw(_("Enter an email address for {0} to send the payment link.").format(invoice.customer))
    return address


def render_email(invoice: Any, amount: float, url: str) -> str:
    return frappe.render_template(  # nosemgrep - constant template path
        "frappe_paystack/templates/emails/pos_payment_link.html",
        {"invoice": invoice, "amount": frappe.utils.fmt_money(amount, currency=invoice.currency), "url": url,
         "items": invoice.items},
    )


@frappe.whitelist()
def send_pos_payment_link(pos_invoice: str, email: str = "") -> dict:
    """Email a checkout link for the Paystack tender of a (draft) POS Invoice."""
    from payments.utils import get_payment_gateway_controller

    from frappe_paystack.core.session import checkout_url, create_session

    invoice = frappe.get_doc("POS Invoice", pos_invoice)
    invoice.check_permission("read")
    amount = paystack_tender_amount(invoice)
    if amount <= 0:
        frappe.throw(_("Enter the amount to collect through Paystack first."))
    address = resolve_email(invoice, email)

    session = create_session(
        get_payment_gateway_controller("Paystack"),
        {"reference_doctype": "POS Invoice", "reference_docname": invoice.name, "amount": amount,
         "currency": invoice.currency, "payer_email": address, "payer_name": invoice.customer_name or invoice.customer,
         "title": _("POS Invoice {0}").format(invoice.name)},
        reuse=False,
    )
    url = checkout_url(session.name)
    frappe.sendmail(recipients=[address], subject=_("Payment link for {0}").format(invoice.name),
                    message=render_email(invoice, amount, url), reference_doctype="POS Invoice",
                    reference_name=invoice.name, now=True)
    frappe.publish_realtime("paystack_pos_awaiting", {"pos_invoice": invoice.name, "log": session.name, "email": address,
                                                      "amount": amount}, user=frappe.session.user)
    return {"log": session.name, "email": address, "amount": amount, "url": url}


def notify_pos_link_paid(session: Any) -> None:
    if session.linked_doctype != "POS Invoice":
        return
    outstanding = flt(session.amount) - flt(session.amount_paid)
    frappe.publish_realtime(
        "paystack_pos_paid",
        {"pos_invoice": session.linked_docname, "log": session.name, "amount_paid": flt(session.amount_paid),
         "outstanding": outstanding if outstanding > 0 else 0, "fully_paid": outstanding <= 0},
        user=session.initiated_by or session.owner,
    )


def notify_pos_charge_started(payment_request: Any, reference: str, response: dict) -> None:
    """Push the checkout URL of a POS phone payment to the till (15.x event)."""
    data = response.get("data") if isinstance(response.get("data"), dict) else response
    url = (data or {}).get("authorization_url")
    if not url:
        return
    frappe.publish_realtime(
        "paystack_pos_charge_url",
        {"pos_invoice": payment_request.reference_name, "payment_request": payment_request.name, "reference": reference,
         "url": url},
        user=frappe.session.user,
    )


@frappe.whitelist()
def pos_payment_status(log: str) -> dict:
    record = frappe.db.get_value(PAYMENT_LOG, log, ["name", "status", "amount", "amount_paid", "linked_docname"], as_dict=True)
    if not record:
        frappe.throw(_("Payment link {0} not found.").format(log))
    frappe.get_doc(PAYMENT_LOG, log).check_permission("read")
    outstanding = flt(record.amount) - flt(record.amount_paid)
    return {"log": record.name, "status": record.status, "pos_invoice": record.linked_docname,
            "amount_paid": flt(record.amount_paid), "outstanding": outstanding if outstanding > 0 else 0,
            "fully_paid": record.status in CAPTURED_STATUSES and outstanding <= 0}


def request_phone_payment(controller: Any, **kwargs) -> dict:
    """
    ERPNext POS "Phone" mode: start a Paystack checkout for a Payment Request and
    show its URL on the till. Completion is announced to the till by
    notify_phone_payment.
    """
    from frappe_paystack.core.lifecycle import start_checkout
    from frappe_paystack.core.session import create_session

    args = frappe._dict(kwargs)
    request = frappe.get_doc("Payment Request", args.reference_docname)
    session = create_session(
        controller,
        {"reference_doctype": "Payment Request", "reference_docname": request.name,
         "amount": flt(args.request_amount or args.grand_total or request.grand_total), "currency": args.currency or request.currency,
         "payer_email": request.email_to, "title": _("Payment Request {0}").format(request.name)},
        reuse=False,
    )
    checkout = start_checkout(session.name, email=request.email_to)
    notify_pos_charge_started(request, session.name, checkout)
    return checkout


def notify_phone_payment(session: Any, request: Any) -> None:
    """Release the POS payment screen once a phone payment is captured (15.x event)."""
    frappe.publish_realtime(
        event="process_phone_payment",
        doctype="POS Invoice",
        docname=request.reference_name,
        user=session.initiated_by or session.owner,
        message={"amount": flt(session.amount_paid), "success": True, "failure_message": ""},
    )
