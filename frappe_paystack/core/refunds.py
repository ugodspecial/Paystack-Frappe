"""
Refunds, independent of any accounting system.

A refund is requested against a captured session, recorded as a Paystack
Refund Log and moves through Paystack's refund states (pending, processing,
processed, failed) by webhook or by the refund sweep, which polls Paystack.
Only processed refunds count towards the session's `total_refunded`; pending
and processing ones still hold part of the refundable balance, so a refund can
never exceed what was captured. Subscribers to `paystack_refund_updated` (for
example the ERPNext adapter, which books the reversal Payment Entry) are told
of every status change.
"""

from __future__ import annotations

from typing import Any, Optional

import frappe
from frappe import _
from frappe.query_builder.functions import Sum
from frappe.utils import add_days, flt, now_datetime, nowdate

from frappe_paystack.core import money
from frappe_paystack.core.adapters import get_adapter
from frappe_paystack.core.client import client_for
from frappe_paystack.core.constants import (
    GATEWAY_SETTING,
    HOOK_REFUND_UPDATED,
    PAID,
    PARTIALLY_REFUNDED,
    PAYMENT_LOG,
    PAYSTACK_REFUND_STATUS_MAP,
    REFUND_FAILED,
    REFUND_LOG,
    REFUND_OPEN_STATUSES,
    REFUND_PENDING,
    REFUND_PROCESSED,
    REFUNDABLE_STATUSES,
    REFUNDED,
)
from frappe_paystack.core.logging import log_error_for, record_failure
from frappe_paystack.core.session import lock

SYNC_LOOKBACK_DAYS = 30
SYNC_LIMIT = 100


def total_refunded(session_name: str, statuses=(REFUND_PROCESSED,), exclude: Optional[str] = None) -> float:
    """Sum refund_amount over a session's Refund Logs in the given statuses."""
    table = frappe.qb.DocType(REFUND_LOG)
    query = (
        frappe.qb.from_(table)
        .select(Sum(table.refund_amount))
        .where(table.payment_log == session_name)
        .where(table.status.isin(list(statuses)))
    )
    if exclude:
        query = query.where(table.name != exclude)
    total = query.run()[0][0]
    return flt(total) if total else 0.0


def refundable_balance(session: Any, exclude: Optional[str] = None) -> float:
    """What can still be refunded: captured, less processed and in-flight refunds."""
    held = total_refunded(session.name, statuses=(REFUND_PROCESSED,) + REFUND_OPEN_STATUSES, exclude=exclude)
    return flt(flt(session.amount_paid) - held, 2)


def session_refund_status(amount_paid: float, refunded: float) -> str:
    if flt(refunded) <= 0:
        return PAID
    if flt(refunded) >= flt(amount_paid):
        return REFUNDED
    return PARTIALLY_REFUNDED


def refund_payment(
    session_name: str,
    amount: Optional[float] = None,
    reason: Optional[str] = None,
    reference_doctype: Optional[str] = None,
    reference_docname: Optional[str] = None,
    customer_note: Optional[str] = None,
) -> Any:
    """
    Refund part or all of a captured payment and return the Paystack Refund Log.

    The amount is in the charge currency (the session's currency_paid), in major
    units; None refunds the whole remaining balance. `reference_*` names the
    business document that caused the refund (e.g. a credit note). Commits.
    Permission checks are the caller's responsibility (see core.permissions).
    """
    session = lock(session_name)
    if session.status not in REFUNDABLE_STATUSES:
        frappe.throw(_("Only captured payments can be refunded. {0} is {1}.").format(session.name, session.status))
    if not session.transaction_id:
        frappe.throw(_("Payment {0} has no Paystack transaction to refund.").format(session.name))

    currency = money.clean_currency(session.currency_paid or session.currency)
    balance = refundable_balance(session)
    amount = balance if amount in (None, "") else flt(amount)
    if amount <= 0:
        frappe.throw(_("The refund amount must be greater than zero."))
    if amount - balance > 0.005:
        frappe.throw(
            _("The refund of {0} exceeds the refundable balance of {1}.").format(
                money.format_amount(amount, currency), money.format_amount(balance, currency)
            )
        )

    refund = frappe.get_doc(
        {
            "doctype": REFUND_LOG,
            "payment_log": session.name,
            "linked_doctype": reference_doctype or session.linked_doctype,
            "linked_docname": reference_docname or session.linked_docname,
            "transaction_id": session.transaction_id,
            "refund_amount": amount,
            "currency": currency,
            "status": REFUND_PENDING,
            "refund_reason": reason or _("Refund"),
            "requested_by": frappe.session.user,
        }
    )
    refund.flags.ignore_permissions = True
    refund.flags.ignore_links = True
    refund.insert()
    frappe.db.commit()

    setting = frappe.get_doc(GATEWAY_SETTING, session.gateway_setting)
    try:
        data = client_for(setting, REFUND_LOG, refund.name).create_refund(
            transaction=session.transaction_id,
            amount_minor=money.to_minor(amount, currency),
            currency=currency,
            merchant_note=reason,
            customer_note=customer_note,
        )
    except Exception:
        frappe.db.rollback()
        refund.db_set(
            {
                "status": REFUND_FAILED,
                "errors": record_failure(
                    f"Paystack refund failed for {session.name}", reference_doctype=REFUND_LOG, reference_name=refund.name
                ),
            }
        )
        frappe.db.commit()
        refund.reload()
        _refund_changed(refund)
        return refund

    apply_refund_data(refund.name, data)
    refund.reload()
    return refund


