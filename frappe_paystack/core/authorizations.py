"""
Reusable card authorizations (saved cards) for any payer.

Stored only when the account opts in (`save_card_authorizations`) and the
payer can be identified: by the reference adapter (e.g. ERPNext: the Customer)
or by the logged-in user who paid. Charging a saved card creates an ordinary
session and goes through the same state machine as a checkout.
"""

from __future__ import annotations

from typing import Any, Optional

import frappe
from frappe import _
from frappe.utils import cint, getdate, nowdate

from frappe_paystack.core import money
from frappe_paystack.core.adapters import get_adapter
from frappe_paystack.core.client import client_for
from frappe_paystack.core.constants import (
    AUTHORIZATION,
    CAPTURED_STATUSES,
    FAILED,
    GATEWAY_SETTING,
    HOOK_AUTHORIZATION_CAPTURED,
    PAYMENT_LOG,
    VIA_CHARGE,
)
from frappe_paystack.core.logging import record_failure
from frappe_paystack.core.users import GUEST

# Channels Paystack can charge again without the payer present.
REUSABLE_CHANNELS = ("card",)

PUBLIC_FIELDS = ("name", "card_label", "card_type", "brand", "bank", "last4", "exp_month", "exp_year", "channel")

CHARGE_REJECTED = ("failed", "abandoned", "reversed")


def is_storable(authorization: dict) -> bool:
    if not authorization.get("reusable"):
        return False
    if authorization.get("channel") not in REUSABLE_CHANNELS:
        return False
    return bool(authorization.get("authorization_code") and authorization.get("signature"))


def authorization_fields(authorization: dict, customer_code: Optional[str]) -> dict:
    return {
        "customer_code": customer_code,
        "authorization_code": authorization.get("authorization_code"),
        "signature": authorization.get("signature"),
        "last4": authorization.get("last4"),
        "exp_month": authorization.get("exp_month"),
        "exp_year": authorization.get("exp_year"),
        "card_type": authorization.get("card_type"),
        "brand": authorization.get("brand"),
        "bank": authorization.get("bank"),
        "channel": authorization.get("channel"),
        "reusable": 1 if authorization.get("reusable") else 0,
        "active": 1,
    }


def store_authorization(
    setting: str,
    authorization: dict,
    email: Optional[str] = None,
    customer_code: Optional[str] = None,
    party_type: Optional[str] = None,
    party: Optional[str] = None,
    user: Optional[str] = None,
) -> Optional[str]:
    """Record (or refresh) a reusable instrument and return its name."""
    if not is_storable(authorization) or not (party or user or email):
        return None

    values = authorization_fields(authorization, customer_code)
    filters = {"signature": values["signature"], "gateway_setting": setting}
    if party_type and party:
        filters.update({"party_type": party_type, "party": party})
    elif user:
        filters["user"] = user
    else:
        filters["email"] = email

    existing = frappe.db.get_value(AUTHORIZATION, filters, "name")
    doc = frappe.get_doc(AUTHORIZATION, existing) if existing else frappe.new_doc(AUTHORIZATION)
    doc.update(
        {
            "gateway_setting": setting,
            "party_type": party_type or doc.party_type,
            "party": party or doc.party,
            "user": user or doc.user,
            "email": email or doc.email,
        }
    )
    doc.update(values)
    doc.flags.ignore_permissions = True
    doc.flags.ignore_links = True
    doc.save()
    return doc.name


def capture_from_transaction(session_name: str, tx: dict) -> Optional[str]:
    """Store the instrument a captured session was paid with, when the account opts in."""
    session = frappe.get_doc(PAYMENT_LOG, session_name)
    if not session.gateway_setting or not cint(
        frappe.db.get_value(GATEWAY_SETTING, session.gateway_setting, "save_card_authorizations")
    ):
        return None

    authorization = tx.get("authorization") if isinstance(tx.get("authorization"), dict) else {}
    customer = tx.get("customer") if isinstance(tx.get("customer"), dict) else {}
    payer = get_adapter(session.linked_doctype).get_payer(session) if session.linked_doctype else {}
    user = session.run_as_user if session.run_as_user and session.run_as_user != GUEST else None

    name = store_authorization(
        setting=session.gateway_setting,
        authorization=authorization,
        email=customer.get("email") or session.payer_email,
        customer_code=customer.get("customer_code"),
        party_type=payer.get("party_type"),
        party=payer.get("party"),
        user=user,
    )
    if name:
        session.db_set("customer_authorization", name, update_modified=False)
        from frappe_paystack.core.notify import broadcast

        broadcast(HOOK_AUTHORIZATION_CAPTURED, frappe.get_doc(AUTHORIZATION, name))
    return name


