"""
Re-attach the ERPNext-only fields as Custom Fields (same names, same columns).

Runs first in post_model_sync so later patches can read them. No-op without ERPNext.
"""


def execute() -> None:
    from frappe_paystack.integrations import erpnext

    if erpnext.is_available():
        erpnext.activate()
