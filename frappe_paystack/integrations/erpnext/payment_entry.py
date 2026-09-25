"""
Payment Entries for captures collected through direct links (Sales Invoice,
Sales Order, Dunning), with the 15.x back-off, abandonment after 12 attempts
or 7 days, and the desk "Book Payment Entry" action.
"""

from __future__ import annotations

from typing import Any, Optional

import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, cint, flt, get_datetime, getdate, now_datetime, nowdate

from frappe_paystack.core.constants import CAPTURED_STATUSES, PAYMENT_LOG
from frappe_paystack.core.logging import log_error_for, record_failure
from frappe_paystack.integrations.erpnext import is_available
from frappe_paystack.integrations.erpnext.accounts import (
    discard_draft_payment_entry,
    gateway_accounts,
    party_account_for,
    party_account_rate,
    set_if_field,
)
from frappe_paystack.utils.sweep import run_sweep

BOOKABLE_DOCTYPES = ("Sales Invoice", "Sales Order", "Dunning")
POS_INVOICE = "POS Invoice"
PAYMENT_REQUEST = "Payment Request"
OUTSTANDING_DOCTYPES = ("Sales Invoice",)

BOOK_JOB = "frappe_paystack.integrations.erpnext.payment_entry.book_payment_entry"
COMPLETE_JOB = "frappe_paystack.integrations.erpnext.payment_entry.complete_payment_from_log"
COMPLETE_EVENT = "paystack_payment_completed"

RETRY_DELAY_MINUTES = 15
RETRY_LOOKBACK_DAYS = 7
RETRY_LIMIT = 50
BACKOFF_MINUTES = 10
BACKOFF_STEPS = 8
BACKOFF_CAP_MINUTES = 24 * 60
MAX_ATTEMPTS = 12
PAYMENT_SWEEP = "payment"
COMPLETION_LOCK_TTL = 900


def on_payment_succeeded(session: Any) -> None:
    """`paystack_payment_succeeded`: decide how ERPNext books a new capture."""
    if not is_available() or not frappe.get_meta(PAYMENT_LOG).has_field("booking_status"):
        return
    doctype = session.linked_doctype
    if doctype == PAYMENT_REQUEST:
        # Booked by the Payment Request adapter when the consumer is notified.
        if not session.get("booking_status"):
            set_if_field(PAYMENT_LOG, session.name, {"booking_status": "Pending"})
        return
    if doctype == POS_INVOICE:
        from frappe_paystack.integrations.erpnext.pos import notify_pos_link_paid

        set_if_field(PAYMENT_LOG, session.name, {"booking_status": "Not Required"})
        notify_pos_link_paid(session)
        return
    if doctype not in BOOKABLE_DOCTYPES:
        set_if_field(PAYMENT_LOG, session.name, {"booking_status": "Not Required"})
        return
    set_if_field(PAYMENT_LOG, session.name, {"booking_status": "Pending"})
    frappe.enqueue(
        BOOK_JOB,
        payment_log_name=session.name,
        queue="default",
        timeout=600,
        job_id=f"pe-{session.name}",
        deduplicate=True,
        now=bool(frappe.flags.in_test),
        enqueue_after_commit=not frappe.flags.in_test,
    )


