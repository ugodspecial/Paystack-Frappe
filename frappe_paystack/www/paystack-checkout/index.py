"""
Public checkout page: /paystack-checkout/<session>.

Also the hosted-checkout return URL: Paystack sends the payer back with
?trxref=<reference>; the payment is verified on the server and the payer is
redirected the way the consuming app asked. See core.portal.page_context.
"""

import frappe

no_cache = 1


def get_context(context):
    from frappe_paystack.core.portal import page_context

    return page_context(frappe.form_dict.reference, frappe.form_dict.get("trxref"), context)
