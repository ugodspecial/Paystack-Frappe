"""
Point the Scheduled Job Type rows of the moved jobs at their current methods.

Runs pre_model_sync, so each row keeps its history and enabled state.
"""

from paystack_frappe.migration import rename_scheduled_jobs


def execute() -> None:
    rename_scheduled_jobs()
