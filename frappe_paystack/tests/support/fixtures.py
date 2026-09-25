"""Test-session setup that works with or without ERPNext."""

import frappe


def before_tests() -> None:
    frappe.clear_cache()
    if "erpnext" in frappe.get_installed_apps():
        from frappe_paystack.tests.support.erpnext_fixtures import before_tests as erpnext_before_tests

        erpnext_before_tests()
    frappe.db.commit()
