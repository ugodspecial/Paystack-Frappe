"""
The only place that talks to the Paystack API.

Every call goes through ``PaystackClient._send``, which tests replace with a
fake (see ``frappe_paystack.tests.support.paystack_fake``). Calls are logged
as Integration Requests with secrets redacted.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import quote

import frappe
import requests
from frappe import _

from frappe_paystack.core.constants import API_BASE
from frappe_paystack.core.logging import log_integration_request

DEFAULT_TIMEOUT = 30
LOOKUP_TIMEOUT = 15

# Paystack answers an unknown reference or id with 404.
NOT_FOUND = 404


class PaystackError(frappe.ValidationError):
    """Paystack refused a request or could not be reached."""


class PaystackResponse:
    """A decoded Paystack response."""

    def __init__(self, status_code: int, body: Optional[Dict[str, Any]], text: str = ""):
        self.status_code = status_code
        self.body = body if isinstance(body, dict) else None
        self.text = text or ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300 and bool(self.body) and bool(self.body.get("status"))

    @property
    def data(self) -> Any:
        return (self.body or {}).get("data")

    @property
    def message(self) -> str:
        if self.body and self.body.get("message"):
            return str(self.body.get("message"))
        return (self.text or "").strip()[:200] or _("empty response")


class PaystackClient:
    """Paystack API client bound to one account's secret key."""

    def __init__(self, secret_key: str, reference_doctype: Optional[str] = None, reference_name: Optional[str] = None):
        if not secret_key:
            frappe.throw(_("The Paystack secret key is not set."), PaystackError)
        self.secret_key = secret_key
        self.reference_doctype = reference_doctype
        self.reference_name = reference_name

    # ------------------------------------------------------------------ transport

    def _send(
        self,
        method: str,
        path: str,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> PaystackResponse:
        """Perform one HTTP request. Tests patch this method."""
        response = requests.request(
            method,
            f"{API_BASE}{path}",
            headers={
                "Authorization": f"Bearer {self.secret_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=json,
            params=params,
            timeout=timeout,
        )
        try:
            body = response.json()
        except ValueError:
            body = None
        return PaystackResponse(response.status_code, body, response.text)

    def request(
        self,
        method: str,
        path: str,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
        timeout: int = DEFAULT_TIMEOUT,
        allow_not_found: bool = False,
    ) -> Any:
        """
        Call Paystack and return the response's ``data``.

        Throws PaystackError on transport failures and refusals. With
        allow_not_found, a 404 returns None instead.
        """
        log_request = {"method": method, "path": path, "json": json, "params": params}
        try:
            response = self._send(method, path, json=json, params=params, timeout=timeout)
        except PaystackError:
            raise
        except Exception as exc:
            log_integration_request(
                status="Failed",
                url=f"{API_BASE}{path}",
                request_data=log_request,
                error=str(exc),
                reference_doctype=self.reference_doctype,
                reference_docname=self.reference_name,
            )
            frappe.throw(_("Could not reach Paystack: {0}").format(str(exc)), PaystackError)

        log_integration_request(
            status="Completed" if response.ok else "Failed",
            url=f"{API_BASE}{path}",
            request_data=log_request,
            response_data=response.body or {"text": response.text[:500]},
            error=None if response.ok else response.message,
            reference_doctype=self.reference_doctype,
            reference_docname=self.reference_name,
        )

        if allow_not_found and response.status_code == NOT_FOUND:
            return None

        if response.body is None:
            frappe.throw(
                _("Paystack returned a non-JSON response (HTTP {0}): {1}").format(
                    response.status_code, response.message
                ),
                PaystackError,
            )

        if not response.ok:
            frappe.throw(_("Paystack refused the request: {0}").format(response.message), PaystackError)

        return response.data

    # ------------------------------------------------------------------ transactions

    def initialize_transaction(
        self,
        email: str,
        amount_minor: int,
        currency: str,
        reference: str,
        callback_url: Optional[str] = None,
        metadata: Optional[dict] = None,
        channels: Optional[list] = None,
    ) -> dict:
        body: Dict[str, Any] = {
            "email": email,
            "amount": int(amount_minor),
            "currency": currency,
            "reference": reference,
        }
        if callback_url:
            body["callback_url"] = callback_url
        if metadata:
            body["metadata"] = metadata
        if channels:
            body["channels"] = channels
        return self.request("POST", "/transaction/initialize", json=body) or {}

    def verify_transaction(self, reference: str) -> Optional[dict]:
        """Return the transaction behind a reference, or None when Paystack has none."""
        return self.request(
            "GET",
            f"/transaction/verify/{quote(str(reference), safe='')}",
            timeout=LOOKUP_TIMEOUT,
            allow_not_found=True,
        )

    def fetch_transaction(self, transaction_id: Any) -> Optional[dict]:
        return self.request(
            "GET",
            f"/transaction/{quote(str(transaction_id), safe='')}",
            timeout=LOOKUP_TIMEOUT,
            allow_not_found=True,
        )

    def charge_authorization(
        self,
        email: str,
        amount_minor: int,
        currency: str,
        reference: str,
        authorization_code: str,
        metadata: Optional[dict] = None,
    ) -> dict:
        body: Dict[str, Any] = {
            "email": email,
            "amount": int(amount_minor),
            "currency": currency,
            "reference": reference,
            "authorization_code": authorization_code,
        }
        if metadata:
            body["metadata"] = metadata
        return self.request("POST", "/transaction/charge_authorization", json=body) or {}

    # ------------------------------------------------------------------ refunds

    def create_refund(
        self,
        transaction: Any,
        amount_minor: Optional[int] = None,
        currency: Optional[str] = None,
        merchant_note: Optional[str] = None,
        customer_note: Optional[str] = None,
    ) -> dict:
        body: Dict[str, Any] = {"transaction": str(transaction)}
        if amount_minor:
            body["amount"] = int(amount_minor)
        if currency:
            body["currency"] = currency
        if merchant_note:
            body["merchant_note"] = merchant_note[:500]
        if customer_note:
            body["customer_note"] = customer_note[:500]
        return self.request("POST", "/refund", json=body) or {}

    def fetch_refund(self, refund_id: Any) -> Optional[dict]:
        return self.request(
            "GET",
            f"/refund/{quote(str(refund_id), safe='')}",
            timeout=LOOKUP_TIMEOUT,
            allow_not_found=True,
        )

    def list_refunds(self, transaction: Any) -> list:
        return self.request("GET", "/refund", params={"transaction": str(transaction)}) or []

    # ------------------------------------------------------------------ settlements

    def settlement_transactions(self, settlement_id: Any, page: int = 1, per_page: int = 100) -> list:
        return (
            self.request(
                "GET",
                f"/settlement/{quote(str(settlement_id), safe='')}/transactions",
                params={"perPage": per_page, "page": page},
            )
            or []
        )

    def list_settlements(self, date_from: Optional[str] = None, date_to: Optional[str] = None, page: int = 1) -> list:
        params: Dict[str, Any] = {"perPage": 100, "page": page}
        if date_from:
            params["from"] = date_from
        if date_to:
            params["to"] = date_to
        return self.request("GET", "/settlement", params=params) or []


def client_for(setting: Any, reference_doctype: Optional[str] = None, reference_name: Optional[str] = None) -> PaystackClient:
    """Return a client for a Paystack Gateway Setting document."""
    return PaystackClient(
        setting.get_password("secret_key", raise_exception=False),
        reference_doctype=reference_doctype,
        reference_name=reference_name,
    )
