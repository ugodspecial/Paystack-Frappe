"""Point the "Paystack" Payment Gateway at an enabled account and broadcast payment_gateway_enabled."""

import frappe


def execute() -> None:
    from frappe_paystack.install import ensure_roles, register_enabled_settings

    ensure_roles()
    register_enabled_settings()
    frappe.db.commit()
