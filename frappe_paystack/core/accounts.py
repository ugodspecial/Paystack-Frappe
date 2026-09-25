"""
Paystack accounts (Paystack Gateway Setting records) and their Payment Gateways.

Each enabled setting is one Paystack account. The Payment Gateway "Paystack"
points at the default account, which keeps existing Payment Gateway Accounts
and Payment Requests working. A setting may also register itself as its own
Payment Gateway ("Paystack-<name>") so that apps such as LMS can select it by
name.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
from typing import Any, List, Optional

import frappe
from frappe import _
from frappe.utils import call_hook_method

from frappe_paystack.core.constants import (
    DEFAULT_GATEWAY_NAME,
    GATEWAY_NAME_PREFIX,
    GATEWAY_SETTING,
    HOOK_ACCOUNT_RESOLVERS,
)

PAYMENT_GATEWAY = "Payment Gateway"


def hmac_sha512(payload: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha512).hexdigest()


def verify_signature(payload: bytes, signature: Optional[str], secret: Optional[str]) -> bool:
    """Compare Paystack's x-paystack-signature with the HMAC-SHA512 of the raw body."""
    if not signature or not secret:
        return False
    return hmac.compare_digest(hmac_sha512(payload or b"", secret), signature.strip())


def is_ip_allowed(allowed_ips: Optional[str], request_ip: Optional[str]) -> bool:
    """
    Return True when request_ip is in the allowlist.

    Entries are addresses or CIDR ranges separated by commas or newlines. An
    empty allowlist allows every address.
    """
    if not (allowed_ips or "").strip():
        return True
    if not request_ip:
        return False
    try:
        address = ipaddress.ip_address(request_ip.strip())
    except ValueError:
        return False

    for entry in allowed_ips.replace(",", "\n").split("\n"):
        entry = entry.strip()
        if not entry:
            continue
        try:
            if address in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


def gateway_name_for(setting_name: str) -> str:
    return f"{GATEWAY_NAME_PREFIX}{setting_name}"


def enabled_settings() -> List[str]:
    return frappe.get_all(GATEWAY_SETTING, filters={"enabled": 1}, pluck="name", order_by="creation asc")


def get_setting(name: Optional[str]) -> Optional[Any]:
    if not name or not frappe.db.exists(GATEWAY_SETTING, name):
        return None
    return frappe.get_cached_doc(GATEWAY_SETTING, name)


def default_setting_name() -> Optional[str]:
    """Return the setting the "Paystack" Payment Gateway points at, else the oldest enabled one."""
    controller = frappe.db.get_value(PAYMENT_GATEWAY, DEFAULT_GATEWAY_NAME, "gateway_controller")
    if controller and frappe.db.get_value(GATEWAY_SETTING, controller, "enabled"):
        return controller
    names = enabled_settings()
    return names[0] if names else None


def resolve_setting(
    controller: Any,
    reference_doctype: Optional[str],
    reference_docname: Optional[str],
) -> Any:
    """
    Return the setting that should collect a payment.

    Order: the reference DocType's adapter, then every `paystack_account_resolvers`
    hook, then the controller the consumer resolved through Payments.
    """
    from frappe_paystack.core.adapters import get_adapter

    candidates = []
    if reference_doctype and reference_docname:
        candidates.append(get_adapter(reference_doctype).resolve_account(reference_doctype, reference_docname))
        for path in frappe.get_hooks(HOOK_ACCOUNT_RESOLVERS) or []:
            candidates.append(frappe.get_attr(path)(reference_doctype, reference_docname, controller.name))

    for name in candidates:
        if not name or name == controller.name:
            continue
        setting = frappe.get_doc(GATEWAY_SETTING, name)
        if not setting.enabled:
            frappe.throw(_("Paystack account {0} is disabled.").format(name))
        return setting

    return controller


