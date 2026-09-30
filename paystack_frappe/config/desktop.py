from frappe import _


def get_data() -> list:
    return [
        {
            "module_name": "Paystack Frappe",
            "category": "Modules",
            "label": _("Paystack Frappe"),
            "icon": "octicon octicon-credit-card",
            "type": "module",
            "hidden": 0,
        }
    ]
