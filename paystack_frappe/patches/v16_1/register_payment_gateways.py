"""Point the "Paystack" Payment Gateway at an enabled account and broadcast payment_gateway_enabled."""

import frappe


def execute() -> None:
    from paystack_frappe.install import ensure_roles, register_enabled_settings

    ensure_roles()
    register_enabled_settings()
    frappe.db.commit()
