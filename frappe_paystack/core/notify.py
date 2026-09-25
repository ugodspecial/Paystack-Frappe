"""
Telling the consuming app what happened to its payment.

The consumer is notified at most once per session, under a row lock, as the
session's `run_as_user` (decision D5). Consumer code runs inside a savepoint:
if it raises, its writes are rolled back, the gateway state is kept, and the
failure is recorded and retried with back-off by the sweep. After
NOTIFY_MAX_ATTEMPTS attempts the session's notification is marked Needs
Attention for a person to resolve (for example, when an administrator already
enrolled the learner by hand and the consumer refuses a duplicate).
"""

from __future__ import annotations

from typing import Any, Optional

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from frappe_paystack.core.adapters import get_adapter
from frappe_paystack.core.constants import (
    CAPTURED_STATUSES,
    HOOK_PAYMENT_FAILED,
    HOOK_PAYMENT_SUCCEEDED,
    NOTIFY_DONE,
    NOTIFY_FAILED,
    NOTIFY_MAX_ATTEMPTS,
    NOTIFY_NEEDS_ATTENTION,
    NOTIFY_NOT_REQUIRED,
    NOTIFY_PENDING,
    PAYMENT_LOG,
)
from frappe_paystack.core.logging import log_error_for
from frappe_paystack.core.session import lock, request_data
from frappe_paystack.core.users import GUEST, run_as

SAVEPOINT = "paystack_notify"

# Minutes before the first retry; doubles per attempt, capped.
RETRY_BASE_MINUTES = 5
RETRY_CAP_MINUTES = 12 * 60


def flags_data(session: Any) -> frappe._dict:
    """Return what the consumer reads from frappe.flags.data (Payments convention)."""
    data = request_data(session)
    data.setdefault("reference_doctype", session.linked_doctype)
    data.setdefault("reference_docname", session.linked_docname)
    data.setdefault("order_id", session.order_id or session.name)
    data.setdefault("amount", session.amount)
    data.setdefault("currency", session.currency)
    if not data.get("payment_gateway"):
        data["payment_gateway"] = session.payment_gateway or "Paystack"
    data.update(
        {
            "payment_session": session.name,
            "paystack_reference": session.payment_reference,
            "paystack_transaction_id": session.transaction_id,
            "amount_paid": session.amount_paid,
            "currency_paid": session.currency_paid,
            "status": session.status,
        }
    )
    return data


def notification_user(session: Any) -> str:
    adapter = get_adapter(session.linked_doctype)
    return adapter.notification_user(session) or session.run_as_user or session.owner or GUEST


def retry_delay_minutes(attempts: int) -> int:
    if attempts < 1:
        return 0
    return min(RETRY_BASE_MINUTES * 2 ** (attempts - 1), RETRY_CAP_MINUTES)


def retry_due(session: Any) -> bool:
    if not session.last_notification_attempt:
        return True
    due_at = add_to_date(
        get_datetime(session.last_notification_attempt),
        minutes=retry_delay_minutes(cint(session.notification_attempts)),
    )
    return due_at <= now_datetime()


def notify_success(session_name: str, force: bool = False) -> Optional[str]:
    """
    Notify the consumer of a captured payment, at most once.

    Returns the redirect the consumer asked for (a string return value of
    on_payment_authorized), if any. `force` skips the back-off (desk retries).
    Commits.
    """
    session = lock(session_name)

    if session.status not in CAPTURED_STATUSES:
        frappe.db.commit()
        return None

    if session.notification_status in (NOTIFY_DONE, NOTIFY_NOT_REQUIRED):
        frappe.db.commit()
        return session.consumer_redirect or None

    if not force and session.notification_status == NOTIFY_FAILED and not retry_due(session):
        frappe.db.commit()
        return None

    if not (session.linked_doctype and session.linked_docname):
        session.db_set("notification_status", NOTIFY_NOT_REQUIRED, update_modified=False)
        frappe.db.commit()
        return None

    adapter = get_adapter(session.linked_doctype)
    user = notification_user(session)
    previous_data = frappe.flags.get("data")
    redirect = None

    frappe.db.savepoint(SAVEPOINT)
    try:
        if not frappe.db.exists(session.linked_doctype, session.linked_docname):
            frappe.throw(
                _("{0} {1} no longer exists, so it could not be told about this payment.").format(
                    session.linked_doctype, session.linked_docname
                )
            )
        with run_as(user):
            frappe.flags.data = flags_data(session)
            reference_doc = frappe.get_doc(session.linked_doctype, session.linked_docname)
            reference_doc.flags.ignore_permissions = True
            result = adapter.notify_success(session, reference_doc)
            if isinstance(result, str) and result:
                redirect = result
            elif isinstance(result, dict) and result.get("redirect_to"):
                redirect = str(result.get("redirect_to"))
    except Exception as exc:
        _rollback_savepoint()
        _record_failure(session_name, exc)
        return None
    finally:
        frappe.flags.data = previous_data

    frappe.db.set_value(
        PAYMENT_LOG,
        session_name,
        {
            "notification_status": NOTIFY_DONE,
            "notification_attempts": cint(session.notification_attempts) + 1,
            "last_notification_attempt": now_datetime(),
            "notification_error": None,
            "notified_as": user,
            "consumer_redirect": redirect,
        },
        update_modified=False,
    )
    frappe.db.commit()
    return redirect


