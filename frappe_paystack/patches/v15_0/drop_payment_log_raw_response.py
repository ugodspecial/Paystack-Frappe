import frappe

PAYMENT_LOG = "Paystack Payment Log"


def execute() -> None:
    """15.x: drop raw_response and the submittable columns from Paystack Payment Log."""
    for column in ("raw_response", "amended_from"):
        if frappe.db.has_column(PAYMENT_LOG, column):
            frappe.db.sql_ddl(f"ALTER TABLE `tab{PAYMENT_LOG}` DROP COLUMN `{column}`")
    frappe.db.sql("update `tabPaystack Payment Log` set docstatus = 0 where docstatus = 1")
