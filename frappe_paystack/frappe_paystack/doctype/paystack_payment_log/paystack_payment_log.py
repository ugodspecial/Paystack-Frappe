# Copyright (c) 2025, Anthony Emmanuel and contributors
# For license information, please see license.txt

"""
A payment session: one payable intent from any consuming application.

The name (a random hash) is the capability behind the public checkout link
/paystack-checkout/<name>. State changes happen only through
frappe_paystack.core.lifecycle; this controller guards invariants and offers
the desk actions.
"""

from typing import Optional

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from frappe_paystack.core.constants import (
    CAPTURED_STATUSES,
    NEEDS_ATTENTION,
    NOTIFY_DONE,
    NOTIFY_NOT_REQUIRED,
    NOTIFICATION_STATUSES,
    SESSION_STATUSES,
)

# Statuses a session can never be deleted in: it is the only record of money.
UNDELETABLE_STATUSES = CAPTURED_STATUSES + (NEEDS_ATTENTION,)


class PaystackPaymentLog(Document):
    def validate(self) -> None:
        if self.status not in SESSION_STATUSES:
            frappe.throw(_("Invalid status value: {0}.").format(self.status))
        if self.notification_status and self.notification_status not in NOTIFICATION_STATUSES:
            frappe.throw(_("Invalid notification status: {0}.").format(self.notification_status))
        if bool(self.linked_doctype) ^ bool(self.linked_docname):
            frappe.throw(_("Reference DocType and Reference Name must both be set or both be empty."))
        if flt(self.amount) < 0:
            frappe.throw(_("Amount cannot be negative."))

    def on_trash(self) -> None:
        if self.status in UNDELETABLE_STATUSES:
            frappe.throw(_("Cannot delete a payment in status {0}: it is the record of money Paystack holds.").format(self.status))

    def get_payment_link(self) -> str:
        from frappe_paystack.core.session import checkout_url

        return checkout_url(self.name)

    def is_expired(self) -> bool:
        from frappe_paystack.core.session import is_expired

        return is_expired(self)

    # ------------------------------------------------------------------ desk actions

    def _check_manage(self) -> None:
        from frappe_paystack.core.permissions import check_money_permission

        self.check_permission("read")
        check_money_permission(_("You are not permitted to manage Paystack payments."))

    @frappe.whitelist()
    def verify_with_paystack(self) -> dict:
        """
        Ask Paystack for this payment's status and apply it.

        The consumer is notified as the session's run-as user, never as the
        administrator clicking the button (decision D5).
        """
        from frappe_paystack.core.lifecycle import manual_verify

        self._check_manage()
        return manual_verify(self.name)

    # Upstream name for the desk "Verify Transaction" action.
    @frappe.whitelist()
    def validate_payment(self) -> dict:
        return self.verify_with_paystack()

    @frappe.whitelist()
    def retry_notification(self) -> dict:
        """Notify the consumer again now (skipping the back-off), as the run-as user."""
        from frappe_paystack.core.notify import notify_success, reset_notification

        self._check_manage()
        if self.status not in CAPTURED_STATUSES:
            frappe.throw(_("Only captured payments notify their application."))
        if self.notification_status in (NOTIFY_DONE, NOTIFY_NOT_REQUIRED):
            frappe.throw(_("The application was already notified."))
        reset_notification(self.name)
        notify_success(self.name, force=True)
        self.reload()
        return {"notification_status": self.notification_status, "notification_error": self.notification_error}

    @frappe.whitelist()
    def mark_notification_resolved(self, note: str) -> None:
        """Close a failed notification that was resolved by other means (e.g. enrolled by hand)."""
        from frappe_paystack.core.notify import mark_notification_resolved

        self._check_manage()
        if not (note or "").strip():
            frappe.throw(_("Explain how the notification was resolved."))
        mark_notification_resolved(self.name, note.strip())

    @frappe.whitelist()
    def refund(self, amount: Optional[float] = None, reason: Optional[str] = None) -> str:
        from frappe_paystack.core.refunds import refund_payment

        self.check_permission("read")
        from frappe_paystack.core.permissions import check_money_permission

        check_money_permission(_("You are not permitted to refund Paystack payments."))
        return refund_payment(self.name, amount=amount, reason=reason).name

