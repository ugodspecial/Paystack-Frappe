"""
Compatibility module for frappe_paystack 15.x (`frappe_paystack.utils.utils`).

Generic helpers are implemented here on top of frappe_paystack.core. ERPNext
helpers (party accounts, customer email, company settings) are looked up
lazily in frappe_paystack.integrations.erpnext, so importing this module never
imports ERPNext.
"""

from importlib import import_module
from typing import Any, Dict, Optional

import frappe
from frappe import _

from frappe_paystack.core import money
from frappe_paystack.core.accounts import hmac_sha512, is_ip_allowed, verify_signature  # noqa: F401
from frappe_paystack.core.constants import GATEWAY_SETTING, PAYSTACK_SERVICE  # noqa: F401
from frappe_paystack.core.logging import (  # noqa: F401
    error_log_link,
    log_error_for,
    log_integration_request,
    record_failure,
    redact,
)

SUPPORTED_CURRENCIES = list(money.SUPPORTED_CURRENCIES)

_ERPNEXT = {
    "customer_email",
    "get_customer_email",
    "check_company_permission",
    "party_account_for",
    "party_account_rate",
    "outstanding_rate",
    "discard_draft_payment_entry",
    "get_company_currency",
    "coalesce_currency",
    "charge_currency",
}


def __getattr__(name):
    if name in _ERPNEXT:
        return getattr(import_module("frappe_paystack.integrations.erpnext.accounts"), name)
    raise AttributeError(f"module 'frappe_paystack.utils.utils' has no attribute {name!r}")


def redact_authorization(payload: Any) -> Any:
    return redact(payload)


def normalize_currency(currency: Optional[str]) -> str:
    """Upper-cased currency code. Unlike 15.x, an unsupported code is no longer turned into NGN."""
    return money.clean_currency(currency)


def ensure_supported_currency(currency: str) -> None:
    money.ensure_supported(currency)


def to_minor_units(amount: float, currency: str) -> int:
    return money.to_minor(amount, currency)


def from_minor_units(amount_minor: int, currency: str = "") -> float:
    return money.from_minor(amount_minor, currency)


def parse_paystack_response(response: Any) -> Dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        body = (getattr(response, "text", "") or "").strip()[:200]
        frappe.throw(
            _("Paystack returned a non-JSON response (HTTP {0}): {1}").format(
                getattr(response, "status_code", "?"), body or _("empty body")
            )
        )
    return data


def resolve_settings_for_signature(payload: bytes, signature: Optional[str]) -> Optional[Dict[str, Any]]:
    from frappe_paystack.core.accounts import settings_for_signature

    setting = settings_for_signature(payload, signature)
    return _settings_dict(setting) if setting else None


def get_gateway_secret(gateway: Optional[str]) -> str:
    return frappe.get_doc(GATEWAY_SETTING, {"name": gateway, "enabled": 1}).get_password("secret_key")


def _settings_dict(setting: Any) -> Dict[str, Any]:
    return {
        "name": setting.name,
        "company": setting.get("company"),
        "public_key": setting.public_key,
        "secret_key": setting.get_password("secret_key"),
        "webhook_secret": setting.get_webhook_secret(),
        "test_mode": bool(setting.test_mode),
        "allowed_webhook_ips": setting.allowed_webhook_ips or None,
        "auto_refund_on_credit_note": bool(setting.get("auto_refund_on_credit_note")),
        "default_currency": setting.currency,
    }


def resolve_paystack_settings(company: Optional[str]) -> Optional[Dict[str, Any]]:
    """15.x: the enabled settings for a company (ERPNext), as a dict."""
    from frappe_paystack.integrations.erpnext.accounts import setting_for_company

    name = setting_for_company(company)
    return _settings_dict(frappe.get_doc(GATEWAY_SETTING, name)) if name else None


def get_company_row_settings(company: Optional[str]) -> Optional[Dict[str, Any]]:
    return resolve_paystack_settings(company)


def is_paystack_enabled(company: Optional[str]) -> bool:
    return bool(resolve_paystack_settings(company))
