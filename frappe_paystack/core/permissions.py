"""Who may move money (refunds, saved-card charges, manual completion)."""

from __future__ import annotations

from typing import Optional

import frappe
from frappe import _

from frappe_paystack.core.constants import HOOK_MANAGER_ROLES, PAYSTACK_MANAGER, SYSTEM_MANAGER


def manager_roles() -> set:
    roles = {SYSTEM_MANAGER, PAYSTACK_MANAGER}
    roles.update(frappe.get_hooks(HOOK_MANAGER_ROLES) or [])
    return roles


def can_move_money(user: Optional[str] = None) -> bool:
    user = user or frappe.session.user
    if user == "Administrator":
        return True
    return bool(manager_roles() & set(frappe.get_roles(user)))


def check_money_permission(message: Optional[str] = None) -> None:
    if not can_move_money():
        frappe.throw(message or _("You are not permitted to move Paystack money."), frappe.PermissionError)
