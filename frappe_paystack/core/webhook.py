"""
Paystack webhooks.

The request handler only authenticates and records the event: it matches the
signing account (HMAC-SHA512 of the raw body with the account's secret, or its
optional webhook-secret override), applies the optional IP allowlist, files the
event as an Integration Request keyed by `<event>:<object id>` and answers 200.
Processing happens in a background job (inline under tests), so slow consumer
code never makes Paystack time out and retry. Redelivered events whose first
delivery was processed are acknowledged and skipped; ones that failed or never
ran are processed again. A sweep re-drives events left Queued.
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, now_datetime

from frappe_paystack.core.accounts import is_ip_allowed, settings_for_signature
from frappe_paystack.core.client import PaystackError, client_for
from frappe_paystack.core.constants import (
    CAPTURED_STATUSES,
    EVENT_CHARGE_SUCCESS,
    EVENT_COMPLETED,
    EVENT_FAILED,
    EVENT_QUEUED,
    GATEWAY_SETTING,
    INTEGRATION_REQUEST,
    PAYMENT_LOG,
    PAYSTACK_SERVICE,
    REFUND_EVENTS,
    SETTLEMENT_EVENTS,
    SIGNATURE_HEADER,
    VIA_WEBHOOK,
)
from frappe_paystack.core.logging import log_error_for, log_integration_request, redact

WEBHOOK_URL_PREFIX = "webhook:"
PROCESS_JOB = "frappe_paystack.core.webhook.process_event"

# Fixed-window counters (seconds) and alert thresholds.
RATE_WINDOW = 60
GLOBAL_LIMIT = 1000
VIOLATION_ALERT = 10
SIGNATURE_ALERT = 10

# Events left Queued longer than this are re-driven by the sweep.
STUCK_EVENT_MINUTES = 10

# Unmatched charge handlers other apps may register (e.g. legacy references).
HOOK_UNMATCHED_CHARGE = "paystack_unmatched_charge"


# ---------------------------------------------------------------------------
# Rate limiting and alerting
# ---------------------------------------------------------------------------


def _window_key(scope: str) -> str:
    return frappe.cache.make_key(f"paystack-webhook-{scope}:{int(time.time()) // RATE_WINDOW}")


def count_in_window(scope: str) -> int:
    key = _window_key(scope)
    count = cint(frappe.cache.incrby(key, 1))
    if count == 1:
        frappe.cache.expire(key, RATE_WINDOW)
    return count


def global_rate_limit_exceeded() -> bool:
    return count_in_window("global") > GLOBAL_LIMIT


def record_rate_limit_violation(scope: str, request_ip: Optional[str]) -> None:
    log_integration_request(
        status="Failed",
        url="webhook",
        request_data={"scope": scope, "request_ip": request_ip},
        error=f"Webhook rate limit exceeded ({scope}) for IP {request_ip or 'unknown'}",
    )
    if count_in_window("violations") == VIOLATION_ALERT:
        log_error_for(
            title="Paystack webhook rate limit: sustained violations",
            message=f"{VIOLATION_ALERT} webhook requests were throttled within {RATE_WINDOW}s. "
            f"Latest: {scope} limit, IP {request_ip or 'unknown'}.",
        )


def record_signature_rejection(request_ip: Optional[str]) -> None:
    # The body is not stored: an unauthenticated sender controls it.
    log_integration_request(
        status="Failed",
        url="webhook",
        request_data={"request_ip": request_ip},
        error="Invalid Paystack signature",
    )
    if count_in_window("signatures") == SIGNATURE_ALERT:
        log_error_for(
            title="Paystack webhook: sustained signature rejections",
            message=f"{SIGNATURE_ALERT} webhook requests failed signature verification within {RATE_WINDOW}s. "
            f"Latest source IP: {request_ip or 'unknown'}.",
        )


# ---------------------------------------------------------------------------
# Receiving
# ---------------------------------------------------------------------------


def dedupe_key(event: str, obj: dict) -> str:
    identifier = obj.get("id") or obj.get("reference") or obj.get("refund_reference") or ""
    return f"{event}:{identifier}"[:140]


def receive(payload: bytes, signature: Optional[str], request_ip: Optional[str]) -> dict:
    """
    Authenticate and record one webhook delivery; queue its processing.

    Throws PermissionError for an unverifiable signature or a disallowed IP.
    Returns {"event", "integration_request", "duplicate"}.
    """
    setting = settings_for_signature(payload, signature)
    if not setting:
        record_signature_rejection(request_ip)
        frappe.throw(_("Invalid Paystack signature"), frappe.PermissionError)

    if not is_ip_allowed(setting.allowed_webhook_ips, request_ip):
        log_integration_request(
            status="Failed",
            url="webhook",
            request_data={"request_ip": request_ip, "account": setting.name},
            error=f"IP {request_ip} not in the allowlist of {setting.name}",
        )
        frappe.throw(_("IP not allowed"), frappe.PermissionError)

    try:
        data = json.loads(payload or b"{}")
    except ValueError:
        frappe.throw(_("The webhook body is not valid JSON."))
    if not isinstance(data, dict):
        frappe.throw(_("The webhook body is not a JSON object."))

    event = str(data.get("event") or "")
    obj = data.get("data") if isinstance(data.get("data"), dict) else {}
    key = dedupe_key(event, obj)

    existing = frappe.db.get_value(
        INTEGRATION_REQUEST,
        {"integration_request_service": PAYSTACK_SERVICE, "request_id": key, "url": WEBHOOK_URL_PREFIX + setting.name},
        ["name", "status"],
        as_dict=True,
        order_by="creation desc",
    )
    if existing and existing.status == EVENT_COMPLETED:
        return {"event": event, "integration_request": existing.name, "duplicate": True}

    name = existing.name if existing else log_integration_request(
        status=EVENT_QUEUED,
        url=WEBHOOK_URL_PREFIX + setting.name,
        request_data=data,
        request_id=key,
    )
    if existing:
        frappe.db.set_value(INTEGRATION_REQUEST, name, "status", EVENT_QUEUED, update_modified=True)
    frappe.db.commit()

    if name:
        enqueue_processing(name)
    return {"event": event, "integration_request": name, "duplicate": bool(existing)}


def enqueue_processing(integration_request: str) -> None:
    frappe.enqueue(
        PROCESS_JOB,
        queue="short",
        timeout=300,
        integration_request=integration_request,
        job_id=f"paystack-webhook-{integration_request}",
        deduplicate=True,
        # The event row is committed before this runs, so the job always finds it.
        now=bool(frappe.flags.in_test or frappe.conf.get("paystack_process_webhooks_inline")),
    )


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------


def process_event(integration_request: str) -> Optional[str]:
    """Process one recorded webhook event. Idempotent. Returns the outcome."""
    request = frappe.get_doc(INTEGRATION_REQUEST, integration_request)
    if request.status == EVENT_COMPLETED:
        return "already processed"

    setting_name = (request.url or "")[len(WEBHOOK_URL_PREFIX):]
    if not frappe.db.exists(GATEWAY_SETTING, setting_name):
        request.db_set({"status": EVENT_FAILED, "error": f"Unknown Paystack account {setting_name}"})
        frappe.db.commit()
        return None
    setting = frappe.get_doc(GATEWAY_SETTING, setting_name)

    try:
        data = json.loads(request.data or "{}")
    except ValueError:
        data = {}
    event = str(data.get("event") or "")
    obj = data.get("data") if isinstance(data.get("data"), dict) else {}

    try:
        if event == EVENT_CHARGE_SUCCESS:
            outcome = process_charge(obj, setting, integration_request)
        elif event in REFUND_EVENTS:
            from frappe_paystack.core.refunds import apply_refund_event

            outcome = apply_refund_event(event, obj, setting)
        elif event in SETTLEMENT_EVENTS:
            from frappe_paystack.core.settlements import record_from_webhook

            outcome = record_from_webhook(obj, setting)
        else:
            outcome = f"ignored {event or 'unnamed event'}"
    except Exception as exc:
        frappe.db.rollback()
        log_error_for(title=f"Paystack webhook processing failed: {event}", reference_doctype=INTEGRATION_REQUEST,
                      reference_name=integration_request)
        frappe.db.set_value(
            INTEGRATION_REQUEST,
            integration_request,
            {"status": EVENT_FAILED, "error": str(exc)[:1000]},
            update_modified=True,
        )
        frappe.db.commit()
        return None

    frappe.db.set_value(
        INTEGRATION_REQUEST,
        integration_request,
        {"status": EVENT_COMPLETED, "output": frappe.as_json({"outcome": outcome}), "error": None},
        update_modified=True,
    )
    frappe.db.commit()
    return outcome


def metadata_of(obj: dict) -> dict:
    metadata = obj.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except ValueError:
            metadata = {}
    return metadata if isinstance(metadata, dict) else {}


def session_for_transaction(obj: dict) -> Optional[str]:
    from frappe_paystack.core.session import session_for_reference

    metadata = metadata_of(obj)
    for candidate in (metadata.get("session"), metadata.get("reference")):
        if candidate and frappe.db.exists(PAYMENT_LOG, candidate):
            return candidate
    return session_for_reference(obj.get("reference"))


def process_charge(obj: dict, setting: Any, integration_request: Optional[str] = None) -> str:
    from frappe_paystack.core.lifecycle import OUTCOME_IGNORED, apply_transaction
    from frappe_paystack.core.notify import notify_success

    session_name = session_for_transaction(obj)
    if not session_name:
        for path in frappe.get_hooks(HOOK_UNMATCHED_CHARGE) or []:
            handled = frappe.get_attr(path)(obj, setting)
            if handled:
                return str(handled)
        return f"no payment for reference {obj.get('reference')}"

    if integration_request:
        frappe.db.set_value(
            INTEGRATION_REQUEST,
            integration_request,
            {"reference_doctype": PAYMENT_LOG, "reference_docname": session_name},
            update_modified=False,
        )

    # Verify before trust: the signed payload is authentic, but Paystack's own
    # record is checked when it can be reached.
    transaction = obj
    try:
        verified = client_for(setting, PAYMENT_LOG, session_name).verify_transaction(obj.get("reference"))
        if verified is not None:
            transaction = verified
    except PaystackError:
        pass

    outcome = apply_transaction(session_name, transaction, via=VIA_WEBHOOK, account=setting.name)
    if outcome != OUTCOME_IGNORED and frappe.db.get_value(PAYMENT_LOG, session_name, "status") in CAPTURED_STATUSES:
        notify_success(session_name)
    return outcome


def redrive_stuck_events() -> list:
    """Re-queue webhook events that were recorded but never processed."""
    names = frappe.get_all(
        INTEGRATION_REQUEST,
        filters={
            "integration_request_service": PAYSTACK_SERVICE,
            "status": EVENT_QUEUED,
            "url": ["like", WEBHOOK_URL_PREFIX + "%"],
            "modified": ["<", add_to_date(now_datetime(), minutes=-STUCK_EVENT_MINUTES)],
        },
        pluck="name",
        limit=100,
    )
    for name in names:
        try:
            process_event(name)
        except Exception:
            frappe.db.rollback()
            log_error_for(title=f"Paystack: could not re-drive webhook {name}")
    return names


def request_payload() -> tuple:
    """Return (raw body, signature, source IP) of the current request."""
    payload = frappe.request.get_data(cache=True) if frappe.request else b""
    signature = frappe.get_request_header(SIGNATURE_HEADER)
    request_ip = getattr(frappe.local, "request_ip", None)
    return payload, signature, request_ip


__all__ = ["receive", "process_event", "redact"]
