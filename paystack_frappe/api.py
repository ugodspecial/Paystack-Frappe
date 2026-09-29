"""
Whitelisted endpoints.

The webhook path (/api/method/paystack_frappe.api.paystack_webhook) and the
checkout endpoints are generic. The ERPNext endpoints (payment links, saved
cards, company checks) are whitelisted where they are implemented, in
paystack_frappe.integrations.erpnext.api, which imports ERPNext lazily; they
are only usable when ERPNext is installed.
"""

from functools import wraps
from typing import Any, Optional

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from paystack_frappe.core import webhook as core_webhook
from paystack_frappe.core.constants import PAYMENT_LOG
from paystack_frappe.core.urls import remember_origin

WEBHOOK_IP_LIMIT = 100
WEBHOOK_RATE_WINDOW = 60
CHECKOUT_LIMIT = 30
CHECKOUT_WINDOW = 60


def rate_limited_webhook(fn):
    """Apply the app-wide webhook ceiling and record throttled requests."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        request_ip = getattr(frappe.local, "request_ip", None)
        if core_webhook.global_rate_limit_exceeded():
            core_webhook.record_rate_limit_violation("global", request_ip)
            frappe.throw(_("Too many Paystack webhooks. Retry shortly."), frappe.RateLimitExceededError)
        try:
            return fn(*args, **kwargs)
        except frappe.RateLimitExceededError:
            core_webhook.record_rate_limit_violation("ip", request_ip)
            raise

    return wrapper


@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep - guest by design; the HMAC signature guards it
@rate_limited_webhook
@rate_limit(limit=WEBHOOK_IP_LIMIT, seconds=WEBHOOK_RATE_WINDOW)
def paystack_webhook() -> None:
    """Receive a Paystack webhook: authenticate, record, acknowledge, process in the background."""
    payload, signature, request_ip = core_webhook.request_payload()
    core_webhook.receive(payload, signature, request_ip)
    frappe.local.response["http_status_code"] = 200


# ---------------------------------------------------------------------------
# Checkout (guest-safe: the unguessable session name is the capability)
# ---------------------------------------------------------------------------


def _session_or_404(reference: str) -> str:
    if not reference or not frappe.db.exists(PAYMENT_LOG, reference):
        frappe.throw(_("This payment link is not available."), frappe.DoesNotExistError)
    return reference


@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep - guest by design; rate limited, POST only
@rate_limit(limit=CHECKOUT_LIMIT, seconds=CHECKOUT_WINDOW)
def start_checkout(reference: str, email: Optional[str] = None, origin: Optional[str] = None) -> dict:
    """
    Open (or reuse) the Paystack transaction behind a checkout link.

    `origin` is the checkout page's own `window.location.origin`. It is used -
    after being checked against this site's hosts - for the Paystack
    `callback_url`, so the payer is returned to the address (and the port) they
    are actually browsing.
    """
    from paystack_frappe.core.lifecycle import start_checkout as start

    remember_origin(origin)
    return start(_session_or_404(reference), email=email)


@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep - guest by design; rate limited, POST only
@rate_limit(limit=CHECKOUT_LIMIT, seconds=CHECKOUT_WINDOW)
def verify_checkout(
    reference: str, transaction_reference: Optional[str] = None, origin: Optional[str] = None
) -> dict:
    """Verify a checkout with Paystack after the popup or redirect returns; returns status and redirect."""
    from paystack_frappe.core.constants import VIA_CALLBACK
    from paystack_frappe.core.lifecycle import verify

    remember_origin(origin)
    return verify(_session_or_404(reference), reference=transaction_reference, via=VIA_CALLBACK)


@frappe.whitelist(allow_guest=True)  # nosemgrep - guest by design; returns safe fields only
@rate_limit(limit=CHECKOUT_LIMIT * 4, seconds=CHECKOUT_WINDOW)
def get_payment_status(reference: str) -> dict:
    """The checkout page's view of a payment (no secrets, no personal data beyond the payer email)."""
    from paystack_frappe.core.portal import checkout_context

    return checkout_context(_session_or_404(reference)) or {}


@frappe.whitelist()
def payment_link_qr(reference: str, origin: Optional[str] = None) -> str:
    """A QR code (SVG data URI) for a checkout link, for users who can read the payment."""
    from paystack_frappe.core.qr import qr_data_uri
    from paystack_frappe.core.session import checkout_url

    remember_origin(origin)
    doc = frappe.get_doc(PAYMENT_LOG, _session_or_404(reference))
    if not frappe.has_permission(PAYMENT_LOG, "read", doc=doc):
        if not (doc.linked_doctype and frappe.has_permission(doc.linked_doctype, "read", doc=doc.linked_docname)):
            frappe.throw(_("Not permitted"), frappe.PermissionError)
    return qr_data_uri(checkout_url(doc.name))


@frappe.whitelist()
def initiate_refund_from_log(payment_log_name: str, amount: Any = None, reason: Optional[str] = None) -> str:
    """Refund a captured payment (charge currency, major units). Returns the Refund Log name."""
    from paystack_frappe.core.permissions import check_money_permission
    from paystack_frappe.core.refunds import refund_payment

    check_money_permission(_("You are not permitted to refund Paystack payments."))
    frappe.get_doc(PAYMENT_LOG, payment_log_name).check_permission("read")
    return refund_payment(payment_log_name, amount=amount, reason=reason or _("Manual refund")).name
