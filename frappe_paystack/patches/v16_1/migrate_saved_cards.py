"""15.x saved cards belonged to a Customer (and company); 16.1 stores a generic payer and account."""

import frappe

AUTHORIZATION = "Paystack Customer Authorization"
GATEWAY_SETTING = "Paystack Gateway Setting"


def execute() -> None:
    if frappe.db.has_column(AUTHORIZATION, "customer"):
        frappe.db.sql(
            f"""update `tab{AUTHORIZATION}` set party_type = 'Customer', party = customer
            where coalesce(customer, '') != '' and coalesce(party, '') = ''"""
        )
    if frappe.db.has_column(AUTHORIZATION, "company") and frappe.db.has_column(GATEWAY_SETTING, "company"):
        for row in frappe.db.sql(
            f"""select name, company from `tab{AUTHORIZATION}`
            where coalesce(company, '') != '' and coalesce(gateway_setting, '') = ''""",
            as_dict=True,
        ):
            setting = frappe.db.get_value(GATEWAY_SETTING, {"company": row.company}, "name", order_by="enabled desc, creation asc")
            if setting:
                frappe.db.set_value(AUTHORIZATION, row.name, "gateway_setting", setting, update_modified=False)
    frappe.db.commit()
