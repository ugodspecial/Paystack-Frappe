"""
Rename the app in place: frappe_paystack -> paystack_frappe.

Runs pre_model_sync, before this app's files sync against the new module name,
so the Module Def row (and every row linking to it), the Patch Log entries and
the Scheduled Job Types all carry their new names before any schema is read.
See paystack_frappe/rename_app.py for the bench-level half of the migration.
"""

from paystack_frappe.migration import rename_app_records


def execute() -> None:
    rename_app_records()