def book_payment_entry(payment_log_name: str) -> Optional[str]:
    """Book the Payment Entry for a captured direct-link payment. Runs as Administrator."""
    session_user = frappe.session.user
    booked = None
    try:
        frappe.set_user("Administrator")  # nosemgrep - reading account balances needs a system user
        log = frappe.get_doc(PAYMENT_LOG, payment_log_name)
        if log.linked_doctype not in BOOKABLE_DOCTYPES or log.status not in CAPTURED_STATUSES:
            return None
        if log.get("payment_entry") or log.get("booking_status") in ("Booked", "Not Required"):
            return log.get("payment_entry")

        inv = frappe.get_doc(log.linked_doctype, log.linked_docname)
        if inv.doctype in OUTSTANDING_DOCTYPES and flt(inv.outstanding_amount) <= 0:
            set_if_field(PAYMENT_LOG, log.name, {"booking_status": "Not Required"})
            return None

        party_account = party_account_for(inv)
        rate = party_account_rate(inv, party_account)
        paid_amount = flt(flt(log.amount_paid) / rate, inv.precision("grand_total"))
        if paid_amount <= 0:
            return None

        settings = gateway_accounts(log.gateway_setting)
        if not settings.suspense_account:
            frappe.throw(_("Set a Suspense Account on Paystack account {0} to book its payments.").format(log.gateway_setting))

        from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

        pe = get_payment_entry(log.linked_doctype, log.linked_docname, party_amount=paid_amount,
                               bank_account=settings.suspense_account)
        pe.mode_of_payment = settings.mode_of_payment
        pe.reference_no = log.payment_reference or log.name
        pe.reference_date = log.payment_date or getdate()
        pe.remarks = f"Paystack payment {log.name}"
        if rate != 1:
            pe.source_exchange_rate = rate
        pe.flags.ignore_permissions = True
        booked = pe
        pe.save()
        pe.submit()
        set_if_field(PAYMENT_LOG, log.name, {"payment_entry": pe.name, "booking_status": "Booked"})
        return pe.name
    except Exception:
        discard_draft_payment_entry(booked)
        frappe.db.set_value(
            PAYMENT_LOG,
            payment_log_name,
            "errors",
            record_failure(f"Paystack Payment Entry creation failed for {payment_log_name}", reference_doctype=PAYMENT_LOG,
                           reference_name=payment_log_name),
            update_modified=False,
        )
        return None
    finally:
        frappe.set_user(session_user)  # nosemgrep - restores the caller


def backoff_minutes(retry_count: int) -> int:
    if cint(retry_count) < 1:
        return 0
    steps = min(cint(retry_count) - 1, BACKOFF_STEPS)
    return min(BACKOFF_MINUTES * 2**steps, BACKOFF_CAP_MINUTES)


def retry_due(row: Any) -> bool:
    if not row.last_retry:
        return True
    return get_datetime(row.last_retry) <= add_to_date(now_datetime(), minutes=-backoff_minutes(row.retry_count))


def unbooked(extra: Optional[dict] = None, limit: int = RETRY_LIMIT, fields=None) -> list:
    filters = {
        "status": ["in", list(CAPTURED_STATUSES)],
        "booking_status": "Pending",
        "linked_doctype": ["in", list(BOOKABLE_DOCTYPES)],
        "payment_entry": ["in", ["", None]],
        **(extra or {}),
    }
    return frappe.get_all(PAYMENT_LOG, filters=filters, fields=fields or ["name", "retry_count", "last_retry"],
                          order_by="last_retry asc, creation asc", limit=limit)


def record_retry(name: str) -> None:
    frappe.db.set_value(
        PAYMENT_LOG, name,
        {"retry_count": cint(frappe.db.get_value(PAYMENT_LOG, name, "retry_count")) + 1, "last_retry": now_datetime()},
        update_modified=False,
    )
    frappe.db.commit()  # nosemgrep - the attempt is counted before it can crash


def abandon(name: str, cause: str) -> None:
    set_if_field(PAYMENT_LOG, name, {"booking_status": "Needs Attention"})
    frappe.db.commit()  # nosemgrep - durable before the alert
    log_error_for(
        title=f"Paystack booking abandoned for {name}",
        message=_("{0} Last recorded error: {1}").format(cause, frappe.db.get_value(PAYMENT_LOG, name, "errors") or _("none")),
        reference_doctype=PAYMENT_LOG,
        reference_name=name,
    )


def drive_retry(name: str) -> None:
    record_retry(name)
    book_payment_entry(name)
    if frappe.db.get_value(PAYMENT_LOG, name, "payment_entry"):
        frappe.db.set_value(PAYMENT_LOG, name, {"retry_count": 0, "last_retry": None}, update_modified=False)
        frappe.db.commit()  # nosemgrep
    elif cint(frappe.db.get_value(PAYMENT_LOG, name, "retry_count")) >= MAX_ATTEMPTS:
        abandon(name, _("{0} booking attempts booked nothing.").format(MAX_ATTEMPTS))


def retry_stuck_bookings() -> None:
    """Scheduler: re-drive captures whose Payment Entry was never booked (no-op without ERPNext)."""
    if not is_available() or not frappe.get_meta(PAYMENT_LOG).has_field("booking_status"):
        return
    try:
        for row in unbooked({"creation": ["<=", add_days(nowdate(), -RETRY_LOOKBACK_DAYS)]}):
            abandon(row.name, _("No Payment Entry was booked within {0} days of this capture.").format(RETRY_LOOKBACK_DAYS))
        names = [
            row.name
            for row in unbooked({"modified": ["<", add_to_date(now_datetime(), minutes=-RETRY_DELAY_MINUTES)]})
            if retry_due(row) and not completion_held(row.name)
        ]
    except Exception:
        frappe.log_error(title="Paystack payment sweep: lookup failed", message=frappe.get_traceback())
        return
    run_sweep(PAYMENT_SWEEP, PAYMENT_LOG, "payment_entry", names, drive_retry)