def has_expired(doc: Any) -> bool:
    month, year = cint(doc.exp_month), cint(doc.exp_year)
    if not month or not year:
        return False
    today = getdate(nowdate())
    return (year, month) < (today.year, today.month)


def usable_authorizations(
    party_type: Optional[str] = None,
    party: Optional[str] = None,
    user: Optional[str] = None,
    setting: Optional[str] = None,
) -> list:
    """Return the chargeable instruments of a payer, newest first (no codes)."""
    filters: dict = {"active": 1, "reusable": 1}
    if party_type and party:
        filters.update({"party_type": party_type, "party": party})
    elif user:
        filters["user"] = user
    else:
        return []
    if setting:
        filters["gateway_setting"] = setting
    rows = frappe.get_all(AUTHORIZATION, filters=filters, fields=list(PUBLIC_FIELDS) + ["exp_month", "exp_year"],
                          order_by="modified desc")
    return [row for row in rows if not has_expired(row)]


def charge_saved_authorization(
    authorization: str,
    controller: Any,
    kwargs: dict,
    payer_user: Optional[str] = None,
) -> Any:
    """Create a session for a payment request (get_payment_url kwargs) and charge a saved card for it."""
    from frappe_paystack.core.session import create_session

    session = create_session(controller, kwargs, payer_user=payer_user, reuse=False)
    return charge_session(session.name, authorization)


def charge_session(session_name: str, authorization: str) -> Any:
    """
    Charge a saved card for an existing unpaid session and return the session.

    Paystack answers synchronously and the result goes through the same state
    machine as a checkout (the webhook that follows is a no-op). Raises when the
    card was declined or needs the payer present.
    """
    from frappe_paystack.core.lifecycle import OUTCOME_PAID, apply_transaction, payability
    from frappe_paystack.core.notify import notify_success
    from frappe_paystack.core.session import attempt_reference, lock

    saved = frappe.get_doc(AUTHORIZATION, authorization)
    if not (saved.active and saved.reusable) or has_expired(saved):
        frappe.throw(_("That saved card can no longer be charged."))

    session = lock(session_name)
    payable, reason = payability(session)
    if not payable:
        frappe.throw(reason)
    if saved.gateway_setting and saved.gateway_setting != session.gateway_setting:
        frappe.throw(_("That saved card belongs to another Paystack account."), frappe.PermissionError)

    setting = frappe.get_doc(GATEWAY_SETTING, session.gateway_setting)
    attempt = cint(session.attempt_count) + 1
    reference = attempt_reference(session.name, attempt)
    email = session.payer_email or saved.email
    session.db_set(
        {"attempt_count": attempt, "payment_reference": reference, "customer_authorization": saved.name,
         "payer_email": email, "access_code": None, "authorization_url": None},
        update_modified=False,
    )
    frappe.db.commit()

    try:
        result = client_for(setting, PAYMENT_LOG, session.name).charge_authorization(
            email=email,
            amount_minor=money.to_minor(session.amount, session.currency),
            currency=session.currency,
            reference=reference,
            authorization_code=saved.get_password("authorization_code"),
            metadata={"session": session.name, "reference": session.name, "reference_doctype": session.linked_doctype,
                      "reference_docname": session.linked_docname},
        )
    except Exception:
        frappe.db.rollback()
        session.db_set(
            {"status": FAILED, "errors": record_failure(f"Paystack saved-card charge failed for {session.name}",
                                                          reference_doctype=PAYMENT_LOG, reference_name=session.name)}
        )
        frappe.db.commit()
        raise

    outcome = apply_transaction(session.name, result, via=VIA_CHARGE, account=setting.name)
    session.reload()
    if session.status in CAPTURED_STATUSES:
        notify_success(session.name)
        session.reload()
        return session

    status = str(result.get("status") or "").lower()
    message = result.get("gateway_response") or result.get("message") or status
    if status in CHARGE_REJECTED or (outcome != OUTCOME_PAID and session.status == FAILED):
        frappe.throw(_("The saved card was declined: {0}").format(message))
    frappe.throw(
        _("This card needs the payer to authorise the payment ({0}). Send them the checkout link instead.").format(message)
    )
