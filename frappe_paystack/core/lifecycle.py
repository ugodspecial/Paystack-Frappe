"""
The payment state machine.

Every way a payment can complete (the payer returning from Paystack, the
inline popup, the webhook, the sweep and the desk "Verify" action) ends in
`apply_transaction`, which runs under a row lock, checks the transaction
against the session (reference, account, status, amount in subunits,
currency) and makes one idempotent transition. The browser never supplies an
amount, a currency or a reference: transactions are initialised on the server
and inline checkout resumes them with `resumeTransaction(access_code)`.
"""

from __future__ import annotations

from typing import Any, Optional

import frappe
from frappe import _
from frappe.utils import cint, get_url, getdate, now_datetime

from frappe_paystack.core import money
from frappe_paystack.core.adapters import get_adapter
from frappe_paystack.core.client import client_for
from frappe_paystack.core.constants import (
    CANCELLED,
    CAPTURED_STATUSES,
    CHECKOUT_HOSTED,
    CHECKOUT_INLINE,
    EXPIRED,
    FAILED,
    GATEWAY_SETTING,
    NEEDS_ATTENTION,
    PAID,
    PAYMENT_LOG,
    PENDING,
    RETRYABLE_STATUSES,
    TX_ABANDONED,
    TX_FAILED,
    TX_REVERSED,
    TX_SUCCESS,
    VIA_CALLBACK,
    VIA_MANUAL,
)
from frappe_paystack.core.logging import log_error_for
from frappe_paystack.core.session import (
    attempt_reference,
    checkout_url,
    is_expired,
    lock,
    reference_belongs,
)
from frappe_paystack.core.users import current_user

# Outcomes of apply_transaction.
OUTCOME_PAID = "paid"
OUTCOME_ALREADY_PAID = "already_paid"
OUTCOME_FAILED = "failed"
OUTCOME_CANCELLED = "cancelled"
OUTCOME_PENDING = "pending"
OUTCOME_MISMATCH = "mismatch"
OUTCOME_DUPLICATE = "duplicate_capture"
OUTCOME_IGNORED = "ignored"


class NotPayable(frappe.ValidationError):
    pass


def get_setting_for(session: Any) -> Any:
    if not session.gateway_setting:
        frappe.throw(_("Payment {0} has no Paystack account.").format(session.name))
    return frappe.get_doc(GATEWAY_SETTING, session.gateway_setting)


def payability(session: Any) -> tuple:
    """Return (payable, reason) for a session, including the reference adapter's opinion."""
    if session.status in CAPTURED_STATUSES:
        return False, _("This payment has already been completed.")
    if session.status == NEEDS_ATTENTION:
        return False, _("This payment is being reviewed. Please contact the merchant.")
    if session.status == EXPIRED or is_expired(session):
        return False, _("This payment link has expired. Please request a new one.")
    if session.status not in RETRYABLE_STATUSES:
        return False, _("This payment can no longer be made.")
    if session.gateway_setting and not frappe.db.get_value(GATEWAY_SETTING, session.gateway_setting, "enabled"):
        return False, _("Online payments are not available at the moment.")
    if session.linked_doctype and session.linked_docname:
        if not frappe.db.exists(session.linked_doctype, session.linked_docname):
            return False, _("The document this payment is for no longer exists.")
        payable, reason = get_adapter(session.linked_doctype).is_payable(session)
        if not payable:
            return False, reason or _("This document can no longer be paid.")
    return True, None


def start_checkout(session_name: str, email: Optional[str] = None) -> dict:
    """
    Open (or reuse) a Paystack transaction for a session and return what the browser needs.

    Returns {mode, reference, authorization_url, access_code, public_key}. Commits.
    """
    session = lock(session_name)
    payable, reason = payability(session)
    if not payable:
        frappe.db.commit()
        frappe.throw(reason, NotPayable)

    setting = get_setting_for(session)
    email = (email or "").strip()
    payer_email = session.payer_email or email
    if not payer_email or not frappe.utils.validate_email_address(payer_email):
        frappe.throw(_("Enter a valid email address to receive your receipt."))

    mode = setting.checkout_mode or CHECKOUT_INLINE
    reusable = (
        session.status == PENDING
        and session.access_code
        and session.authorization_url
        and (session.attempt_email or payer_email) == payer_email
    )
    if not reusable:
        attempt = cint(session.attempt_count) + 1
        reference = attempt_reference(session.name, attempt)
        data = client_for(setting, PAYMENT_LOG, session.name).initialize_transaction(
            email=payer_email,
            amount_minor=money.to_minor(session.amount, session.currency),
            currency=session.currency,
            reference=reference,
            callback_url=checkout_url(session.name),
            metadata=transaction_metadata(session, payer_email),
        )
        if not data.get("access_code") and not data.get("authorization_url"):
            frappe.throw(_("Paystack did not return a checkout for this payment."))
        session.db_set(
            {
                "status": PENDING,
                "attempt_count": attempt,
                "payment_reference": data.get("reference") or reference,
                "access_code": data.get("access_code"),
                "authorization_url": data.get("authorization_url"),
                "attempt_email": payer_email,
                "payer_email": session.payer_email or payer_email,
            },
            update_modified=True,
        )

    frappe.db.commit()
    return {
        "mode": mode if mode in (CHECKOUT_INLINE, CHECKOUT_HOSTED) else CHECKOUT_INLINE,
        "reference": session.payment_reference,
        "authorization_url": session.authorization_url,
        "access_code": session.access_code,
        "public_key": setting.public_key,
    }


