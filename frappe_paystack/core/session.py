"""
Payment sessions: one Paystack Payment Log per payable intent.

A session holds what the consumer asked for (its original kwargs, verbatim),
what Paystack is asked to charge, who started it and who the consumer
callback runs as. Checkout attempts get their own Paystack references
(`<session>`, `<session>-2`, ...), because Paystack refuses a reused
reference.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional

import frappe
from frappe import _
from frappe.integrations.utils import create_request_log
from frappe.utils import add_to_date, cint, get_datetime, get_url, now_datetime

from frappe_paystack.core import money
from frappe_paystack.core.adapters import get_adapter
from frappe_paystack.core.constants import (
    DEFAULT_GATEWAY_NAME,
    EXPIRED,
    HOOK_SESSION_CREATED,
    NOTIFY_PENDING,
    PAYMENT_LOG,
    PAYSTACK_SERVICE,
    PENDING,
    RETRYABLE_STATUSES,
)
from frappe_paystack.core.users import current_user, impersonator, resolve_run_as_user

# kwargs of the Payments v1 get_payment_url contract that the core reads.
CONTRACT_KEYS = (
    "amount",
    "currency",
    "title",
    "description",
    "reference_doctype",
    "reference_docname",
    "payer_email",
    "payer_name",
    "order_id",
    "redirect_to",
    "payment_gateway",
)

ATTEMPT_SUFFIX = re.compile(r"^(?P<session>.+?)-(?P<attempt>\d+)$")


def checkout_url(session_name: str) -> str:
    return get_url(f"/paystack-checkout/{session_name}")


def _json_default(value: Any) -> str:
    return str(value)


def normalise_request(kwargs: dict) -> frappe._dict:
    """Return the contract fields of get_payment_url kwargs, cleaned."""
    request = frappe._dict({key: kwargs.get(key) for key in CONTRACT_KEYS})
    request.currency = money.clean_currency(request.currency)
    request.payer_email = (request.payer_email or "").strip()
    if request.payer_email in ("Guest", "Administrator"):
        request.payer_email = ""
    if request.payer_email and not frappe.utils.validate_email_address(request.payer_email):
        request.payer_email = ""
    if bool(request.reference_doctype) ^ bool(request.reference_docname):
        frappe.throw(_("reference_doctype and reference_docname must be passed together."))
    return request


def idempotency_key(setting_name: str, request: dict, run_as_user: str, extras: dict) -> str:
    """Hash the fields that make two get_payment_url calls the same payment intent."""
    material = {
        "setting": setting_name,
        "reference_doctype": request.get("reference_doctype"),
        "reference_docname": request.get("reference_docname"),
        "amount": str(money.to_decimal(request.get("amount"))),
        "currency": request.get("currency"),
        "payer_email": request.get("payer_email"),
        "order_id": request.get("order_id"),
        "run_as_user": run_as_user,
        "extras": extras,
    }
    digest = hashlib.sha256(json.dumps(material, sort_keys=True, default=_json_default).encode()).hexdigest()
    return f"v1:{digest}"


def find_open_session(key: str) -> Optional[str]:
    """Return an unexpired Pending session created for the same intent."""
    for row in frappe.get_all(
        PAYMENT_LOG,
        filters={"idempotency_key": key, "status": PENDING},
        fields=["name", "expires_at"],
        order_by="creation desc",
        limit=5,
    ):
        if not row.expires_at or get_datetime(row.expires_at) > now_datetime():
            return row.name
    return None


def create_session(
    controller: Any,
    kwargs: dict,
    payer_user: Optional[str] = None,
    reuse: bool = True,
) -> Any:
    """
    Create (or reuse) the session for a get_payment_url call and return it.

    `controller` is the Paystack Gateway Setting the consumer resolved through
    Payments. The reference adapter and `paystack_account_resolvers` may route
    the payment to another enabled account.
    """
    from frappe_paystack.core.accounts import resolve_setting

    request = normalise_request(kwargs)
    setting = resolve_setting(controller, request.reference_doctype, request.reference_docname)
    if not setting.enabled:
        frappe.throw(_("Paystack account {0} is not enabled.").format(setting.name))

    adapter = get_adapter(request.reference_doctype)
    request = frappe._dict(adapter.prepare_request(request, setting) or request)

    currency = money.ensure_supported(request.currency, setting.get_allowed_currencies())
    amount = money.validate_amount(request.amount, currency)
    request.currency = currency

    run_as_user = resolve_run_as_user(payer_user)
    extras = {k: v for k, v in kwargs.items() if k not in CONTRACT_KEYS}
    key = idempotency_key(setting.name, {**request, "amount": amount}, run_as_user, extras)

    if reuse:
        existing = find_open_session(key)
        if existing:
            return frappe.get_doc(PAYMENT_LOG, existing)

    original = dict(kwargs)
    original.setdefault("payment_gateway", request.payment_gateway or gateway_for(setting.name))

    log = frappe.new_doc(PAYMENT_LOG)
    log.update(
        {
            "status": PENDING,
            "gateway_setting": setting.name,
            "payment_gateway": original.get("payment_gateway"),
            "linked_doctype": request.reference_doctype,
            "linked_docname": request.reference_docname,
            "amount": float(amount),
            "currency": currency,
            "title": (request.title or "")[:140],
            "description": request.description,
            "payer_email": request.payer_email,
            "payer_name": (request.payer_name or "")[:140],
            "redirect_to": request.redirect_to,
            "order_id": request.order_id,
            "initiated_by": current_user(),
            "impersonated_by": impersonator(),
            "run_as_user": run_as_user,
            "notification_status": NOTIFY_PENDING,
            "idempotency_key": key,
        }
    )
    hours = cint(setting.payment_link_validity_hours)
    if hours > 0:
        log.expires_at = add_to_date(now_datetime(), hours=hours)

    log.flags.ignore_permissions = True
    log.flags.ignore_links = True
    log.insert()

    # The consumer sees its own order_id when it passed one; otherwise the
    # session name, which is unique per payment (LMS stores it in a unique column).
    original.setdefault("order_id", log.name)
    log.db_set("request_data", json.dumps(original, default=_json_default), update_modified=False)
    if not log.order_id:
        log.db_set("order_id", original["order_id"], update_modified=False)

    adapter.on_session_created(log)
    from frappe_paystack.core.notify import broadcast

    broadcast(HOOK_SESSION_CREATED, log)

    # An Integration Request owned by the caller, referencing the consumer's
    # document, is what Payments consumers (e.g. LMS) fall back to.
    try:
        request_log = create_request_log(
            original,
            service_name=PAYSTACK_SERVICE,
            status="Queued",
            reference_doctype=request.reference_doctype,
            reference_docname=request.reference_docname,
        )
        log.db_set("integration_request", request_log.name, update_modified=False)
    except Exception:
        frappe.log_error(title=f"Paystack: could not file the request log for {log.name}", message=frappe.get_traceback())

    return log


def gateway_for(setting_name: str) -> str:
    """Return the Payment Gateway name a setting is reachable through."""
    from frappe_paystack.core.accounts import gateway_name_for

    if frappe.db.get_value("Payment Gateway", DEFAULT_GATEWAY_NAME, "gateway_controller") == setting_name:
        return DEFAULT_GATEWAY_NAME
    separate = gateway_name_for(setting_name)
    if frappe.db.exists("Payment Gateway", separate):
        return separate
    return DEFAULT_GATEWAY_NAME


def lock(session_name: str) -> Any:
    """Lock the session row for the rest of the transaction and return a fresh document."""
    if not frappe.db.get_value(PAYMENT_LOG, session_name, "name", for_update=True):
        frappe.throw(_("Payment {0} was not found.").format(session_name), frappe.DoesNotExistError)
    frappe.clear_document_cache(PAYMENT_LOG, session_name)
    return frappe.get_doc(PAYMENT_LOG, session_name)


def attempt_reference(session_name: str, attempt: int) -> str:
    return session_name if attempt <= 1 else f"{session_name}-{attempt}"


def session_for_reference(reference: Optional[str]) -> Optional[str]:
    """Return the session a Paystack reference belongs to (`<session>` or `<session>-<n>`)."""
    if not reference:
        return None
    reference = str(reference).strip()
    if frappe.db.exists(PAYMENT_LOG, reference):
        return reference
    match = ATTEMPT_SUFFIX.match(reference)
    if match and frappe.db.exists(PAYMENT_LOG, match.group("session")):
        return match.group("session")
    return frappe.db.get_value(PAYMENT_LOG, {"payment_reference": reference}, "name")


def reference_belongs(session: Any, reference: Optional[str]) -> bool:
    if not reference:
        return False
    reference = str(reference)
    if reference in (session.name, session.payment_reference):
        return True
    match = ATTEMPT_SUFFIX.match(reference)
    return bool(match and match.group("session") == session.name and cint(match.group("attempt")) <= cint(session.attempt_count))


def is_expired(session: Any) -> bool:
    return bool(session.expires_at) and get_datetime(session.expires_at) < now_datetime()


def request_data(session: Any) -> frappe._dict:
    try:
        return frappe._dict(json.loads(session.request_data or "{}"))
    except ValueError:
        return frappe._dict()


def expire_if_due(session: Any) -> bool:
    """
    Mark an unpaid session Expired once its window closes. Returns True if it changed.

    Failed and cancelled attempts could be retried while the link was open;
    after expiry they cannot, so they expire too. A capture that arrives later
    still settles (see lifecycle.apply_transaction).
    """
    if session.status in RETRYABLE_STATUSES and is_expired(session):
        session.db_set("status", EXPIRED, update_modified=True)
        return True
    return False
