"""
Who a consumer callback runs as (decision D5).

A payment session records who started it (`initiated_by`), who was
impersonating that user if anyone (`impersonated_by`) and who the consumer
callback must run as (`run_as_user`, the initiator unless a trusted server-side
caller named a payer). Every path that completes a payment (the payer's
browser, the Paystack webhook, the scheduler sweep and the desk actions an
administrator uses) notifies the consumer as `run_as_user`, never as whoever
happened to trigger the completion. An administrator who verifies a learner's
payment from the desk therefore never enrols themselves.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Optional

import frappe
from frappe import _

GUEST = "Guest"
ADMINISTRATOR = "Administrator"

# Per-request state frappe.set_user() resets; restored after a temporary switch.
_LOCAL_STATE = (
    "role_permissions",
    "new_doc_templates",
    "user_perms",
    "jenv",
    "jenv_restricted",
    "jenv_unrestricted",
)


def current_user() -> str:
    return getattr(getattr(frappe.local, "session", None), "user", None) or GUEST


def impersonator() -> Optional[str]:
    """Return the user impersonating the current session, if any (Frappe's Impersonate)."""
    try:
        return (frappe.session.data or {}).get("impersonated_by") or None
    except Exception:
        return None


def resolve_run_as_user(payer_user: Optional[str] = None) -> str:
    """
    Return the user a new session's consumer callback will run as.

    Without payer_user it is the current user (Guest for anonymous checkouts).
    payer_user ("on behalf of") is honoured only for System Managers, and never
    for Administrator as a target. It is accepted from server-side Python only
    (core.api.create_session), never from get_payment_url kwargs, because those
    can originate from untrusted input.
    """
    initiator = current_user()
    if not payer_user or payer_user == initiator:
        return initiator

    if initiator == GUEST or "System Manager" not in frappe.get_roles(initiator):
        frappe.throw(_("Only a System Manager can start a payment on behalf of another user."), frappe.PermissionError)

    if payer_user == ADMINISTRATOR:
        frappe.throw(_("A payment cannot be started on behalf of Administrator."), frappe.PermissionError)

    if payer_user != GUEST and not frappe.db.get_value("User", payer_user, "enabled"):
        frappe.throw(_("User {0} does not exist or is disabled.").format(payer_user), frappe.PermissionError)

    return payer_user


@contextmanager
def run_as(user: Optional[str]):
    """
    Temporarily act as `user`, restoring the caller's session afterwards.

    frappe.set_user() rewrites frappe.local.session (sid, data) and form_dict in
    place, which would corrupt a browser session if used inside a web request.
    The session object is therefore swapped for a copy while switched, and the
    original objects are put back on exit.
    """
    previous = current_user()
    if not user or user == previous:
        yield
        return

    saved_session = frappe.local.session
    saved_form_dict = getattr(frappe.local, "form_dict", None)
    saved_state = {name: getattr(frappe.local, name, None) for name in _LOCAL_STATE}

    frappe.local.session = frappe._dict(saved_session or {})
    frappe.local.session.data = frappe._dict()
    try:
        frappe.set_user(user)
        yield
    finally:
        frappe.local.session = saved_session
        frappe.local.form_dict = saved_form_dict if saved_form_dict is not None else frappe._dict()
        frappe.local.cache = {}
        for name, value in saved_state.items():
            setattr(frappe.local, name, value)
        # Anything cached for the temporary user is discarded.
        frappe.local.role_permissions = {}
        frappe.local.new_doc_templates = {}
        frappe.local.user_perms = None
