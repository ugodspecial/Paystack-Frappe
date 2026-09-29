"""
Name the Paystack account on records that only named a company (15.x).

With several companies each record gets its company's account; otherwise the
default account.
"""

import frappe

GATEWAY_SETTING = "Paystack Gateway Setting"
DOCTYPES = ("Paystack Payment Log", "Paystack Settlement", "Paystack Reconciliation Log")


def execute() -> None:
    settings = frappe.db.sql(f"select name, enabled from `tab{GATEWAY_SETTING}` order by enabled desc, creation asc", as_dict=True)
    if not settings:
        return
    default = settings[0].name
    by_company = {}
    if frappe.db.has_column(GATEWAY_SETTING, "company"):
        for row in frappe.db.sql(f"select name, company from `tab{GATEWAY_SETTING}` order by enabled desc, creation asc", as_dict=True):
            if row.company:
                by_company.setdefault(row.company, row.name)

    for doctype in DOCTYPES:
        if not frappe.db.has_column(doctype, "gateway_setting"):
            continue
        if by_company and frappe.db.has_column(doctype, "company"):
            for company, setting in by_company.items():
                frappe.db.sql(
                    f"""update `tab{doctype}` set gateway_setting = %s
                    where coalesce(gateway_setting, '') = '' and company = %s""",
                    (setting, company),
                )
        frappe.db.sql(f"update `tab{doctype}` set gateway_setting = %s where coalesce(gateway_setting, '') = ''", (default,))
    frappe.db.commit()
