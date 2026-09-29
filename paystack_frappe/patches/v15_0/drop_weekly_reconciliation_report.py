"""Delete the Scheduled Job Type row of the weekly reconciliation report."""

from paystack_frappe.migration import drop_removed_scheduled_jobs


def execute() -> None:
    drop_removed_scheduled_jobs()