def settings_for_signature(payload: bytes, signature: Optional[str]) -> Optional[Any]:
    """
    Return the enabled setting whose secret signed the payload.

    A payload that more than one account verifies is refused (and logged),
    because the event could not be attributed to one account.
    """
    if not signature:
        return None

    matched = []
    for name in enabled_settings():
        setting = frappe.get_doc(GATEWAY_SETTING, name)
        try:
            secret = setting.get_webhook_secret()
        except Exception:
            # One account whose secret cannot be decrypted (e.g. a site restored without
            # its encryption key) must not stop webhooks for the others.
            frappe.clear_messages()
            frappe.log_error(title=f"Paystack: cannot read the webhook secret of {name}", message=frappe.get_traceback())
            continue
        if verify_signature(payload, signature, secret):
            matched.append(setting)

    if len(matched) > 1:
        frappe.log_error(
            title="Paystack webhook: one signing secret serves several accounts",
            message=", ".join(s.name for s in matched),
        )
        return None

    return matched[0] if matched else None


# ---------------------------------------------------------------------------
# Payment Gateway registration (frappe/payments)
# ---------------------------------------------------------------------------


def ensure_payment_gateway(gateway_name: str, setting_name: str) -> None:
    """Create or re-point a Payment Gateway record at a setting."""
    from payments.utils import create_payment_gateway

    if not frappe.db.exists(PAYMENT_GATEWAY, gateway_name):
        create_payment_gateway(gateway_name, settings=GATEWAY_SETTING, controller=setting_name)
        return

    current = frappe.db.get_value(PAYMENT_GATEWAY, gateway_name, ["gateway_settings", "gateway_controller"], as_dict=True)
    if current.gateway_settings != GATEWAY_SETTING or current.gateway_controller != setting_name:
        frappe.db.set_value(
            PAYMENT_GATEWAY,
            gateway_name,
            {"gateway_settings": GATEWAY_SETTING, "gateway_controller": setting_name},
            update_modified=False,
        )
        frappe.clear_document_cache(PAYMENT_GATEWAY, gateway_name)


def register_setting(setting: Any) -> List[str]:
    """
    Register an enabled setting with Payments and broadcast `payment_gateway_enabled`.

    Returns the Payment Gateway names that point at the setting. ERPNext
    subscribes to `payment_gateway_enabled` and creates a Payment Gateway Account
    for its default company; nothing here depends on it.
    """
    gateways = []

    current_default = frappe.db.get_value(PAYMENT_GATEWAY, DEFAULT_GATEWAY_NAME, "gateway_controller")
    default_is_usable = bool(
        current_default
        and current_default != setting.name
        and frappe.db.get_value(GATEWAY_SETTING, current_default, "enabled")
    )
    if not default_is_usable:
        ensure_payment_gateway(DEFAULT_GATEWAY_NAME, setting.name)
        gateways.append(DEFAULT_GATEWAY_NAME)

    if setting.get("separate_payment_gateway"):
        ensure_payment_gateway(gateway_name_for(setting.name), setting.name)
        gateways.append(gateway_name_for(setting.name))

    for gateway in gateways:
        try:
            call_hook_method("payment_gateway_enabled", gateway=gateway)
        except Exception:
            frappe.log_error(
                title=f"Paystack: a payment_gateway_enabled subscriber failed for {gateway}",
                message=frappe.get_traceback(),
            )

    return gateways


def unregister_setting(setting_name: str) -> None:
    """Move gateways off a setting that is disabled or deleted."""
    separate = gateway_name_for(setting_name)
    if frappe.db.exists(PAYMENT_GATEWAY, separate):
        # Keep the record if something still links to it (e.g. an ERPNext
        # Payment Gateway Account); it resolves again when re-enabled.
        frappe.db.set_value(PAYMENT_GATEWAY, separate, "gateway_controller", setting_name, update_modified=False)

    if frappe.db.get_value(PAYMENT_GATEWAY, DEFAULT_GATEWAY_NAME, "gateway_controller") != setting_name:
        return

    replacement = frappe.db.get_value(
        GATEWAY_SETTING,
        {"name": ["!=", setting_name], "enabled": 1},
        "name",
        order_by="creation asc",
    )
    if replacement:
        ensure_payment_gateway(DEFAULT_GATEWAY_NAME, replacement)


def gateways_for_setting(setting_name: str) -> List[str]:
    return frappe.get_all(
        PAYMENT_GATEWAY,
        filters={"gateway_settings": GATEWAY_SETTING, "gateway_controller": setting_name},
        pluck="name",
    )