def apply_refund_data(refund_name: str, data: dict) -> str:
    """Record Paystack's view of a refund on its log and propagate the change. Commits."""
    data = data or {}
    refund = frappe.get_doc(REFUND_LOG, refund_name)
    new_status = PAYSTACK_REFUND_STATUS_MAP.get(str(data.get("status") or "").lower(), refund.status)

    # A final status is never walked back by a late or out-of-order event.
    if refund.status in (REFUND_PROCESSED, REFUND_FAILED) and new_status != refund.status:
        return refund.status

    values = {"status": new_status, "raw_response": frappe.as_json(_trim(data))}
    if data.get("id"):
        values["refund_reference"] = str(data.get("id"))
    if new_status == REFUND_PROCESSED and not refund.processed_at:
        values["processed_at"] = now_datetime()
    changed = new_status != refund.status or not refund.refund_reference
    refund.db_set(values, update_modified=True)
    frappe.db.commit()

    if changed:
        refund.reload()
        _refund_changed(refund)
    return new_status


def _trim(data: dict) -> dict:
    """Keep the refund payload small; the transaction object echoes card details."""
    from frappe_paystack.core.logging import redact

    clean = dict(data)
    transaction = clean.get("transaction")
    if isinstance(transaction, dict):
        clean["transaction"] = {k: transaction.get(k) for k in ("id", "reference", "amount", "currency", "status")}
    return redact(clean)


def _refund_changed(refund: Any) -> None:
    """Update the session's refund totals and status, then tell adapters and subscribers."""
    from frappe_paystack.core.notify import broadcast

    session = lock(refund.payment_log)
    refunded = total_refunded(session.name)
    values = {"total_refunded": refunded}
    if session.status in REFUNDABLE_STATUSES + (REFUNDED,):
        values["status"] = session_refund_status(session.amount_paid, refunded)
    session.db_set(values, update_modified=False)
    frappe.db.commit()

    if session.linked_doctype:
        try:
            get_adapter(session.linked_doctype).on_refund(refund)
        except Exception:
            log_error_for(title=f"Paystack: refund adapter failed for {refund.name}", reference_doctype=REFUND_LOG,
                          reference_name=refund.name)
    broadcast(HOOK_REFUND_UPDATED, refund)
    frappe.db.commit()


def refund_references(obj: dict) -> tuple:
    """Return (refund id, transaction reference, transaction id) from a refund webhook payload."""
    transaction = obj.get("transaction") if isinstance(obj.get("transaction"), dict) else {}
    refund_id = obj.get("id") or obj.get("refund_reference")
    transaction_reference = transaction.get("reference") or obj.get("transaction_reference")
    transaction_id = transaction.get("id") or (obj.get("transaction") if not isinstance(obj.get("transaction"), dict) else None)
    return (str(refund_id) if refund_id else None, transaction_reference, str(transaction_id) if transaction_id else None)


