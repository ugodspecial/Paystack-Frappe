"""
ERPNext adapter for the Paystack gateway.

Everything ERPNext-specific lives here: Payment Request settlement, Payment
Entry / reversal / Journal Entry booking, company routing, POS, Dunning,
subscriptions, the customer portal and credit-note refunds. ERPNext is
imported lazily, inside functions, and every scheduler and hook entry point
returns early when ERPNext is not installed. The core registers these pieces
through hooks (see hooks.py) and never imports this package.
"""

import frappe


def is_available() -> bool:
    """Whether ERPNext is installed on this site (cached per request)."""
    cache = getattr(frappe.local, "paystack_erpnext_available", None)
    if cache is None:
        try:
            cache = "erpnext" in frappe.get_installed_apps()
        except Exception:
            cache = False
        frappe.local.paystack_erpnext_available = cache
    return cache


def activate() -> None:
    """Install (idempotently) the customisations the adapter needs."""
    frappe.local.paystack_erpnext_available = None
    from frappe_paystack.integrations.erpnext.setup import activate as _activate

    _activate()


def deactivate() -> None:
    """Remove the adapter's customisations; data columns and documents are kept."""
    from frappe_paystack.integrations.erpnext.setup import deactivate as _deactivate

    _deactivate()
    frappe.local.paystack_erpnext_available = None
