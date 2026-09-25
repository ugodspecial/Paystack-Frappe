# Copyright (c) 2026, Anthony Emmanuel and contributors
# For license information, please see license.txt

"""
A reusable card authorization Paystack returned with a successful charge.

Stored and charged by frappe_paystack.core.authorizations. The authorization
code charges the card, so it is kept in a Password field.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from frappe_paystack.core import authorizations

AUTHORIZATION_DOCTYPE = "Paystack Customer Authorization"
PUBLIC_FIELDS = authorizations.PUBLIC_FIELDS


class PaystackCustomerAuthorization(Document):
    def validate(self) -> None:
        self.card_label = self.build_label()

    def build_label(self) -> str:
        brand = self.brand or self.card_type or self.channel or _("Card")
        if not self.last4:
            return str(brand).title()
        return f"{str(brand).title()} •••• {self.last4}"

    def has_expired(self) -> bool:
        return authorizations.has_expired(self)

    def is_usable(self) -> bool:
        return bool(self.active and self.reusable) and not self.has_expired()

    def get_authorization_code(self) -> str:
        return self.get_password("authorization_code")


# Upstream helpers, kept for callers that imported them from here.
is_storable = authorizations.is_storable
authorization_fields = authorizations.authorization_fields