def _rollback_savepoint() -> None:
    try:
        frappe.db.rollback(save_point=SAVEPOINT)
    except Exception:
        # The consumer committed or ended the transaction itself; nothing to undo.
        pass


def _record_failure(session_name: str, exc: Exception) -> None:
    attempts = cint(frappe.db.get_value(PAYMENT_LOG, session_name, "notification_attempts")) + 1
    status = NOTIFY_NEEDS_ATTENTION if attempts >= NOTIFY_MAX_ATTEMPTS else NOTIFY_FAILED
    message = str(exc) or exc.__class__.__name__
    log_error_for(
        title=f"Paystack: consumer notification failed for {session_name}",
        message=frappe.get_traceback(),
        reference_doctype=PAYMENT_LOG,
        reference_name=session_name,
    )
    frappe.db.set_value(
        PAYMENT_LOG,
        session_name,
        {
            "notification_status": status,
            "notification_attempts": attempts,
            "last_notification_attempt": now_datetime(),
            "notification_error": frappe.utils.strip_html(message)[:1000],
        },
        update_modified=False,
    )
    frappe.db.commit()


def notify_failure(session_name: str, message: str) -> None:
    """Best-effort: tell the consumer a payment failed (no retries). Commits."""
    session = frappe.get_doc(PAYMENT_LOG, session_name)
    if session.linked_doctype and session.linked_docname and frappe.db.exists(
        session.linked_doctype, session.linked_docname
    ):
        adapter = get_adapter(session.linked_doctype)
        frappe.db.savepoint(SAVEPOINT)
        previous_data = frappe.flags.get("data")
        try:
            with run_as(notification_user(session)):
                frappe.flags.data = flags_data(session)
                reference_doc = frappe.get_doc(session.linked_doctype, session.linked_docname)
                adapter.notify_failure(session, reference_doc, message)
        except Exception:
            _rollback_savepoint()
            log_error_for(
                title=f"Paystack: failure notification raised for {session_name}",
                reference_doctype=PAYMENT_LOG,
                reference_name=session_name,
            )
        finally:
            frappe.flags.data = previous_data
    broadcast(HOOK_PAYMENT_FAILED, session)
    frappe.db.commit()


def broadcast(hook: str, doc: Any) -> None:
    """Call every app's `hook` handler with doc; a failing handler is logged, not raised."""
    for path in frappe.get_hooks(hook) or []:
        frappe.db.savepoint("paystack_broadcast")
        try:
            frappe.get_attr(path)(doc)
        except Exception:
            try:
                frappe.db.rollback(save_point="paystack_broadcast")
            except Exception:
                pass
            log_error_for(
                title=f"Paystack: {hook} handler {path} failed",
                reference_doctype=doc.doctype,
                reference_name=doc.name,
            )


def payment_succeeded(session_name: str) -> None:
    """Broadcast a new capture to `paystack_payment_succeeded` subscribers. Commits."""
    broadcast(HOOK_PAYMENT_SUCCEEDED, frappe.get_doc(PAYMENT_LOG, session_name))
    frappe.db.commit()


def mark_notification_resolved(session_name: str, note: str) -> None:
    """Close a notification a person has resolved by other means."""
    frappe.db.set_value(
        PAYMENT_LOG,
        session_name,
        {
            "notification_status": NOTIFY_NOT_REQUIRED,
            "notification_error": _("Resolved manually by {0}: {1}").format(frappe.session.user, note)[:1000],
        },
        update_modified=True,
    )


def reset_notification(session_name: str) -> None:
    frappe.db.set_value(
        PAYMENT_LOG,
        session_name,
        {"notification_status": NOTIFY_PENDING, "last_notification_attempt": None},
        update_modified=False,
    )