def find_refund(obj: dict, setting: Any) -> Optional[str]:
    """
    Return the Refund Log a refund payload belongs to, creating one for a
    refund started outside this site (e.g. from the Paystack dashboard).

    Refund webhooks carry the transaction reference and a processor
    reference, not always the refund id, so after an id match the newest open
    refund on the payment is used, preferring one for the same amount.
    """
    refund_id, transaction_reference, transaction_id = refund_references(obj)
    if obj.get("id"):
        name = frappe.db.get_value(REFUND_LOG, {"refund_reference": str(obj.get("id"))}, "name")
        if name:
            return name

    session = None
    if transaction_id:
        session = frappe.db.get_value(PAYMENT_LOG, {"transaction_id": transaction_id}, "name")
    if not session and transaction_reference:
        from frappe_paystack.core.session import session_for_reference

        session = session_for_reference(transaction_reference)
    if not session:
        return None
    if frappe.db.get_value(PAYMENT_LOG, session, "gateway_setting") not in (None, "", setting.name):
        return None

    amount = money.from_minor(obj.get("amount") or 0)
    candidates = frappe.get_all(
        REFUND_LOG,
        filters={"payment_log": session, "status": ["in", list(REFUND_OPEN_STATUSES)]},
        fields=["name", "refund_amount"],
        order_by="creation desc",
    )
    for row in candidates:
        if amount and abs(flt(row.refund_amount) - amount) < 0.005:
            return row.name
    if candidates:
        return candidates[0].name

    # A redelivered final event for a refund already settled.
    if amount:
        for row in frappe.get_all(
            REFUND_LOG,
            filters={"payment_log": session, "status": ["in", [REFUND_PROCESSED, REFUND_FAILED]]},
            fields=["name", "refund_amount"],
            order_by="creation desc",
        ):
            if abs(flt(row.refund_amount) - amount) < 0.005:
                return row.name

    session_doc = frappe.get_doc(PAYMENT_LOG, session)
    refund = frappe.get_doc(
        {
            "doctype": REFUND_LOG,
            "payment_log": session,
            "linked_doctype": session_doc.linked_doctype,
            "linked_docname": session_doc.linked_docname,
            "transaction_id": session_doc.transaction_id,
            "refund_amount": amount or session_doc.amount_paid,
            "currency": money.clean_currency(obj.get("currency") or session_doc.currency_paid or session_doc.currency),
            "status": REFUND_PENDING,
            "refund_reference": str(obj.get("id")) if obj.get("id") else None,
            "refund_reason": _("Refund started outside this site"),
        }
    )
    refund.flags.ignore_permissions = True
    refund.flags.ignore_links = True
    refund.flags.ignore_balance_check = True
    refund.insert()
    frappe.db.commit()
    return refund.name


def apply_refund_event(event: str, obj: dict, setting: Any) -> str:
    """Handle refund.pending / processing / processed / failed."""
    refund_name = find_refund(obj, setting)
    if not refund_name:
        return f"no refund for {obj.get('id') or obj.get('refund_reference')}"
    data = dict(obj)
    data["status"] = obj.get("status") or event.split(".", 1)[1]
    return apply_refund_data(refund_name, data)


def sync_open_refunds() -> list:
    """Poll Paystack for refunds still pending or processing (covers missed webhooks)."""
    names = frappe.get_all(
        REFUND_LOG,
        filters={
            "status": ["in", list(REFUND_OPEN_STATUSES)],
            "refund_reference": ["is", "set"],
            "creation": [">", add_days(nowdate(), -SYNC_LOOKBACK_DAYS)],
        },
        pluck="name",
        limit=SYNC_LIMIT,
    )
    for name in names:
        try:
            refund = frappe.get_doc(REFUND_LOG, name)
            setting_name = frappe.db.get_value(PAYMENT_LOG, refund.payment_log, "gateway_setting")
            if not setting_name:
                continue
            setting = frappe.get_doc(GATEWAY_SETTING, setting_name)
            data = client_for(setting, REFUND_LOG, name).fetch_refund(refund.refund_reference)
            if data:
                apply_refund_data(name, data)
        except Exception:
            frappe.db.rollback()
            log_error_for(title=f"Paystack: refund sync failed for {name}", reference_doctype=REFUND_LOG, reference_name=name)
    return names