def transaction_metadata(session: Any, email: str) -> dict:
    """Metadata every transaction carries; the webhook resolves the session through `session`."""
    return {
        "session": session.name,
        # Upstream 15.x read the Payment Log from metadata.reference.
        "reference": session.name,
        "reference_doctype": session.linked_doctype,
        "reference_docname": session.linked_docname,
        "email": email,
        "custom_fields": [
            {
                "display_name": _("Payment For"),
                "variable_name": "payment_for",
                "value": session.title or f"{session.linked_doctype or ''} {session.linked_docname or ''}".strip(),
            }
        ],
    }


def verify(session_name: str, reference: Optional[str] = None, via: str = VIA_CALLBACK) -> dict:
    """
    Ask Paystack about a session's transaction, apply the result and notify.

    Returns {"status", "outcome", "redirect"}. Safe to call repeatedly.
    """
    session = frappe.get_doc(PAYMENT_LOG, session_name)
    if reference and not reference_belongs(session, reference):
        frappe.throw(_("That transaction does not belong to this payment."), frappe.PermissionError)

    if session.status in CAPTURED_STATUSES:
        return finish(session_name, OUTCOME_ALREADY_PAID)

    reference = reference or session.payment_reference
    if not reference:
        return {"status": session.status, "outcome": OUTCOME_PENDING, "redirect": None}

    setting = get_setting_for(session)
    transaction = client_for(setting, PAYMENT_LOG, session.name).verify_transaction(reference)
    outcome = apply_transaction(session_name, transaction, via=via, account=setting.name)
    return finish(session_name, outcome)


def finish(session_name: str, outcome: str) -> dict:
    """Notify the consumer when the session is captured and compose the browser redirect."""
    from frappe_paystack.core.notify import notify_success

    redirect = None
    session = frappe.get_doc(PAYMENT_LOG, session_name)
    if session.status in CAPTURED_STATUSES:
        redirect = notify_success(session_name)
        session.reload()
    return {"status": session.status, "outcome": outcome, "redirect": result_url(session, redirect)}


def result_url(session: Any, consumer_redirect: Optional[str] = None) -> Optional[str]:
    """Return where the payer's browser goes next, following the Payments convention."""
    from urllib.parse import urlencode

    if session.status in CAPTURED_STATUSES:
        if consumer_redirect:
            return consumer_redirect
        params = {}
        if session.linked_doctype and session.linked_docname:
            params = {"doctype": session.linked_doctype, "docname": session.linked_docname}
        if session.redirect_to:
            params["redirect_to"] = session.redirect_to
        if not params.get("doctype"):
            return session.redirect_to or None
        return get_url("/payment-success?" + urlencode(params))

    if session.status == FAILED:
        params = {"redirect_to": session.redirect_to} if session.redirect_to else {}
        return get_url("/payment-failed" + ("?" + urlencode(params) if params else ""))

    return None


