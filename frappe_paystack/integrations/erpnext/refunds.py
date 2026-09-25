"""Reversal Payment Entries for processed Paystack refunds (15.x accounting)."""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import flt, getdate

from frappe_paystack.core.constants import PAYMENT_LOG, REFUND_LOG, REFUND_PROCESSED
from frappe_paystack.core.logging import record_failure
from frappe_paystack.integrations.erpnext import is_available
from frappe_paystack.integrations.erpnext.accounts import (
    discard_draft_payment_entry,
    gateway_accounts,
    party_account_for,
    party_account_rate,
    session_company,
    set_if_field,
)

SALES_INVOICE = "Sales Invoice"
REVERSIBLE_DOCTYPES = ("Sales Invoice", "Sales Order", "Dunning", "Payment Request", "POS Invoice")


def on_refund_updated(refund: Any) -> None:
    """`paystack_refund_updated`: stamp the company and book the reversal once processed."""
    if not is_available() or not frappe.get_meta(REFUND_LOG).has_field("reversal_payment_entry"):
        return
    session = frappe.get_doc(PAYMENT_LOG, refund.payment_log)
    if not refund.get("company"):
        set_if_field(REFUND_LOG, refund.name, {"company": session_company(session)})
    if refund.status == REFUND_PROCESSED and not refund.get("reversal_payment_entry"):
        book_reversal(refund.name)


def reference_document(refund: Any, session: Any):
    doctype = refund.linked_doctype or session.linked_doctype
    docname = refund.linked_docname or session.linked_docname
    if doctype == "Payment Request":
        doctype, docname = frappe.db.get_value("Payment Request", docname, ["reference_doctype", "reference_name"])
    if doctype not in REVERSIBLE_DOCTYPES or not docname or not frappe.db.exists(doctype, docname):
        return None
    return frappe.get_doc(doctype, docname)


def build_reversal_entry(refund: Any, inv: Any, settings: Any):
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

    party_account = party_account_for(inv)
    rate = party_account_rate(inv, party_account)
    party_amount = flt(flt(refund.refund_amount) / rate, inv.precision("grand_total"))
    if inv.doctype == SALES_INVOICE and inv.get("is_return"):
        pe = get_payment_entry(inv.doctype, inv.name, party_amount=-party_amount, bank_account=settings.suspense_account)
    else:
        pe = frappe.new_doc("Payment Entry")
        pe.payment_type = "Pay"
        pe.company = inv.company
        pe.posting_date = getdate()
        pe.party_type = "Customer"
        pe.party = inv.customer
        pe.paid_from = settings.suspense_account
        pe.paid_to = party_account
        pe.paid_amount = flt(refund.refund_amount)
        pe.received_amount = party_amount
    if rate != 1:
        pe.target_exchange_rate = rate
    return pe


def book_reversal(refund_name: str) -> None:
    session_user = frappe.session.user
    booked = None
    try:
        frappe.set_user("Administrator")  # nosemgrep - booking needs a system user
        refund = frappe.get_doc(REFUND_LOG, refund_name)
        session = frappe.get_doc(PAYMENT_LOG, refund.payment_log)
        inv = reference_document(refund, session)
        if not inv:
            return
        settings = gateway_accounts(session.gateway_setting)
        if not settings.suspense_account:
            frappe.throw(_("No Suspense Account on Paystack account {0}.").format(session.gateway_setting))
        pe = build_reversal_entry(refund, inv, settings)
        pe.mode_of_payment = settings.mode_of_payment
        pe.reference_date = getdate()
        pe.reference_no = refund.refund_reference or refund.name
        pe.remarks = f"Reversal for Paystack refund {refund.name}"
        pe.flags.ignore_permissions = True
        booked = pe
        pe.save()
        pe.submit()
        set_if_field(REFUND_LOG, refund.name, {"reversal_payment_entry": pe.name})
        frappe.db.commit()  # nosemgrep - the reversal is durable before the receipt is sent
        refund.reload()
        refund.send_refund_receipt_email()
    except Exception:
        discard_draft_payment_entry(booked)
        frappe.db.set_value(
            REFUND_LOG, refund_name, "errors",
            record_failure(f"Paystack reversal Payment Entry failed for refund {refund_name}", reference_doctype=REFUND_LOG,
                           reference_name=refund_name),
            update_modified=True,
        )
    finally:
        frappe.set_user(session_user)  # nosemgrep
