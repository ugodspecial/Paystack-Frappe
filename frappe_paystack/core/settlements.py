"""
Paystack payouts (settlements): facts and fee linkage only.

A settlement is recorded from the (undocumented) settlement webhook or by the
daily poll of GET /settlement, which Paystack does document. Its transactions
are then linked to the sessions they paid out, with the fee Paystack kept.
Booking the payout in a ledger is not the gateway's job: subscribers to
`paystack_settlement_recorded` do that (the ERPNext adapter raises a Journal
Entry).
"""

from __future__ import annotations

from typing import Any, Optional

import frappe
from frappe.utils import add_days, flt, getdate, nowdate

from frappe_paystack.core import money
from frappe_paystack.core.client import client_for
from frappe_paystack.core.constants import GATEWAY_SETTING, HOOK_SETTLEMENT_RECORDED, PAYMENT_LOG, SETTLEMENT
from frappe_paystack.core.logging import log_error_for

MAX_PAGES = 50
PAGE_SIZE = 100
POLL_LOOKBACK_DAYS = 7
LINK_JOB = "frappe_paystack.core.settlements.link_transactions"


def payout_amounts(payout: dict) -> dict:
    """
    Map Paystack's settlement fields to gross, fees, deductions and net.

    Paystack reports total_processed (gross), total_fees, deductions and
    total_amount / effective_amount (what was paid out).
    """
    currency = money.clean_currency(payout.get("currency"))
    fees = money.from_minor(payout.get("total_fees") or 0)
    deductions = money.from_minor(payout.get("deductions") or 0)
    net = money.from_minor(payout.get("effective_amount") or payout.get("total_amount") or 0)
    if payout.get("total_processed") is not None:
        gross = money.from_minor(payout.get("total_processed") or 0)
    else:
        gross = flt(net + fees + deductions, 2)
    return {"currency": currency, "gross_amount": gross, "total_fees": fees, "deductions": deductions, "net_amount": net}


def record_settlement(payout: dict, setting: Any, source: str = "webhook") -> Optional[str]:
    """Insert (once) the Paystack Settlement a payout describes and return its name. Commits."""
    settlement_id = str(payout.get("id") or "")
    if not settlement_id:
        return None
    if frappe.db.exists(SETTLEMENT, settlement_id):
        return settlement_id

    settlement = frappe.new_doc(SETTLEMENT)
    settlement.update(
        {
            "settlement_id": settlement_id,
            "gateway_setting": setting.name,
            "paystack_status": str(payout.get("status") or ""),
            "settlement_date": getdate(payout.get("settlement_date") or payout.get("settled_at") or nowdate()),
            "source": source,
            **payout_amounts(payout),
        }
    )
    settlement.flags.ignore_permissions = True
    settlement.flags.ignore_links = True
    settlement.insert()
    frappe.db.commit()

    frappe.enqueue(
        LINK_JOB,
        queue="long",
        timeout=900,
        settlement_name=settlement.name,
        job_id=f"paystack-settlement-{settlement.name}",
        deduplicate=True,
        now=bool(frappe.flags.in_test),
    )
    return settlement.name


def record_from_webhook(obj: dict, setting: Any) -> str:
    name = record_settlement(obj, setting, source="webhook")
    return f"settlement {name}" if name else "settlement without id"


def link_transactions(settlement_name: str) -> int:
    """Stamp each captured session in a payout with the payout and its fee, then broadcast."""
    from frappe_paystack.core.notify import broadcast

    settlement = frappe.get_doc(SETTLEMENT, settlement_name)
    setting = frappe.get_doc(GATEWAY_SETTING, settlement.gateway_setting)
    client = client_for(setting, SETTLEMENT, settlement.name)

    linked = 0
    for page in range(1, MAX_PAGES + 1):
        try:
            rows = client.settlement_transactions(settlement.settlement_id, page=page, per_page=PAGE_SIZE)
        except Exception:
            log_error_for(title=f"Paystack: could not read transactions of payout {settlement.name}",
                          reference_doctype=SETTLEMENT, reference_name=settlement.name)
            break
        for row in rows or []:
            transaction_id = str(row.get("id") or "")
            if not transaction_id:
                continue
            session = frappe.db.get_value(
                PAYMENT_LOG, {"transaction_id": transaction_id, "gateway_setting": setting.name}, "name"
            )
            if not session:
                continue
            frappe.db.set_value(
                PAYMENT_LOG,
                session,
                {"settlement": settlement.name, "paystack_fee": money.from_minor(row.get("fees") or 0)},
                update_modified=False,
            )
            linked += 1
        if len(rows or []) < PAGE_SIZE:
            break

    settlement.db_set("transactions_linked", linked, update_modified=False)
    frappe.db.commit()
    broadcast(HOOK_SETTLEMENT_RECORDED, frappe.get_doc(SETTLEMENT, settlement.name))
    frappe.db.commit()
    return linked


def poll_settlements() -> list:
    """Record recent payouts from GET /settlement for every enabled account (webhook fallback)."""
    recorded = []
    date_from = add_days(nowdate(), -POLL_LOOKBACK_DAYS)
    for name in frappe.get_all(GATEWAY_SETTING, filters={"enabled": 1}, pluck="name"):
        setting = frappe.get_doc(GATEWAY_SETTING, name)
        try:
            for payout in client_for(setting).list_settlements(date_from=date_from) or []:
                if str(payout.get("status") or "").lower() != "success":
                    continue
                settlement = record_settlement(payout, setting, source="poll")
                if settlement:
                    recorded.append(settlement)
        except Exception:
            frappe.db.rollback()
            log_error_for(title=f"Paystack: settlement poll failed for {name}")
    return recorded
