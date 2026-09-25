# Copyright (c) 2026, Anthony Emmanuel and contributors
# For license information, please see license.txt

"""A refund against a captured payment. Created by frappe_paystack.core.refunds."""

from typing import Any

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, fmt_money

from frappe_paystack.core.constants import (
    PAYMENT_LOG,
    REFUND_LOG,
    REFUND_PROCESSED,
    REFUND_STATUSES,
    REFUNDABLE_STATUSES,
)

REFUND_RECEIPT_PRINT_FORMAT = "Paystack Refund Receipt"

# Upstream (15.x) names, kept for code that imported them.
REFUNDABLE_LOG_STATUSES = REFUNDABLE_STATUSES


class PaystackRefundLog(Document):
    def validate(self) -> None:
        if self.status not in REFUND_STATUSES:
            frappe.throw(_("Invalid status value: {0}.").format(self.status))
        if not self.payment_log or not frappe.db.exists(PAYMENT_LOG, self.payment_log):
            frappe.throw(_("A refund needs an existing payment."))
        if flt(self.refund_amount) <= 0:
            frappe.throw(_("Refund amount must be greater than zero."))
        if self.is_new() and not self.flags.ignore_balance_check:
            self.validate_balance()

    def validate_balance(self) -> None:
        from frappe_paystack.core.refunds import refundable_balance

        session = frappe.get_doc(PAYMENT_LOG, self.payment_log)
        if session.status not in REFUNDABLE_STATUSES:
            frappe.throw(_("Payment {0} is {1}. Only captured payments can be refunded.").format(session.name, session.status))
        available = refundable_balance(session)
        if flt(self.refund_amount) - available > 0.005:
            frappe.throw(
                _("Refund amount ({0}) exceeds the refundable balance ({1}).").format(
                    fmt_money(self.refund_amount, currency=self.currency), fmt_money(available, currency=self.currency)
                )
            )

    def on_trash(self) -> None:
        if self.status == REFUND_PROCESSED or self.refund_reference:
            frappe.throw(_("A refund that reached Paystack cannot be deleted."))

    def receipt_recipient(self) -> str:
        from frappe_paystack.core.adapters import get_adapter

        session = frappe.get_doc(PAYMENT_LOG, self.payment_log)
        payer = get_adapter(session.linked_doctype).get_payer(session) if session.linked_doctype else {}
        return (payer or {}).get("email") or session.payer_email or ""

    def send_refund_receipt_email(self) -> bool:
        if self.status != REFUND_PROCESSED:
            return False
        recipient = self.receipt_recipient()
        if not recipient:
            return False
        frappe.enqueue(
            frappe.sendmail,
            queue="short",
            timeout=300,
            recipients=[recipient],
            subject=_("Refund Receipt - {0}").format(self.name),
            message=_("A refund of {0} has been processed. Your receipt is attached.").format(
                fmt_money(self.refund_amount, currency=self.currency)
            ),
            reference_doctype=REFUND_LOG,
            reference_name=self.name,
            attachments=[frappe.attach_print(REFUND_LOG, self.name, print_format=REFUND_RECEIPT_PRINT_FORMAT)],
            enqueue_after_commit=True,
        )
        return True


def get_total_refunded(payment_log_name: str, exclude: Any = None) -> float:
    """Upstream helper: processed refunds against a payment."""
    from frappe_paystack.core.refunds import total_refunded

    return total_refunded(payment_log_name, exclude=exclude)


def refund_status(total: float, amount_paid: float) -> str:
    """Upstream helper: the payment status for a refunded total."""
    from frappe_paystack.core.refunds import session_refund_status

    return session_refund_status(amount_paid, total)


@frappe.whitelist()
def send_refund_receipt(refund_log_name: str) -> bool:
    if not frappe.db.exists(REFUND_LOG, refund_log_name):
        return False
    refund = frappe.get_doc(REFUND_LOG, refund_log_name)
    refund.check_permission("read")
    return refund.send_refund_receipt_email()
