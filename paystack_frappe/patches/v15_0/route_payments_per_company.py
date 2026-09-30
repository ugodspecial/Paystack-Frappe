"""
15.x: hold each company's Paystack collection to its own gateway setting.

Only meaningful with ERPNext (company routing); a no-op otherwise.
"""

import frappe

PAYMENT_LOG = "Paystack Payment Log"


def execute() -> None:
    from paystack_frappe.integrations import erpnext

    if not erpnext.is_available() or not all(frappe.db.has_column(PAYMENT_LOG, c) for c in ("company", "payment_request")):
        return
    from paystack_frappe.install import register_enabled_settings
    from paystack_frappe.integrations.erpnext.setup import create_payment_gateway_accounts_for_existing_settings

    create_payment_gateway_accounts_for_existing_settings()
    register_enabled_settings()
    for row in frappe.db.sql(
        """select name, company, payment_request from `tabPaystack Payment Log`
        where status = 'Pending' and coalesce(payment_request, '') != ''""",
        as_dict=True,
    ):
        company = frappe.db.get_value("Payment Request", row.payment_request, "company")
        if company and company != row.company:
            frappe.db.sql("update `tabPaystack Payment Log` set company = %s where name = %s", (company, row.name))
    frappe.db.commit()
