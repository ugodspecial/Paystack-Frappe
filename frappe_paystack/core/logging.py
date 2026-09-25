"""Integration Request and Error Log helpers with secret redaction."""

from __future__ import annotations

from typing import Any, Optional

import frappe
from frappe import _

from frappe_paystack.core.constants import PAYSTACK_SERVICE

REDACTED = "***"

# Keys whose values charge a card, authenticate the merchant or identify a card.
SENSITIVE_KEYS = frozenset(
    {
        "authorization_code",
        "secret_key",
        "authorization",  # HTTP header form
        "access_code",
        "bin",
        "account_name",
    }
)


def redact(payload: Any) -> Any:
    """Return a copy of payload with sensitive values replaced, walking dicts and lists."""
    if isinstance(payload, list):
        return [redact(item) for item in payload]
    if not isinstance(payload, dict):
        return payload
    clean = {}
    for key, value in payload.items():
        if isinstance(key, str) and key.lower() in SENSITIVE_KEYS and not isinstance(value, (dict, list)):
            clean[key] = REDACTED if value else value
        else:
            clean[key] = redact(value)
    return clean


def log_integration_request(
    status: str,
    url: str,
    request_data: Any,
    response_data: Any = None,
    error: Optional[str] = None,
    reference_doctype: Optional[str] = None,
    reference_docname: Optional[str] = None,
    request_id: Optional[str] = None,
    is_remote_request: int = 1,
) -> Optional[str]:
    """
    File an Integration Request for a Paystack call or event and return its name.

    Never raises: a failure to log is itself logged, and None is returned.
    """
    try:
        from frappe.integrations.utils import create_request_log

        if reference_docname and not (
            reference_doctype and frappe.db.exists(reference_doctype, reference_docname)
        ):
            reference_doctype = reference_docname = None

        kwargs = {
            "service_name": PAYSTACK_SERVICE,
            "is_remote_request": is_remote_request,
            "status": status,
            "url": url,
            "output": redact(response_data),
            "error": error,
            "reference_doctype": reference_doctype,
            "reference_docname": reference_docname,
        }
        if request_id:
            kwargs["request_id"] = request_id

        request = create_request_log(redact(request_data), **kwargs)
        return request.name
    except Exception:
        frappe.log_error(title="Paystack: failed to log Integration Request", message=frappe.get_traceback())
        return None


def error_log_link(error_log_name: Optional[str]) -> str:
    if not error_log_name:
        return ""
    return f'<a href="/app/error-log/{error_log_name}">{error_log_name}</a>'


def log_error_for(
    title: str,
    message: Optional[str] = None,
    reference_doctype: Optional[str] = None,
    reference_name: Optional[str] = None,
) -> Optional[str]:
    """Write an Error Log against the record it concerns and return its name."""
    if reference_name and not (reference_doctype and frappe.db.exists(reference_doctype, reference_name)):
        reference_doctype = reference_name = None

    try:
        error_log = frappe.log_error(
            title=title[:140],
            message=message or frappe.get_traceback(),
            reference_doctype=reference_doctype,
            reference_name=reference_name,
        )
    except Exception:
        return None
    return getattr(error_log, "name", None)


def record_failure(
    title: str,
    message: Optional[str] = None,
    reference_doctype: Optional[str] = None,
    reference_name: Optional[str] = None,
) -> str:
    """Log an error and return a short message that names the Error Log."""
    name = log_error_for(title, message, reference_doctype, reference_name)
    if not name:
        return _("{0}. See the Error Log for details.").format(title)
    return _("{0}. See Error Log {1}.").format(title, error_log_link(name))
