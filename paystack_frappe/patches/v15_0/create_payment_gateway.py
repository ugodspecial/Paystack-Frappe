"""Register Paystack as a Payment Gateway on an existing site (ERPNext pieces only with ERPNext)."""

import frappe


def execute() -> None:
    from paystack_frappe.install import register_enabled_settings
    from paystack_frappe.integrations import erpnext

    if erpnext.is_available():
        from paystack_frappe.integrations.erpnext.setup import (
            create_payment_gateway_accounts_for_existing_settings,
            ensure_email_payment_type,
            ensure_mode_of_payment,
        )

        ensure_mode_of_payment()
        ensure_email_payment_type()
        register_enabled_settings()
        create_payment_gateway_accounts_for_existing_settings()
    else:
        register_enabled_settings()
    frappe.db.commit()
