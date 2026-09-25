"""
Server-side Python API for apps that want Paystack-specific features.

Most apps never need this: resolving the gateway through Payments
(`payments.utils.get_payment_gateway_controller`) and calling
`get_payment_url(**kwargs)` is enough. These functions are plain Python (not
whitelisted); callers are trusted server code and must do their own
permission checks.
"""

from __future__ import annotations

from typing import Any, Optional

import frappe
from frappe import _

from frappe_paystack.core.constants import CAPTURED_STATUSES, DEFAULT_GATEWAY_NAME, PAYMENT_LOG


def _controller(gateway: Optional[str] = None) -> Any:
    from payments.utils import get_payment_gateway_controller

    return get_payment_gateway_controller(gateway or DEFAULT_GATEWAY_NAME)


def create_session(
    reference_doctype: str,
    reference_docname: str,
    amount: Any,
    currency: str,
    payer_email: Optional[str] = None,
    payer_name: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    redirect_to: Optional[str] = None,
    gateway: Optional[str] = None,
    payer_user: Optional[str] = None,
    **extras: Any,
) -> Any:
    """
    Create a payment session and return the Paystack Payment Log.

    `payer_user` starts the payment on behalf of another user (the consumer
    callback then runs as that user). Only System Managers may use it.
    """
    from frappe_paystack.core.session import create_session as _create

    kwargs = {
        "reference_doctype": reference_doctype,
        "reference_docname": reference_docname,
        "amount": amount,
        "currency": currency,
        "payer_email": payer_email,
        "payer_name": payer_name,
        "title": title,
        "description": description,
        "redirect_to": redirect_to,
        "payment_gateway": gateway or DEFAULT_GATEWAY_NAME,
        **extras,
    }
    return _create(_controller(gateway), {k: v for k, v in kwargs.items() if v is not None}, payer_user=payer_user)


def get_checkout_url(session: str) -> str:
    from frappe_paystack.core.session import checkout_url

    return checkout_url(session)


def get_session(session: str) -> dict:
    """Return the safe, public facts of a session."""
    doc = frappe.get_doc(PAYMENT_LOG, session)
    return {
        "name": doc.name,
        "status": doc.status,
        "paid": doc.status in CAPTURED_STATUSES,
        "amount": doc.amount,
        "currency": doc.currency,
        "amount_paid": doc.amount_paid,
        "currency_paid": doc.currency_paid,
        "total_refunded": doc.total_refunded,
        "reference_doctype": doc.linked_doctype,
        "reference_docname": doc.linked_docname,
        "notification_status": doc.notification_status,
        "expires_at": doc.expires_at,
    }


def verify_payment(session: str) -> dict:
    from frappe_paystack.core.constants import VIA_MANUAL
    from frappe_paystack.core.lifecycle import verify

    return verify(session, via=VIA_MANUAL)


def charge_saved_authorization(
    authorization: str,
    reference_doctype: str,
    reference_docname: str,
    amount: Any,
    currency: str,
    gateway: Optional[str] = None,
    payer_user: Optional[str] = None,
    **extras: Any,
) -> Any:
    """Charge a saved card for a document and return the session (raises when declined)."""
    from frappe_paystack.core.authorizations import charge_saved_authorization as _charge

    kwargs = {
        "reference_doctype": reference_doctype,
        "reference_docname": reference_docname,
        "amount": amount,
        "currency": currency,
        "payment_gateway": gateway or DEFAULT_GATEWAY_NAME,
        **extras,
    }
    return _charge(authorization, _controller(gateway), kwargs, payer_user=payer_user)


def refund_payment(session: str, amount: Optional[float] = None, reason: Optional[str] = None, **kwargs: Any) -> Any:
    """Refund a captured session (None refunds the remaining balance). Returns the Refund Log."""
    from frappe_paystack.core.refunds import refund_payment as _refund

    if not frappe.db.exists(PAYMENT_LOG, session):
        frappe.throw(_("Payment {0} not found.").format(session), frappe.DoesNotExistError)
    return _refund(session, amount=amount, reason=reason, **kwargs)
