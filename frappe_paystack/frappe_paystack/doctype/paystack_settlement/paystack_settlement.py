# Copyright (c) 2026, Anthony Emmanuel and contributors
# For license information, please see license.txt

"""A Paystack payout: facts only. Ledger booking belongs to accounting adapters."""

import frappe
from frappe import _
from frappe.model.document import Document


class PaystackSettlement(Document):
    def validate(self) -> None:
        if self.currency:
            self.currency = self.currency.upper().strip()

    def on_trash(self) -> None:
        # journal_entry exists only when the ERPNext adapter is active.
        if self.get("journal_entry"):
            frappe.throw(
                _("Cannot delete this settlement because it is linked to Journal Entry {0}. Cancel the Journal Entry first.").format(
                    self.get("journal_entry")
                )
            )
