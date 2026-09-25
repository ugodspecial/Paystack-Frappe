"""
Collecting ERPNext Subscription invoices from the customer's saved card.

ERPNext raises the invoices; this collects them daily for accounts that opted
in (auto_charge_subscriptions, which requires save_card_authorizations).
"""

from __future__ import annotations

from typing import Any, Optional

import frappe
from frappe.utils import add_days, nowdate

from frappe_paystack.core.constants import GATEWAY_SETTING, PAYMENT_LOG
from frappe_paystack.core.logging import log_error_for
from frappe_paystack.integrations.erpnext import is_available

SALES_INVOICE = "Sales Invoice"
COLLECTABLE_STATUSES = ("Unpaid", "Overdue", "Partly Paid")
CLAIMED_STATUSES = ("Pending", "Paid", "Needs Attention", "Partially Refunded", "Refunded")
COLLECTION_LOOKBACK_DAYS = 30
COLLECTION_LIMIT = 50


def auto_charge_settings() -> list:
    if not frappe.get_meta(GATEWAY_SETTING).has_field("auto_charge_subscriptions"):
        return []
    return frappe.get_all(GATEWAY_SETTING, filters={"enabled": 1, "auto_charge_subscriptions": 1},
                          fields=["name", "company"])


def collectable_invoices(company: str) -> list:
    return frappe.get_all(
        SALES_INVOICE,
        filters={"company": company, "docstatus": 1, "subscription": ["is", "set"], "status": ["in", list(COLLECTABLE_STATUSES)],
                 "outstanding_amount": [">", 0], "posting_date": [">", add_days(nowdate(), -COLLECTION_LOOKBACK_DAYS)]},
        pluck="name", order_by="posting_date asc", limit=COLLECTION_LIMIT,
    )


def has_open_collection(invoice: str) -> bool:
    return bool(frappe.db.exists(PAYMENT_LOG, {"linked_doctype": SALES_INVOICE, "linked_docname": invoice,
                                               "status": ["in", list(CLAIMED_STATUSES)]}))


def collectable_card(invoice: Any, setting: str) -> Optional[str]:
    from frappe_paystack.core.authorizations import usable_authorizations

    cards = usable_authorizations(party_type="Customer", party=invoice.customer, setting=setting)
    return cards[0].name if cards else None


def collect_invoice(invoice_name: str, setting: str) -> Optional[str]:
    from frappe_paystack.integrations.erpnext.api import charge_saved_card

    if has_open_collection(invoice_name):
        return None
    invoice = frappe.get_doc(SALES_INVOICE, invoice_name)
    card = collectable_card(invoice, setting)
    if not card:
        return None
    return charge_saved_card(SALES_INVOICE, invoice_name, card)


def collect_subscription_payments() -> None:
    """Scheduler (daily): charge outstanding subscription invoices (no-op without ERPNext)."""
    if not is_available():
        return
    session_user = frappe.session.user
    try:
        frappe.set_user("Administrator")  # nosemgrep - the scheduled collection needs a system user
        for row in auto_charge_settings():
            if not row.company:
                continue
            for invoice in collectable_invoices(row.company):
                try:
                    collect_invoice(invoice, row.name)
                except Exception:
                    frappe.db.rollback()
                    log_error_for(title=f"Paystack subscription collection failed: {invoice}", reference_doctype=SALES_INVOICE,
                                  reference_name=invoice)
    finally:
        frappe.set_user(session_user)  # nosemgrep