def apply_transaction(
    session_name: str,
    transaction: Optional[dict],
    via: str,
    account: Optional[str] = None,
) -> str:
    """
    Apply a Paystack transaction to a session under a row lock and commit.

    `account` is the setting that vouched for the transaction (the webhook's
    signing account, or the account used to verify it). Returns an OUTCOME_*.
    """
    from frappe_paystack.core.notify import notify_failure, payment_succeeded

    if not transaction:
        return OUTCOME_PENDING

    session = lock(session_name)
    tx = frappe._dict(transaction)
    metadata = tx.metadata if isinstance(tx.metadata, dict) else {}
    tx_status = (tx.status or "").lower()

    problem = None
    if not reference_belongs(session, tx.reference):
        problem = _("Paystack reference {0} does not belong to payment {1}.").format(tx.reference, session.name)
    elif metadata.get("session") and metadata.get("session") != session.name:
        problem = _("Paystack metadata names payment {0}, not {1}.").format(metadata.get("session"), session.name)
    elif account and session.gateway_setting and account != session.gateway_setting:
        # A different Paystack account vouched for this transaction: ignore it.
        log_error_for(
            title=f"Paystack: transaction for {session.name} came from another account",
            message=_("Account {0} reported a transaction for a payment owned by {1}.").format(
                account, session.gateway_setting
            ),
            reference_doctype=PAYMENT_LOG,
            reference_name=session.name,
        )
        frappe.db.commit()
        return OUTCOME_IGNORED

    if problem:
        log_error_for(title=f"Paystack: rejected transaction for {session.name}", message=problem,
                      reference_doctype=PAYMENT_LOG, reference_name=session.name)
        frappe.db.commit()
        return OUTCOME_IGNORED

    if tx_status == TX_SUCCESS:
        return _apply_success(session, tx, via, payment_succeeded)

    if tx_status == TX_FAILED:
        if session.status in (PENDING, EXPIRED, CANCELLED):
            message = tx.gateway_response or tx.message or _("The payment was declined.")
            session.db_set({"status": FAILED, "errors": message[:1000]}, update_modified=True)
            frappe.db.commit()
            notify_failure(session.name, message)
            return OUTCOME_FAILED
        frappe.db.commit()
        return OUTCOME_IGNORED

    if tx_status == TX_ABANDONED:
        if session.status == PENDING:
            session.db_set("status", CANCELLED, update_modified=True)
            frappe.db.commit()
            return OUTCOME_CANCELLED
        frappe.db.commit()
        return OUTCOME_IGNORED

    if tx_status == TX_REVERSED:
        if session.status in CAPTURED_STATUSES:
            session.db_set(
                {"status": NEEDS_ATTENTION, "errors": _("Paystack reversed transaction {0}.").format(tx.id)},
                update_modified=True,
            )
            log_error_for(title=f"Paystack: transaction reversed for {session.name}",
                          message=frappe.as_json(tx), reference_doctype=PAYMENT_LOG, reference_name=session.name)
        frappe.db.commit()
        return OUTCOME_MISMATCH

    # ongoing, pending, processing, queued, send_otp, ...: still in flight.
    frappe.db.commit()
    return OUTCOME_PENDING


def _apply_success(session: Any, tx: frappe._dict, via: str, payment_succeeded) -> str:
    transaction_id = str(tx.id or "")
    currency = money.clean_currency(tx.currency)

    if session.status in CAPTURED_STATUSES or session.status == NEEDS_ATTENTION and session.transaction_id:
        if transaction_id and transaction_id == str(session.transaction_id or ""):
            frappe.db.commit()
            return OUTCOME_ALREADY_PAID
        message = _("Paystack captured a second transaction ({0}) for payment {1}, which was already paid by {2}. Refund one of them.").format(
            transaction_id, session.name, session.transaction_id
        )
        session.db_set({"status": NEEDS_ATTENTION, "errors": message}, update_modified=True)
        log_error_for(title=f"Paystack: duplicate capture for {session.name}", message=message,
                      reference_doctype=PAYMENT_LOG, reference_name=session.name)
        frappe.db.commit()
        return OUTCOME_DUPLICATE

    expected_minor = money.to_minor(session.amount, session.currency)
    captured_minor = cint(tx.amount)
    mismatch = None
    if currency != money.clean_currency(session.currency):
        mismatch = _("Paystack charged {0} but this payment expects {1}.").format(currency or "?", session.currency)
    elif captured_minor != expected_minor:
        mismatch = _("Paystack captured {0} but this payment expects {1}.").format(
            money.format_amount(money.from_minor(captured_minor), currency),
            money.format_amount(session.amount, session.currency),
        )

    values = {
        "amount_paid": money.from_minor(captured_minor, currency),
        "currency_paid": currency or None,
        "transaction_id": transaction_id or None,
        "payment_reference": tx.reference,
        "payment_date": getdate(tx.paid_at or tx.paidAt or now_datetime()),
        "paystack_fee": money.from_minor(tx.fees or 0, currency),
        "channel": tx.channel,
        "completed_via": via,
        "completed_by": current_user(),
        "completed_at": now_datetime(),
    }
    if mismatch:
        values.update({"status": NEEDS_ATTENTION, "errors": mismatch})
        session.db_set(values, update_modified=True)
        log_error_for(title=f"Paystack: capture does not match payment {session.name}", message=mismatch,
                      reference_doctype=PAYMENT_LOG, reference_name=session.name)
        frappe.db.commit()
        return OUTCOME_MISMATCH

    values.update({"status": PAID, "errors": None})
    session.db_set(values, update_modified=True)
    frappe.db.commit()

    # Saved-card capture and broadcast run after the capture is durable.
    try:
        from frappe_paystack.core.authorizations import capture_from_transaction

        capture_from_transaction(session.name, tx)
    except Exception:
        log_error_for(title=f"Paystack: could not store the card used for {session.name}",
                      reference_doctype=PAYMENT_LOG, reference_name=session.name)
    payment_succeeded(session.name)
    return OUTCOME_PAID


def manual_verify(session_name: str) -> dict:
    """Desk action: verify with Paystack and (re)notify, as the session's run-as user."""
    return verify(session_name, via=VIA_MANUAL)
