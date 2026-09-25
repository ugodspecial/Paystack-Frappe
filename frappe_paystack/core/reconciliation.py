"""
Gateway-level reconciliation: what Paystack says against what the session holds.

Ledger checks (a capture with no Payment Entry, a payout with no Journal Entry)
belong to accounting adapters, which subscribe through their own jobs.
"""

from __future__ import annotations

from typing import Any, Optional

import frappe
from frappe import _
from frappe.utils import add_days, flt, now, today

from frappe_paystack.core import money
from frappe_paystack.core.client import client_for
from frappe_paystack.core.constants import (
    CAPTURED_STATUSES,
    GATEWAY_SETTING,
    NEEDS_ATTENTION,
    PAYMENT_LOG,
    PENDING,
    RECONCILIATION_LOG,
    TX_SUCCESS,
)

AMOUNT_TOLERANCE = 0.01
MANUAL_OVERRIDE = "Manual Override"
PROTECTED_STATUSES = (MANUAL_OVERRIDE,)
RECONCILABLE_STATUSES = (PENDING, NEEDS_ATTENTION) + CAPTURED_STATUSES
DAILY_LOOKBACK_DAYS = 2


class ReconciliationEngine:
    """Reconcile one Paystack account's sessions against the Paystack API."""

    def __init__(self, setting: Optional[str] = None) -> None:
        if not setting:
            from frappe_paystack.core.accounts import default_setting_name

            setting = default_setting_name()
        if not setting or not frappe.db.exists(GATEWAY_SETTING, setting):
            frappe.throw(_("No enabled Paystack account to reconcile."), frappe.ValidationError)
        self.setting = frappe.get_doc(GATEWAY_SETTING, setting)
        self.client = client_for(self.setting)

    def run_full_reconciliation(self, date_from: str, date_to: str) -> dict:
        names = frappe.get_all(
            PAYMENT_LOG,
            filters={
                "gateway_setting": self.setting.name,
                "status": ["in", list(RECONCILABLE_STATUSES)],
                "creation": ["between", [date_from, date_to]],
            },
            pluck="name",
            order_by="creation asc",
        )
        tally = {"reconciled": 0, "mismatched": 0, "pending": 0, "errored": 0}
        for name in names:
            try:
                result = self.reconcile_payment(name)
            except Exception:
                frappe.db.rollback()
                frappe.log_error(title=f"Paystack reconciliation aborted for {name}", message=frappe.get_traceback())
                tally["errored"] += 1
                continue
            key = {"Reconciled": "reconciled", "Mismatch": "mismatched", "Skipped": None}.get(result["status"], "pending")
            if key:
                tally[key] += 1
            frappe.db.commit()
        return {"total": len(names), **tally}

    def lookup_transaction(self, session: Any) -> Optional[dict]:
        if session.transaction_id:
            return self.client.fetch_transaction(session.transaction_id)
        if session.payment_reference:
            return self.client.verify_transaction(session.payment_reference)
        return None

    def compare(self, session: Any, transaction: Optional[dict]) -> tuple:
        """Return (status, reason, paystack_amount, recorded_amount)."""
        recorded = flt(session.amount_paid) - flt(session.total_refunded)
        if transaction is None:
            status = "Mismatch" if session.status in CAPTURED_STATUSES else "Pending"
            return status, _("Paystack has no transaction for this payment."), 0.0, recorded

        tx_status = str(transaction.get("status") or "").lower()
        paystack_amount = money.from_minor(transaction.get("amount") or 0)
        currency = money.clean_currency(transaction.get("currency"))
        expected_currency = money.clean_currency(session.currency_paid or session.currency)

        if tx_status != TX_SUCCESS:
            status = "Mismatch" if session.status in CAPTURED_STATUSES else "Pending"
            return status, _("Paystack reports the transaction as '{0}'.").format(tx_status or "unknown"), paystack_amount, recorded
        if session.status not in CAPTURED_STATUSES:
            return "Mismatch", _("Paystack captured this payment but it is recorded as {0}.").format(session.status), paystack_amount, recorded
        if currency and expected_currency and currency != expected_currency:
            return "Mismatch", _("Currency differs: Paystack {0}, recorded {1}.").format(currency, expected_currency), paystack_amount, recorded
        if abs(paystack_amount - flt(session.amount_paid)) >= AMOUNT_TOLERANCE:
            return "Mismatch", _("Amount differs: Paystack {0}, recorded {1}.").format(paystack_amount, flt(session.amount_paid)), paystack_amount, recorded
        return "Reconciled", None, paystack_amount, recorded

    def reconcile_payment(self, session_name: str) -> dict:
        if not frappe.db.exists(PAYMENT_LOG, session_name):
            frappe.throw(_("Payment {0} not found.").format(session_name), frappe.DoesNotExistError)
        if frappe.db.get_value(RECONCILIATION_LOG, session_name, "status") in PROTECTED_STATUSES:
            return {"payment_log": session_name, "status": "Skipped"}

        session = frappe.get_doc(PAYMENT_LOG, session_name)
        try:
            transaction = self.lookup_transaction(session)
        except Exception as exc:
            frappe.log_error(title=f"Paystack reconciliation lookup failed: {session_name}", message=frappe.get_traceback())
            return self.write(session, None, flt(session.amount_paid), "Pending", str(exc))

        status, reason, paystack_amount, recorded = self.compare(session, transaction)
        # The recorded side is net of refunds; so is Paystack's for comparison.
        if status == "Reconciled":
            paystack_amount = flt(paystack_amount) - flt(session.total_refunded)
        return self.write(session, paystack_amount, recorded, status, reason)

    def write(self, session: Any, paystack_amount: Optional[float], recorded: float, status: str, reason: Optional[str]) -> dict:
        if frappe.db.exists(RECONCILIATION_LOG, session.name):
            log = frappe.get_doc(RECONCILIATION_LOG, session.name)
        else:
            log = frappe.new_doc(RECONCILIATION_LOG)
            log.payment_log = session.name
        log.update(
            {
                "gateway_setting": session.gateway_setting,
                "reconciliation_date": today(),
                "paystack_amount": max(flt(paystack_amount), 0),
                "frappe_amount": max(flt(recorded), 0),
                "status": status,
                "mismatch_reason": reason or "",
                "reconciled_by": frappe.session.user,
                "reconciled_at": now(),
            }
        )
        log.flags.ignore_permissions = True
        log.save()
        return {"payment_log": session.name, "status": status, "reason": reason}


def run_daily_reconciliation() -> None:
    for name in frappe.get_all(GATEWAY_SETTING, filters={"enabled": 1}, pluck="name"):
        try:
            ReconciliationEngine(name).run_full_reconciliation(add_days(today(), -DAILY_LOOKBACK_DAYS), add_days(today(), 1))
        except Exception:
            frappe.db.rollback()
            frappe.log_error(title=f"Paystack daily reconciliation failed: {name}", message=frappe.get_traceback())
