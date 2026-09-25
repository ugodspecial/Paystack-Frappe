"""ERPNext test fixtures (imported only when ERPNext is installed)."""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime, nowdate

from frappe_paystack.core.constants import GATEWAY_SETTING

COMPANY_NAME = "Paystack Test Co"
ABBR = "PTC"
CUSTOMER = "Paystack Test Customer"
CUSTOMER_EMAIL = "paystack-customer@example.com"
ITEM = "Paystack Test Service"


def before_tests() -> None:
    """Give an empty ERPNext site a Nigerian company through the setup wizard."""
    if not frappe.db.a_row_exists("Company"):
        from frappe.desk.page.setup_wizard.setup_wizard import setup_complete

        year = now_datetime().year
        setup_complete(
            {
                "currency": "NGN",
                "full_name": "Test User",
                "company_name": COMPANY_NAME,
                "timezone": "Africa/Lagos",
                "company_abbr": ABBR,
                "industry": "Services",
                "country": "Nigeria",
                "fy_start_date": f"{year}-01-01",
                "fy_end_date": f"{year}-12-31",
                "language": "english",
                "company_tagline": "Testing",
                "email": "test@example.com",
                "password": "test",
                "chart_of_accounts": "Standard",
            }
        )
    try:
        from erpnext.setup.utils import _enable_all_roles_for_admin, set_defaults_for_tests

        _enable_all_roles_for_admin()
        set_defaults_for_tests()
    except ImportError:
        pass
    frappe.db.commit()


def company() -> str:
    if frappe.db.exists("Company", COMPANY_NAME):
        return COMPANY_NAME
    return frappe.db.get_value("Company", {"default_currency": "NGN"}, "name") or frappe.get_all("Company", pluck="name")[0]


def abbr(company_name: str) -> str:
    return frappe.db.get_value("Company", company_name, "abbr")


def ensure_account(company_name: str, account_name: str, parent: str, account_type: str = "", root_type: str = "") -> str:
    name = f"{account_name} - {abbr(company_name)}"
    if frappe.db.exists("Account", name):
        return name
    parent_account = frappe.db.get_value("Account", {"company": company_name, "account_name": parent, "is_group": 1}, "name")
    doc = frappe.get_doc(
        {
            "doctype": "Account",
            "account_name": account_name,
            "parent_account": parent_account,
            "company": company_name,
            "account_type": account_type,
            "account_currency": frappe.db.get_value("Company", company_name, "default_currency"),
        }
    )
    doc.flags.ignore_permissions = True
    doc.insert()
    return doc.name


def ensure_customer(name: str = CUSTOMER, email: str = CUSTOMER_EMAIL) -> str:
    if not frappe.db.exists("Customer", name):
        doc = frappe.get_doc(
            {
                "doctype": "Customer",
                "customer_name": name,
                "customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}, "name") or "All Customer Groups",
                "territory": frappe.db.get_value("Territory", {"is_group": 0}, "name") or "All Territories",
                "default_currency": "NGN",
            }
        )
        doc.flags.ignore_permissions = True
        doc.insert()
    frappe.db.set_value("Customer", name, "email_id", email)
    return name


def ensure_item(name: str = ITEM) -> str:
    if not frappe.db.exists("Item", name):
        doc = frappe.get_doc(
            {
                "doctype": "Item",
                "item_code": name,
                "item_name": name,
                "item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups",
                "stock_uom": "Nos",
                "is_stock_item": 0,
                "include_item_in_manufacturing": 0,
            }
        )
        doc.flags.ignore_permissions = True
        doc.insert()
    return name


def paystack_accounts(company_name: str) -> dict:
    return {
        "suspense_account": ensure_account(company_name, "Paystack Suspense", "Bank Accounts", "Bank"),
        "settlement_bank_account": ensure_account(company_name, "Paystack Payout Bank", "Bank Accounts", "Bank"),
        "paystack_fee_account": ensure_account(company_name, "Paystack Fees", "Indirect Expenses"),
    }


def make_sales_invoice(company_name: str, customer: str = CUSTOMER, rate: float = 5000, submit: bool = True):
    doc = frappe.get_doc(
        {
            "doctype": "Sales Invoice",
            "company": company_name,
            "customer": customer,
            "posting_date": nowdate(),
            "due_date": nowdate(),
            "currency": "NGN",
            "conversion_rate": 1,
            "items": [{"item_code": ensure_item(), "qty": 1, "rate": rate}],
        }
    )
    doc.flags.ignore_permissions = True
    doc.insert()
    if submit:
        doc.submit()
    frappe.db.commit()
    return doc


def erpnext_setting(name: str, secret: str, public: str, company_name: str, **values):
    from frappe_paystack.tests.support.base import make_setting

    return make_setting(name, secret, public, company=company_name, mode_of_payment="Paystack",
                        **paystack_accounts(company_name), **values)


__all__ = ["GATEWAY_SETTING"]
