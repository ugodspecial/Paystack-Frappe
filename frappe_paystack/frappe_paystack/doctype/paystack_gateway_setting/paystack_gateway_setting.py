# Copyright (c) 2024, Anthony C. Emmanuel and contributors
# For license information, please see license.txt

"""
A Paystack account, and its Frappe Payments gateway controller.

Implements the Payments v1 controller contract (duck-typed, as every gateway
in frappe/payments does): validate_transaction_currency,
validate_minimum_transaction_amount, get_payment_url, plus the optional
on_payment_request_submission / request_for_payment used by ERPNext, and
refund_payment / fetch_refund / fetch_refunds (named after Razorpay's).
Nothing here imports ERPNext.
"""

from typing import Any, Optional

import frappe
from frappe import _
from frappe.model.document import Document

from frappe_paystack.core import money
from frappe_paystack.core.constants import GATEWAY_SETTING, PAYMENT_LOG

TEST_KEY_PREFIXES = ("sk_test_", "pk_test_")
LIVE_KEY_PREFIXES = ("sk_live_", "pk_live_")
KEY_FIELDS = ("secret_key", "public_key")


class PaystackGatewaySetting(Document):
    supported_currencies = money.SUPPORTED_CURRENCIES

    # ------------------------------------------------------------------ lifecycle

    def onload(self) -> None:
        from frappe_paystack.core.accounts import gateways_for_setting

        self.set_onload("webhook_url", frappe.utils.get_url("/api/method/frappe_paystack.api.paystack_webhook"))
        self.set_onload("payment_gateways", gateways_for_setting(self.name) if not self.is_new() else [])

    def validate(self) -> None:
        self.currency = money.clean_currency(self.currency) or "NGN"
        money.ensure_supported(self.currency)
        extra = [c for c in money.split_currency_list(self.additional_currencies) if c != self.currency]
        for code in extra:
            money.ensure_supported(code)
        self.additional_currencies = ", ".join(extra)
        self.validate_key_mode()

    def on_update(self) -> None:
        from frappe_paystack.core.accounts import register_setting, unregister_setting

        if self.enabled:
            try:
                register_setting(self)
            except Exception:
                frappe.log_error(title="Paystack could not be registered as a Payment Gateway", message=frappe.get_traceback())
        elif self.has_value_changed("enabled"):
            unregister_setting(self.name)

    def on_trash(self) -> None:
        from frappe_paystack.core.accounts import unregister_setting

        if frappe.db.exists(PAYMENT_LOG, {"gateway_setting": self.name}):
            frappe.throw(_("Payments were collected through {0}. Disable it instead of deleting it.").format(self.name))
        unregister_setting(self.name)

    def validate_key_mode(self) -> None:
        """Refuse live keys under Test Mode, and test keys outside it."""
        for fieldname in KEY_FIELDS:
            key = (self.get_password(fieldname, raise_exception=False) or "") if fieldname == "secret_key" else (self.get(fieldname) or "")
            label = _(self.meta.get_label(fieldname))
            if self.test_mode and key.startswith(LIVE_KEY_PREFIXES):
                frappe.throw(
                    _("Test Mode is on, so {0} must be a Paystack test key. This is a live key and would charge real cards.").format(label)
                )
            if not self.test_mode and key.startswith(TEST_KEY_PREFIXES):
                frappe.throw(_("{0} is a Paystack test key, which captures no money. Switch Test Mode on, or enter the live key.").format(label))

    # ------------------------------------------------------------------ helpers

    def get_secret_key(self) -> str:
        return self.get_password("secret_key")

    def get_webhook_secret(self) -> Optional[str]:
        """The secret webhooks are verified with: the override if set, else the secret key."""
        if self.get("webhook_secret"):
            secret = self.get_password("webhook_secret", raise_exception=False)
            if secret:
                return secret
        return self.get_password("secret_key", raise_exception=False)

    def get_allowed_currencies(self) -> list:
        return [money.clean_currency(self.currency)] + [
            c for c in money.split_currency_list(self.additional_currencies) if c != money.clean_currency(self.currency)
        ]

    # ------------------------------------------------------------------ Payments v1 contract

    def get_supported_currencies(self) -> list:
        return list(self.supported_currencies)

    # Upstream name, kept for callers that used it.
    def get_supported_currency(self) -> list:
        return self.get_supported_currencies()

    def validate_transaction_currency(self, currency: str) -> None:
        """Payments/ERPNext call this before creating a payment. Paystack-level check only."""
        if not money.is_supported(currency):
            frappe.throw(
                _("Please select another payment method. Paystack does not support transactions in currency '{0}'").format(currency)
            )

    def validate_minimum_transaction_amount(self, currency: str, amount: Any) -> None:
        money.validate_amount(amount, money.clean_currency(currency))

    def get_payment_url(self, **kwargs) -> str:
        """
        Create (or reuse) a payment session for any reference document and return its checkout URL.

        Accepts the Payments kwargs (amount, currency, title, description,
        reference_doctype, reference_docname, payer_email, payer_name, order_id,
        redirect_to, payment_gateway) and any extras, which are handed back to
        the consumer in frappe.flags.data on completion.
        """
        from frappe_paystack.core.session import checkout_url, create_session

        session = create_session(self, kwargs)
        # Keeps the session through the rollback Frappe applies to GET requests.
        frappe.local.flags.commit = True
        return checkout_url(session.name)

    def on_payment_request_submission(self, payment_request: Any) -> bool:
        """Whether Paystack can collect a payment request's currency (duck-typed)."""
        return money.is_supported(getattr(payment_request, "currency", None))

    def request_for_payment(self, **kwargs) -> Any:
        """Delegate a "phone"-style payment request to the reference DocType's adapter."""
        from frappe_paystack.core.adapters import get_adapter

        adapter = get_adapter(kwargs.get("reference_doctype"))
        handler = getattr(adapter, "request_for_payment", None)
        if not handler:
            frappe.throw(_("Paystack cannot start a payment request for {0}.").format(kwargs.get("reference_doctype")))
        return handler(self, **kwargs)

    # ------------------------------------------------------------------ refunds (Razorpay-style names)

    def refund_payment(self, payment_id: str, amount: Optional[float] = None, reason: Optional[str] = None) -> Any:
        """Refund a session (by name, transaction id or reference) and return the Refund Log."""
        from frappe_paystack.core.permissions import check_money_permission
        from frappe_paystack.core.refunds import refund_payment

        check_money_permission(_("You are not permitted to refund Paystack payments."))
        session = (
            frappe.db.get_value(PAYMENT_LOG, payment_id, "name")
            or frappe.db.get_value(PAYMENT_LOG, {"transaction_id": payment_id}, "name")
            or frappe.db.get_value(PAYMENT_LOG, {"payment_reference": payment_id}, "name")
        )
        if not session:
            frappe.throw(_("No Paystack payment {0}.").format(payment_id), frappe.DoesNotExistError)
        return refund_payment(session, amount=amount, reason=reason)

    def fetch_refund(self, refund_id: str) -> Optional[dict]:
        from frappe_paystack.core.client import client_for

        return client_for(self).fetch_refund(refund_id)

    def fetch_refunds(self, payment_id: str) -> list:
        from frappe_paystack.core.client import client_for

        transaction = frappe.db.get_value(PAYMENT_LOG, payment_id, "transaction_id") or payment_id
        return client_for(self).list_refunds(transaction)

    # ------------------------------------------------------------------ desk

    @frappe.whitelist()
    def test_connection(self) -> dict:
        """Check the secret key against Paystack and report the webhook URL to configure."""
        from frappe_paystack.core.client import client_for

        self.check_permission("write")
        client_for(self, GATEWAY_SETTING, self.name).request("GET", "/transaction", params={"perPage": 1})
        return {
            "ok": True,
            "message": _("Paystack accepted the secret key."),
            "webhook_url": frappe.utils.get_url("/api/method/frappe_paystack.api.paystack_webhook"),
        }
