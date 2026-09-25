"""
Scheduled safety nets. Each job is idempotent and bounded.

* verify_open_sessions: asks Paystack about checkouts that started but never
  reported back (a closed tab, a missed webhook), and expires stale links.
* retry_notifications: re-notifies consumers whose callback failed, with
  back-off, as the session's run-as user.
* redrive_webhooks: processes webhook events recorded but never processed.
* sync_refunds: polls refunds still pending or processing.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_days, add_to_date, get_datetime, now_datetime, nowdate

from frappe_paystack.core.constants import (
    CAPTURED_STATUSES,
    NOTIFY_FAILED,
    NOTIFY_PENDING,
    PAYMENT_LOG,
    PENDING,
    RETRYABLE_STATUSES,
    VIA_SWEEP,
)
from frappe_paystack.core.logging import log_error_for

# A checkout younger than this is still likely to report back by itself.
VERIFY_AFTER_MINUTES = 10
# How often one session may be re-verified.
VERIFY_EVERY_MINUTES = 60
VERIFY_LOOKBACK_DAYS = 3
BATCH = 50


def verify_open_sessions() -> list:
    from frappe_paystack.core.lifecycle import verify
    from frappe_paystack.core.session import expire_if_due, lock

    checked = []
    names = frappe.get_all(
        PAYMENT_LOG,
        filters={
            "status": PENDING,
            "attempt_count": [">", 0],
            "modified": ["<", add_to_date(now_datetime(), minutes=-VERIFY_AFTER_MINUTES)],
            "creation": [">", add_days(nowdate(), -VERIFY_LOOKBACK_DAYS)],
        },
        fields=["name", "last_verified_at"],
        order_by="last_verified_at asc",
        limit=BATCH,
    )
    for row in names:
        if row.last_verified_at and get_datetime(row.last_verified_at) > add_to_date(
            now_datetime(), minutes=-VERIFY_EVERY_MINUTES
        ):
            continue
        try:
            frappe.db.set_value(PAYMENT_LOG, row.name, "last_verified_at", now_datetime(), update_modified=False)
            frappe.db.commit()
            verify(row.name, via=VIA_SWEEP)
            checked.append(row.name)
        except Exception:
            frappe.db.rollback()
            log_error_for(title=f"Paystack: sweep could not verify {row.name}", reference_doctype=PAYMENT_LOG,
                          reference_name=row.name)

    # Links whose window closed: a started checkout is verified once more, then the link expires.
    for name in frappe.get_all(
        PAYMENT_LOG,
        filters={"status": ["in", list(RETRYABLE_STATUSES)], "expires_at": ["<", now_datetime()]},
        pluck="name",
        limit=BATCH * 4,
    ):
        try:
            session = lock(name)
            if session.status == PENDING and session.attempt_count and session.payment_reference:
                frappe.db.commit()
                verify(name, via=VIA_SWEEP)
                session = lock(name)
            expire_if_due(session)
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            log_error_for(title=f"Paystack: could not expire {name}", reference_doctype=PAYMENT_LOG, reference_name=name)
    return checked


def retry_notifications() -> list:
    from frappe_paystack.core.notify import notify_success, retry_due

    retried = []
    for name in frappe.get_all(
        PAYMENT_LOG,
        filters={
            "status": ["in", list(CAPTURED_STATUSES)],
            "notification_status": ["in", [NOTIFY_PENDING, NOTIFY_FAILED]],
            "modified": ["<", add_to_date(now_datetime(), minutes=-2)],
        },
        pluck="name",
        order_by="last_notification_attempt asc",
        limit=BATCH,
    ):
        session = frappe.get_doc(PAYMENT_LOG, name)
        if session.notification_status == NOTIFY_FAILED and not retry_due(session):
            continue
        try:
            notify_success(name)
            retried.append(name)
        except Exception:
            frappe.db.rollback()
            log_error_for(title=f"Paystack: notification retry crashed for {name}", reference_doctype=PAYMENT_LOG,
                          reference_name=name)
    return retried


def redrive_webhooks() -> list:
    from frappe_paystack.core.webhook import redrive_stuck_events

    return redrive_stuck_events()


def sync_refunds() -> list:
    from frappe_paystack.core.refunds import sync_open_refunds

    return sync_open_refunds()


def poll_settlements() -> list:
    from frappe_paystack.core.settlements import poll_settlements as poll

    return poll()


def run_daily_reconciliation() -> None:
    from frappe_paystack.core.reconciliation import run_daily_reconciliation as run

    run()


def every_ten_minutes() -> None:
    """The scheduler entry point for the frequent sweeps."""
    for job in (redrive_webhooks, verify_open_sessions, retry_notifications):
        try:
            job()
        except Exception:
            frappe.db.rollback()
            log_error_for(title=f"Paystack sweep {job.__name__} failed")
