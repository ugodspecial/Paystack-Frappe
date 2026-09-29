"""Move every human-set reconciliation onto the current override status."""

from paystack_frappe.migration import rename_manual_overrides


def execute() -> None:
    rename_manual_overrides()
