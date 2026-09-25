import frappe

REFUND_LOG = "Paystack Refund Log"

UNREVERSED_NOTE = (
    "Refund was sent while this log was submitted, so no reversal Payment "
    "Entry was booked. Book the reversal manually."
)


def execute() -> None:
    """15.x: drop the submittable columns from Paystack Refund Log."""
    if frappe.db.has_column(REFUND_LOG, "reversal_payment_entry"):
        frappe.db.sql(
            """update `tabPaystack Refund Log` set errors = %s
            where docstatus > 0 and status in ('Processed', 'Completed') and coalesce(reversal_payment_entry, '') = ''""",
            (UNREVERSED_NOTE,),
        )
    frappe.db.sql("update `tabPaystack Refund Log` set docstatus = 0 where docstatus > 0")
    if frappe.db.has_column(REFUND_LOG, "amended_from"):
        frappe.db.sql_ddl(f"ALTER TABLE `tab{REFUND_LOG}` DROP COLUMN `amended_from`")
