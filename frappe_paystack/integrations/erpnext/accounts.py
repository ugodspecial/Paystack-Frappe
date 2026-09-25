"""ERPNext accounting helpers: company routing, party accounts, rates, customer email."""

from __future__ import annotations

from typing import Any, Optional

import frappe
from frappe import _
from frappe.utils import flt

from frappe_paystack.core import money
from frappe_paystack.core.constants import GATEWAY_SETTING, PAYMENT_LOG
from frappe_paystack.integrations.erpnext import is_available

PAYMENT_GATEWAY_ACCOUNT = "Payment Gateway Account"
DEFAULT_GATEWAY = "Paystack"


def has_company_field(doctype: str = GATEWAY_SETTING) -> bool:
    try:
        return frappe.get_meta(doctype).has_field("company")
    except Exception:
        return False


def company_for_reference(doctype: Optional[str], docname: Optional[str]) -> Optional[str]:
    if not doctype or not docname or not has_company_field(doctype):
        return None
    return frappe.db.get_value(doctype, docname, "company")


def setting_for_company(company: Optional[str]) -> Optional[str]:
    """The enabled Paystack account that collects a company's money."""
    if not company or not has_company_field(GATEWAY_SETTING):
        return None
    return frappe.db.get_value(GATEWAY_SETTING, {"company": company, "enabled": 1}, "name", order_by="creation asc")


def resolve_setting_for_reference(reference_doctype: str, reference_docname: str, default_setting: str) -> Optional[str]:
    """`paystack_account_resolvers` hook: route to the account of the document's company."""
    if not is_available():
        return None
    company = company_for_reference(reference_doctype, reference_docname)
    if not company:
        return None
    name = setting_for_company(company)
    if not name and frappe.db.get_value(GATEWAY_SETTING, default_setting, "company") not in (None, "", company):
        frappe.throw(
            _("Paystack is not enabled for {0}. Enable a Paystack Gateway Setting for {0} before collecting its payments.").format(
                company
            )
        )
    return name


def gateway_accounts(setting_name: Optional[str]) -> frappe._dict:
    """The ERPNext accounts configured on a Paystack account."""
    if not setting_name or not frappe.db.exists(GATEWAY_SETTING, setting_name):
        return frappe._dict()
    setting = frappe.get_doc(GATEWAY_SETTING, setting_name)
    return frappe._dict(
        name=setting.name,
        company=setting.get("company"),
        currency=setting.currency,
        suspense_account=setting.get("suspense_account"),
        mode_of_payment=setting.get("mode_of_payment"),
        settlement_bank_account=setting.get("settlement_bank_account"),
        paystack_fee_account=setting.get("paystack_fee_account"),
        auto_refund_on_credit_note=setting.get("auto_refund_on_credit_note"),
        auto_charge_subscriptions=setting.get("auto_charge_subscriptions"),
    )


def customer_email(customer: Optional[str]) -> str:
    if not customer:
        return ""
    return frappe.db.get_value("Customer", customer, "email_id") or ""


@frappe.whitelist()
def get_customer_email(customer: str) -> str:
    if not frappe.has_permission("Customer", "read", doc=customer):
        frappe.throw(_("Not permitted to read Customer {0}.").format(customer), frappe.PermissionError)
    return customer_email(customer)


def check_company_permission(company: Optional[str]) -> None:
    if not frappe.has_permission("Company", "read", doc=company):
        frappe.throw(_("You are not permitted to access Paystack data for {0}.").format(company), frappe.PermissionError)


def party_account_for(inv: Any) -> str:
    """The receivable a document's settlement posts against."""
    if inv.doctype == "Sales Invoice":
        return inv.debit_to
    from erpnext.accounts.party import get_party_account

    return get_party_account("Customer", inv.customer, inv.company)


def party_account_rate(inv: Any, party_account: str) -> float:
    """Rate between the charge currency and the party account's currency."""
    company_currency = frappe.get_cached_value("Company", inv.company, "default_currency")
    account_currency = frappe.get_cached_value("Account", party_account, "account_currency")
    if account_currency == company_currency:
        return 1.0
    return flt(inv.get("conversion_rate")) or 1.0


def outstanding_rate(inv: Any) -> float:
    rate = flt(inv.get("conversion_rate"))
    if rate in (0.0, 1.0):
        return 1.0
    account_currency = frappe.get_cached_value("Account", party_account_for(inv), "account_currency")
    return 1.0 if account_currency == inv.get("currency") else rate


def get_company_currency(company: str) -> str:
    return money.clean_currency(frappe.db.get_value("Company", company, "default_currency"))


def coalesce_currency(currency: Optional[str], company: Optional[str], settings: Optional[dict] = None) -> str:
    """15.x helper: explicit, then company, then account currency (no NGN fallback)."""
    if currency:
        return money.clean_currency(currency)
    if company:
        return get_company_currency(company)
    if settings and settings.get("default_currency"):
        return money.clean_currency(settings["default_currency"])
    return ""


def charge_currency(company: Optional[str], currency_paid: Optional[str] = None) -> str:
    return money.clean_currency(currency_paid) if currency_paid else coalesce_currency(None, company)


def discard_draft_payment_entry(entry: Any) -> None:
    name = entry.name if entry else None
    if not name or frappe.db.get_value("Payment Entry", name, "docstatus") != 0:
        return
    frappe.delete_doc("Payment Entry", name, force=True, ignore_permissions=True)


def payable_amount(doc: Any) -> float:
    """What is still payable on a document, in its own currency."""
    outstanding = flt(doc.get("outstanding_amount"))
    if outstanding:
        return flt(outstanding / outstanding_rate(doc))
    return max(0.0, flt(doc.get("grand_total")) - flt(doc.get("advance_paid")))


def set_if_field(doctype: str, name: str, values: dict) -> None:
    """Write adapter-owned fields that exist on this site (they are Custom Fields)."""
    meta = frappe.get_meta(doctype)
    values = {k: v for k, v in values.items() if meta.has_field(k)}
    if values:
        frappe.db.set_value(doctype, name, values, update_modified=False)


def session_company(session: Any) -> Optional[str]:
    company = session.get("company") if hasattr(session, "get") else None
    if company:
        return company
    return frappe.db.get_value(GATEWAY_SETTING, session.gateway_setting, "company") if session.gateway_setting and has_company_field() else None


__all__ = [
    "PAYMENT_LOG",
    "customer_email",
    "get_customer_email",
    "party_account_for",
    "party_account_rate",
    "outstanding_rate",
]
