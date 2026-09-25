import frappe
from frappe.utils import flt

PAYMENT_LOG = "Paystack Payment Log"


def execute() -> None:
    """15.x: stamp the refund status on payment logs that already carry refunds."""
    if not frappe.db.has_column(PAYMENT_LOG, "total_refunded"):
        return
    for row in frappe.db.sql(
        "select name, amount_paid, total_refunded from `tabPaystack Payment Log` where total_refunded > 0", as_dict=True
    ):
        status = "Refunded" if flt(row.total_refunded) >= flt(row.amount_paid) else "Partially Refunded"
        frappe.db.sql("update `tabPaystack Payment Log` set status = %s where name = %s", (status, row.name))
