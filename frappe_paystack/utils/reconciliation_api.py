"""Whitelisted reconciliation endpoints (generic; the desk page and form call these paths)."""

from collections import Counter
from typing import Optional

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import add_days, cint, now, today

from frappe_paystack.core.constants import GATEWAY_SETTING, PAYMENT_LOG, RECONCILIATION_LOG
from frappe_paystack.core.permissions import can_move_money
from frappe_paystack.core.reconciliation import MANUAL_OVERRIDE, ReconciliationEngine

MAX_REPORT_LIMIT = 500


def check_reconciliation_permission(write: bool = True) -> None:
    if write and can_move_money():
        return
    if not write and frappe.has_permission(RECONCILIATION_LOG, "read"):
        return
    frappe.throw(_("You are not permitted to access Paystack reconciliation data."), frappe.PermissionError)


@frappe.whitelist()
@rate_limit(limit=60, seconds=60)
def reconcile_payment(payment_log_name: str) -> dict:
    check_reconciliation_permission()
    setting = frappe.db.get_value(PAYMENT_LOG, payment_log_name, "gateway_setting")
    if not setting:
        frappe.throw(_("Payment {0} not found.").format(payment_log_name), frappe.DoesNotExistError)
    return ReconciliationEngine(setting).reconcile_payment(payment_log_name)


@frappe.whitelist()
@rate_limit(limit=5, seconds=60)
def run_reconciliation(date_from: Optional[str] = None, date_to: Optional[str] = None, setting: Optional[str] = None) -> dict:
    """Reconcile one account, or every enabled account, over a date range (default: last 30 days)."""
    check_reconciliation_permission()
    date_from = date_from or add_days(today(), -30)
    date_to = date_to or add_days(today(), 1)
    settings = [setting] if setting else frappe.get_all(GATEWAY_SETTING, filters={"enabled": 1}, pluck="name")
    totals = Counter()
    for name in settings:
        totals.update(ReconciliationEngine(name).run_full_reconciliation(date_from, date_to))
    return dict(totals)


@frappe.whitelist()
def mark_manual_override(payment_log_name: str, notes: str) -> dict:
    check_reconciliation_permission()
    if not (notes or "").strip():
        frappe.throw(_("A note explaining the override is required."))
    if not frappe.db.exists(RECONCILIATION_LOG, payment_log_name):
        frappe.throw(_("No reconciliation log for {0}.").format(payment_log_name), frappe.DoesNotExistError)
    log = frappe.get_doc(RECONCILIATION_LOG, payment_log_name)
    log.status = MANUAL_OVERRIDE
    log.notes = notes
    log.reconciled_by = frappe.session.user
    log.reconciled_at = now()
    log.flags.ignore_permissions = True
    log.save()
    return {"payment_log": payment_log_name, "status": log.status}


@frappe.whitelist()
def get_reconciliation_report(status: Optional[str] = None, limit: int = 100) -> list:
    check_reconciliation_permission(write=False)
    limit = min(max(cint(limit) or 100, 1), MAX_REPORT_LIMIT)
    filters = {"status": status} if status else {}
    return frappe.get_list(
        RECONCILIATION_LOG,
        filters=filters,
        fields=["name", "payment_log", "gateway_setting", "status", "paystack_amount", "frappe_amount", "difference",
                "mismatch_reason", "reconciled_at"],
        order_by="reconciled_at desc",
        limit_page_length=limit,
    )


@frappe.whitelist()
def get_reconciliation_stats(days: int = 30) -> dict:
    check_reconciliation_permission(write=False)
    rows = frappe.get_list(
        RECONCILIATION_LOG,
        filters={"reconciliation_date": [">=", add_days(today(), -cint(days or 30))]},
        fields=["status"],
        limit_page_length=0,
    )
    result = dict(Counter(row.status for row in rows))
    result["total"] = sum(result.values())
    return result
