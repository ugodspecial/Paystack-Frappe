"""Checkout page context, website permissions and Jinja helpers (all ERPNext-free)."""

from __future__ import annotations

from typing import Any, Optional

import frappe
from frappe import _

from frappe_paystack.core import money
from frappe_paystack.core.adapters import get_adapter
from frappe_paystack.core.constants import (
    CAPTURED_STATUSES,
    CHECKOUT_INLINE,
    GATEWAY_SETTING,
    HOOK_CHECKOUT_CONTEXT,
    PAYMENT_LOG,
    PENDING,
)
from frappe_paystack.core.qr import qr_data_uri
from frappe_paystack.core.session import checkout_url, is_expired

GUEST = "Guest"


def checkout_context(session_name: str) -> Optional[dict]:
    """Return what the public checkout page may show for a session, or None if unknown."""
    from frappe_paystack.core.lifecycle import payability

    if not session_name or not frappe.db.exists(PAYMENT_LOG, session_name):
        return None

    session = frappe.get_doc(PAYMENT_LOG, session_name)
    setting = frappe.db.get_value(
        GATEWAY_SETTING, session.gateway_setting, ["public_key", "checkout_mode", "enabled"], as_dict=True
    ) if session.gateway_setting else None
    payable, reason = payability(session)

    rows = []
    if session.linked_doctype:
        try:
            rows = list(get_adapter(session.linked_doctype).get_checkout_context(session) or [])
        except Exception:
            frappe.log_error(title=f"Paystack: checkout context failed for {session.name}", message=frappe.get_traceback())
    context = {
        "reference": session.name,
        "status": session.status,
        "title": session.title or _("Payment"),
        "description": session.description or "",
        "amount": session.amount,
        "currency": session.currency,
        "formatted_amount": money.format_amount(session.amount, session.currency),
        "email": session.payer_email or "",
        "needs_email": not bool(session.payer_email),
        "payer_name": session.payer_name or "",
        "public_key": (setting or {}).get("public_key") if (setting or {}).get("enabled") else None,
        "checkout_mode": (setting or {}).get("checkout_mode") or CHECKOUT_INLINE,
        "is_payable": payable,
        "closed_reason": reason,
        "is_paid": session.status in CAPTURED_STATUSES,
        "is_expired": is_expired(session),
        "expires_at": str(session.expires_at or ""),
        "rows": rows,
    }
    for path in frappe.get_hooks(HOOK_CHECKOUT_CONTEXT) or []:
        try:
            frappe.get_attr(path)(session, context)
        except Exception:
            frappe.log_error(title=f"Paystack: {HOOK_CHECKOUT_CONTEXT} hook {path} failed", message=frappe.get_traceback())
    return context


def has_payment_log_website_permission(doc: Any, ptype: str = "read", user: Optional[str] = None, verbose: bool = False) -> bool:
    """A signed-in user may read the payments they started or were the payer of."""
    user = user or frappe.session.user
    if not user or user == GUEST or ptype not in ("read", "print"):
        return False
    return user in (doc.get("owner"), doc.get("run_as_user"), doc.get("initiated_by")) or (
        bool(doc.get("payer_email")) and doc.get("payer_email") == user
    )


def open_session_for(doc: Any) -> Optional[Any]:
    """Return the newest unpaid, unexpired session raised for a document."""
    name = frappe.db.get_value(
        PAYMENT_LOG,
        {"linked_doctype": doc.doctype, "linked_docname": doc.name, "status": PENDING},
        "name",
        order_by="creation desc",
    )
    if not name:
        return None
    session = frappe.get_doc(PAYMENT_LOG, name)
    return None if is_expired(session) else session


def paystack_payment_link(doc: Any) -> str:
    """Jinja: the checkout URL a document can currently be paid at, or empty."""
    try:
        session = open_session_for(doc)
    except Exception:
        return ""
    return checkout_url(session.name) if session else ""


def paystack_payment_qr(doc: Any) -> str:
    """Jinja: the checkout URL as an SVG QR data URI, or empty."""
    return qr_data_uri(paystack_payment_link(doc))


def payment_state(doctype: str, docname: str) -> str:
    """'settled' once a capture exists for the document, 'open' otherwise."""
    captured = frappe.db.exists(
        PAYMENT_LOG, {"linked_doctype": doctype, "linked_docname": docname, "status": ["in", list(CAPTURED_STATUSES)]}
    )
    return "settled" if captured else "open"


def page_context(reference: Optional[str], trxref: Optional[str] = None, context: Optional[Any] = None) -> Any:
    """
    Build the /paystack-checkout/<reference> page context.

    With `trxref` (Paystack's hosted-checkout return), the payment is verified
    on the server first and a paid payer is redirected (raises frappe.Redirect).
    """
    import json

    from frappe_paystack.core.constants import VIA_CALLBACK
    from frappe_paystack.core.lifecycle import verify

    context = context if context is not None else frappe._dict()
    context.no_cache = 1
    context.title = _("Complete Payment")
    context.doc = None
    context.payload = "null"
    context.reference = None

    if not reference or not frappe.db.exists(PAYMENT_LOG, reference):
        return context

    if trxref:
        result = None
        try:
            result = verify(reference, reference=trxref, via=VIA_CALLBACK)
        except frappe.PermissionError:
            result = None
        except Exception:
            frappe.log_error(title=f"Paystack: callback verification failed for {reference}", message=frappe.get_traceback())
        if result and result.get("redirect") and result.get("status") in CAPTURED_STATUSES:
            frappe.local.flags.redirect_location = result["redirect"]
            raise frappe.Redirect

    data = checkout_context(reference)
    context.reference = reference
    context.doc = data
    payload = json.dumps(
        {
            "reference": reference,
            "email": data.get("email"),
            "needs_email": data.get("needs_email"),
            "checkout_mode": data.get("checkout_mode"),
            "is_payable": data.get("is_payable"),
            "is_paid": data.get("is_paid"),
        },
        default=str,
    )
    for character, escape in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026")):
        payload = payload.replace(character, escape)
    context.payload = payload
    return context
