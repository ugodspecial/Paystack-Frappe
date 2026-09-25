"""Sales Invoice events: a credit note refunds the Paystack payment it reverses (opt-in)."""

from __future__ import annotations

from typing import Any, Optional

import frappe
from frappe.utils import flt

from frappe_paystack.core.constants import PAYMENT_LOG, REFUNDABLE_STATUSES
from frappe_paystack.core.logging import record_failure
from frappe_paystack.integrations.erpnext import is_available
from frappe_paystack.integrations.erpnext.accounts import gateway_accounts, setting_for_company

REFUND_JOB = "frappe_paystack.integrations.erpnext.sales_invoice.refund_for_credit_note"


def on_submit(doc: Any, method: Optional[str] = None) -> None:
    """Queue the refund; it runs only once the credit note's submission has committed."""
    if not is_available() or not doc.get("is_return") or not doc.get("return_against"):
        return
    setting = setting_for_company(doc.get("company"))
    if not setting or not gateway_accounts(setting).auto_refund_on_credit_note:
        return
    frappe.enqueue(
        REFUND_JOB,
        credit_note=doc.name,
        queue="short",
        job_id=f"paystack-credit-note-{doc.name}",
        deduplicate=True,
        enqueue_after_commit=not frappe.flags.in_test,
        now=bool(frappe.flags.in_test),
    )


def refund_for_credit_note(credit_note: str) -> Optional[str]:
    from frappe_paystack.core.refunds import refund_payment, refundable_balance

    doc = frappe.get_doc("Sales Invoice", credit_note)
    session = frappe.db.get_value(
        PAYMENT_LOG,
        {"linked_doctype": "Sales Invoice", "linked_docname": doc.return_against,
         "status": ["in", list(REFUNDABLE_STATUSES)]},
        "name",
    )
    if not session:
        return None
    if frappe.db.exists("Paystack Refund Log", {"linked_doctype": "Sales Invoice", "linked_docname": doc.name}):
        return None
    balance = refundable_balance(frappe.get_doc(PAYMENT_LOG, session))
    amount = min(abs(flt(doc.get("base_grand_total"))), balance)
    if amount <= 0:
        return None
    try:
        return refund_payment(session, amount=amount, reason=f"Credit note {doc.name}", reference_doctype="Sales Invoice",
                              reference_docname=doc.name).name
    except Exception:
        record_failure(f"Paystack auto-refund failed for credit note {doc.name}", reference_doctype="Sales Invoice",
                       reference_name=doc.name)
        return None


# 15.x name (frappe_paystack.events.sales_invoice_on_submit).
sales_invoice_on_submit = on_submit