# ---------------------------------------------------------------------------
# Manual completion from the desk
# ---------------------------------------------------------------------------


def completion_key(name: str) -> str:
    return frappe.cache.make_key(f"paystack-complete:{name}")


def hold_completion(name: str) -> bool:
    return bool(frappe.cache.set(completion_key(name), frappe.session.user, ex=COMPLETION_LOCK_TTL, nx=True))  # nosemgrep


def completion_held(name: str) -> bool:
    return bool(frappe.cache.get(completion_key(name)))  # nosemgrep


def release_completion(name: str) -> None:
    frappe.cache.delete(completion_key(name))


def is_completable(log: Any) -> bool:
    return (
        log.status in CAPTURED_STATUSES
        and log.linked_doctype in BOOKABLE_DOCTYPES
        and log.get("booking_status") in ("Pending", "Needs Attention", None, "")
        and not log.get("payment_entry")
    )


@frappe.whitelist()
def complete_payment(payment_log_name: str) -> str:
    """Queue verification with Paystack and booking of a capture's Payment Entry."""
    from frappe_paystack.core.permissions import check_money_permission

    check_money_permission(_("You are not permitted to complete Paystack payments."))
    log = frappe.get_doc(PAYMENT_LOG, payment_log_name)
    log.check_permission("read")
    if not is_completable(log):
        frappe.throw(_("{0} has no outstanding booking to complete.").format(payment_log_name))
    if not hold_completion(payment_log_name):
        frappe.throw(_("A completion of {0} is already running.").format(payment_log_name))
    queued = frappe.enqueue(
        COMPLETE_JOB, payment_log_name=payment_log_name, user=frappe.session.user, queue="long", timeout=600,
        job_id=f"paystack-complete-{payment_log_name}", deduplicate=True, now=bool(frappe.flags.in_test),
    )
    if queued is None and not frappe.flags.in_test:
        release_completion(payment_log_name)
        frappe.throw(_("A completion of {0} is already queued.").format(payment_log_name))
    return payment_log_name


def publish_completion(name: str, user: str, message: str, payment_entry: Optional[str]) -> None:
    frappe.publish_realtime(COMPLETE_EVENT, {"log": name, "booked": bool(payment_entry), "payment_entry": payment_entry,
                                             "message": message}, user=user)


def complete_payment_from_log(payment_log_name: str, user: str) -> None:
    from frappe_paystack.core.reconciliation import ReconciliationEngine

    try:
        log = frappe.get_doc(PAYMENT_LOG, payment_log_name)
        if not is_completable(log):
            publish_completion(payment_log_name, user, _("Nothing to complete."), log.get("payment_entry"))
            return
        engine = ReconciliationEngine(log.gateway_setting)
        status, reason, _paystack, _recorded = engine.compare(log, engine.lookup_transaction(log))
        if status != "Reconciled":
            frappe.db.set_value(PAYMENT_LOG, payment_log_name, "errors", reason, update_modified=False)
            frappe.db.commit()  # nosemgrep
            publish_completion(payment_log_name, user, reason, None)
            return
        set_if_field(PAYMENT_LOG, payment_log_name, {"booking_status": "Pending"})
        run_sweep(PAYMENT_SWEEP, PAYMENT_LOG, "payment_entry", [payment_log_name], book_payment_entry)
        payment_entry = frappe.db.get_value(PAYMENT_LOG, payment_log_name, "payment_entry")
        message = (_("Payment Entry {0} booked.").format(payment_entry) if payment_entry
                   else frappe.db.get_value(PAYMENT_LOG, payment_log_name, "errors") or _("No Payment Entry was booked."))
        publish_completion(payment_log_name, user, message, payment_entry)
    except Exception:
        reason = record_failure(f"Paystack manual completion failed for {payment_log_name}", reference_doctype=PAYMENT_LOG,
                                reference_name=payment_log_name)
        frappe.db.set_value(PAYMENT_LOG, payment_log_name, "errors", reason, update_modified=False)
        frappe.db.commit()  # nosemgrep
        publish_completion(payment_log_name, user, reason, None)
    finally:
        release_completion(payment_log_name)
