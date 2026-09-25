"""
Journal Entries for Paystack payouts: net to the bank, fees to the fee account,
the gross out of the suspense account (15.x accounting).
"""

from __future__ import annotations

from typing import Any, Optional

import frappe
from frappe import _
from frappe.utils import add_days, flt, nowdate

from frappe_paystack.core.constants import GATEWAY_SETTING, SETTLEMENT
from frappe_paystack.core.logging import record_failure
from frappe_paystack.integrations.erpnext import is_available
from frappe_paystack.integrations.erpnext.accounts import gateway_accounts, set_if_field
from frappe_paystack.utils.sweep import run_sweep

BALANCE_TOLERANCE = 0.01
RETRY_LOOKBACK_DAYS = 30
RETRY_LIMIT = 50
SETTLEMENT_SWEEP = "settlement"


def on_settlement_recorded(settlement: Any) -> None:
    """`paystack_settlement_recorded`: stamp the company and book the payout."""
    if not is_available() or not frappe.get_meta(SETTLEMENT).has_field("journal_entry"):
        return
    settings = gateway_accounts(settlement.gateway_setting)
    if settings.company and not settlement.get("company"):
        set_if_field(SETTLEMENT, settlement.name, {"company": settings.company, "booking_status": "Pending"})
    post_settlement_entry(frappe.get_doc(SETTLEMENT, settlement.name))


def unpostable_reason(settlement: Any, settings: Any) -> Optional[str]:
    if not settings or not settings.company:
        return _("Paystack account {0} has no company.").format(settlement.gateway_setting)
    company_currency = frappe.get_cached_value("Company", settings.company, "default_currency")
    if settlement.currency != company_currency:
        return _("Paystack paid out in {0} but {1} books in {2}. Clear this payout by hand.").format(
            settlement.currency, settings.company, company_currency)
    missing = [label for field, label in (("suspense_account", _("Suspense Account")),
                                          ("settlement_bank_account", _("Settlement Bank Account")),
                                          ("paystack_fee_account", _("Paystack Fee Account"))) if not settings.get(field)]
    if missing:
        return _("Set {0} on Paystack account {1} before this payout can be booked.").format(", ".join(missing), settings.name)
    if flt(settlement.gross_amount) <= 0:
        return _("Paystack reports nothing settled on this payout.")
    shortfall = flt(settlement.gross_amount) - flt(settlement.total_fees) - flt(settlement.deductions) - flt(settlement.net_amount)
    if abs(shortfall) > BALANCE_TOLERANCE:
        return _("Paystack reports {0} settled less {1} in fees and {2} in deductions, which is not the {3} paid out. "
                 "Clear this payout by hand.").format(settlement.gross_amount, settlement.total_fees, settlement.deductions,
                                                      settlement.net_amount)
    return None


def build_settlement_entry(settlement: Any, settings: Any):
    cost_center = frappe.get_cached_value("Company", settings.company, "cost_center")
    entry = frappe.new_doc("Journal Entry")
    entry.voucher_type = "Journal Entry"
    entry.company = settings.company
    entry.posting_date = settlement.settlement_date
    entry.cheque_no = settlement.settlement_id
    entry.cheque_date = settlement.settlement_date
    entry.user_remark = f"Paystack settlement {settlement.settlement_id}"
    entry.append("accounts", {"account": settings.settlement_bank_account,
                              "debit_in_account_currency": flt(settlement.net_amount), "cost_center": cost_center})
    if flt(settlement.total_fees):
        entry.append("accounts", {"account": settings.paystack_fee_account,
                                  "debit_in_account_currency": flt(settlement.total_fees), "cost_center": cost_center})
    if flt(settlement.deductions):
        entry.append("accounts", {"account": settings.suspense_account,
                                  "debit_in_account_currency": flt(settlement.deductions), "cost_center": cost_center})
    entry.append("accounts", {"account": settings.suspense_account,
                              "credit_in_account_currency": flt(settlement.gross_amount), "cost_center": cost_center})
    return entry


def post_settlement_entry(settlement: Any) -> Optional[str]:
    if settlement.get("journal_entry"):
        return settlement.journal_entry
    settings = gateway_accounts(settlement.gateway_setting)
    reason = unpostable_reason(settlement, settings)
    if reason:
        frappe.db.set_value(SETTLEMENT, settlement.name, "errors", reason, update_modified=False)
        frappe.db.commit()  # nosemgrep
        return None
    entry = None
    try:
        entry = build_settlement_entry(settlement, settings)
        entry.flags.ignore_permissions = True
        entry.insert()
        entry.submit()
    except Exception:
        if entry and entry.name and frappe.db.get_value("Journal Entry", entry.name, "docstatus") == 0:
            frappe.delete_doc("Journal Entry", entry.name, force=True, ignore_permissions=True)
        set_if_field(SETTLEMENT, settlement.name, {"booking_status": "Failed"})
        frappe.db.set_value(SETTLEMENT, settlement.name, "errors",
                            record_failure(f"Paystack settlement journal entry failed for payout {settlement.name}",
                                           reference_doctype=SETTLEMENT, reference_name=settlement.name),
                            update_modified=False)
        frappe.db.commit()  # nosemgrep
        return None
    set_if_field(SETTLEMENT, settlement.name, {"journal_entry": entry.name, "booking_status": "Booked"})
    frappe.db.set_value(SETTLEMENT, settlement.name, "errors", None, update_modified=False)
    frappe.db.commit()  # nosemgrep
    return entry.name


def retry_unposted_settlements() -> None:
    """Scheduler: book payouts whose Journal Entry could not be raised (no-op without ERPNext)."""
    if not is_available() or not frappe.get_meta(SETTLEMENT).has_field("journal_entry"):
        return
    try:
        names = frappe.get_all(SETTLEMENT, filters={"journal_entry": ["in", ["", None]],
                                                    "creation": [">", add_days(nowdate(), -RETRY_LOOKBACK_DAYS)]},
                               pluck="name", order_by="creation asc", limit=RETRY_LIMIT)
    except Exception:
        frappe.log_error(title="Paystack settlement sweep: lookup failed", message=frappe.get_traceback())
        return
    run_sweep(SETTLEMENT_SWEEP, SETTLEMENT, "journal_entry", names,
              lambda name: post_settlement_entry(frappe.get_doc(SETTLEMENT, name)))


__all__ = ["GATEWAY_SETTING", "post_settlement_entry", "retry_unposted_settlements", "on_settlement_recorded"]
