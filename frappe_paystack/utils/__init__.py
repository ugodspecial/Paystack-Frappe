"""
Compatibility facade for code written against frappe_paystack 15.x.

`from frappe_paystack.utils import X` keeps working, but nothing is imported
until a name is used (PEP 562), so importing this package never imports
ERPNext. Generic helpers resolve to frappe_paystack.core; ERPNext helpers
resolve to frappe_paystack.integrations.erpnext and need ERPNext installed.
"""

from importlib import import_module

_LOCATIONS = {
    # generic
    "PAYSTACK_SERVICE": "frappe_paystack.core.constants",
    "SUPPORTED_CURRENCIES": "frappe_paystack.core.money",
    "hmac_sha512": "frappe_paystack.core.accounts",
    "verify_signature": "frappe_paystack.core.accounts",
    "is_ip_allowed": "frappe_paystack.core.accounts",
    "log_integration_request": "frappe_paystack.core.logging",
    "log_error_for": "frappe_paystack.core.logging",
    "record_failure": "frappe_paystack.core.logging",
    "error_log_link": "frappe_paystack.core.logging",
    "redact_authorization": "frappe_paystack.utils.utils",
    "to_minor_units": "frappe_paystack.utils.utils",
    "from_minor_units": "frappe_paystack.utils.utils",
    "ensure_supported_currency": "frappe_paystack.utils.utils",
    "normalize_currency": "frappe_paystack.utils.utils",
    "parse_paystack_response": "frappe_paystack.utils.utils",
    "resolve_settings_for_signature": "frappe_paystack.utils.utils",
    "get_gateway_secret": "frappe_paystack.utils.utils",
    # ERPNext (via the adapter)
    "resolve_paystack_settings": "frappe_paystack.utils.utils",
    "is_paystack_enabled": "frappe_paystack.utils.utils",
    "get_company_row_settings": "frappe_paystack.utils.utils",
    "customer_email": "frappe_paystack.integrations.erpnext.accounts",
    "get_customer_email": "frappe_paystack.integrations.erpnext.accounts",
    "check_company_permission": "frappe_paystack.integrations.erpnext.accounts",
    "party_account_for": "frappe_paystack.integrations.erpnext.accounts",
    "party_account_rate": "frappe_paystack.integrations.erpnext.accounts",
    "outstanding_rate": "frappe_paystack.integrations.erpnext.accounts",
    "discard_draft_payment_entry": "frappe_paystack.integrations.erpnext.accounts",
}

__all__ = sorted(_LOCATIONS)


def __getattr__(name):
    module = _LOCATIONS.get(name)
    if not module:
        raise AttributeError(f"module 'frappe_paystack.utils' has no attribute {name!r}")
    return getattr(import_module(module), name)
