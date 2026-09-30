"""ERPNext customer portal: a customer's open invoices, Paystack payments and refunds (404 without ERPNext)."""


def get_context(context):
    from paystack_frappe.integrations.erpnext.portal import my_payments_context

    return my_payments_context(context)
