"""
15.x install helpers, kept importable for patches and third-party callers.

Core install logic lives in frappe_paystack.install; ERPNext pieces (Mode of
Payment, Payment Gateway Accounts, POS) in frappe_paystack.integrations.erpnext.setup,
which imports ERPNext lazily.
"""

import frappe

from frappe_paystack.core.constants import GATEWAY_SETTING  # noqa: F401
from frappe_paystack.install import create_dashboard_widgets, ensure_dashboard_charts, ensure_number_cards  # noqa: F401

MODE_OF_PAYMENT = "Paystack"
GATEWAY_DOCTYPE = GATEWAY_SETTING


def after_install() -> None:
    from frappe_paystack.install import after_install as install

    install()


def create_payment_gateway_record() -> None:
    from frappe_paystack.core.accounts import default_setting_name, ensure_payment_gateway

    setting = default_setting_name()
    if setting:
        ensure_payment_gateway("Paystack", setting)


def update_payment_gateway_controller(setting_name: str) -> None:
    from frappe_paystack.core.accounts import ensure_payment_gateway

    ensure_payment_gateway("Paystack", setting_name)


def repoint_payment_gateway_controller(removed_setting: str) -> None:
    from frappe_paystack.core.accounts import unregister_setting

    unregister_setting(removed_setting)


def find_replacement_setting(removed_setting: str) -> str:
    return frappe.db.get_value(GATEWAY_SETTING, {"name": ["!=", removed_setting]}, "name", order_by="enabled desc, modified desc")


def _erpnext(name):
    from frappe_paystack.integrations.erpnext import is_available

    if not is_available():
        return None
    from frappe_paystack.integrations.erpnext import setup as erpnext_setup

    return getattr(erpnext_setup, name)


def ensure_mode_of_payment(pos_enabled: bool = False):
    fn = _erpnext("ensure_mode_of_payment")
    return fn(pos_enabled) if fn else None


def ensure_email_payment_type() -> None:
    fn = _erpnext("ensure_email_payment_type")
    if fn:
        fn()


def create_payment_gateway_account(company: str, suspense_account: str, currency: str = "NGN"):
    fn = _erpnext("create_payment_gateway_account")
    return fn(company, suspense_account, currency) if fn else None


def create_payment_gateway_accounts_for_existing_settings() -> None:
    fn = _erpnext("create_payment_gateway_accounts_for_existing_settings")
    if fn:
        fn()


def setup_pos_payment_mode(company: str, suspense_account: str, channel: str = "Email"):
    fn = _erpnext("setup_pos_payment_mode")
    return fn(company, suspense_account, channel) if fn else None
