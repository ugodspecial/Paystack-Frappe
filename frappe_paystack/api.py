"""
Whitelisted endpoints.

The webhook path (/api/method/frappe_paystack.api.paystack_webhook) and the
checkout endpoints are generic. The ERPNext endpoints that used to live here
(create_payment_link, saved cards, company checks) keep their dotted paths as
thin shims into frappe_paystack.integrations.erpnext.api, which imports ERPNext
lazily; they are only usable when ERPNext is installed.
"""

from functools import wraps
from typing import Any, Optional

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from frappe_paystack.core import webhook as core_webhook
from frappe_paystack.core.constants import PAYMENT_LOG

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
def start_checkout(reference: str, email: Optional[str] = None) -> dict:
    """Open (or reuse) the Paystack transaction behind a checkout link."""
    from frappe_paystack.core.lifecycle import start_checkout as start

    return start(_session_or_404(reference), email=email)


@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep - guest by design; rate limited, POST only
@rate_limit(limit=CHECKOUT_LIMIT, seconds=CHECKOUT_WINDOW)
def verify_checkout(reference: str, transaction_reference: Optional[str] = None) -> dict:
    """Verify a checkout with Paystack after the popup or redirect returns; returns status and redirect."""
    from frappe_paystack.core.constants import VIA_CALLBACK
    from frappe_paystack.core.lifecycle import verify

    return verify(_session_or_404(reference), reference=transaction_reference, via=VIA_CALLBACK)


@frappe.whitelist(allow_guest=True)  # nosemgrep - guest by design; returns safe fields only
@rate_limit(limit=CHECKOUT_LIMIT * 4, seconds=CHECKOUT_WINDOW)
def get_payment_status(reference: str) -> dict:
    """The checkout page's view of a payment (no secrets, no personal data beyond the payer email)."""
    from frappe_paystack.core.portal import checkout_context

    return checkout_context(_session_or_404(reference)) or {}


@frappe.whitelist()
def payment_link_qr(reference: str) -> str:
    """A QR code (SVG data URI) for a checkout link, for users who can read the payment."""
    from frappe_paystack.core.qr import qr_data_uri
    from frappe_paystack.core.session import checkout_url

    doc = frappe.get_doc(PAYMENT_LOG, _session_or_404(reference))
    if not frappe.has_permission(PAYMENT_LOG, "read", doc=doc):
        if not (doc.linked_doctype and frappe.has_permission(doc.linked_doctype, "read", doc=doc.linked_docname)):
            frappe.throw(_("Not permitted"), frappe.PermissionError)
    return qr_data_uri(checkout_url(doc.name))


@frappe.whitelist()
def initiate_refund_from_log(payment_log_name: str, amount: Any = None, reason: Optional[str] = None) -> str:
    """Refund a captured payment (charge currency, major units). Returns the Refund Log name."""
    from frappe_paystack.core.permissions import check_money_permission
    from frappe_paystack.core.refunds import refund_payment

    check_money_permission(_("You are not permitted to refund Paystack payments."))
    frappe.get_doc(PAYMENT_LOG, payment_log_name).check_permission("read")
    return refund_payment(payment_log_name, amount=amount, reason=reason or _("Manual refund")).name


# ---------------------------------------------------------------------------
# Upstream 15.x names
# ---------------------------------------------------------------------------


@frappe.whitelist(allow_guest=True)  # nosemgrep - guest by design; returns safe fields only
def validate_payment_link(docname: str) -> dict:
    """Deprecated: use get_payment_status."""
    from frappe_paystack.core.portal import checkout_context

    return checkout_context(docname) or {}


@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep - guest by design; rate limited, POST only
@rate_limit(limit=CHECKOUT_LIMIT, seconds=CHECKOUT_WINDOW)
def start_hosted_checkout(reference: str, email: Optional[str] = None) -> str:
    """Deprecated: use start_checkout. Returns the Paystack-hosted checkout URL."""
    from frappe_paystack.core.lifecycle import start_checkout as start

    return start(_session_or_404(reference), email=email).get("authorization_url")


@frappe.whitelist()
def create_payment_link(doctype: str, docname: str, amount: Any = None, currency: Optional[str] = None) -> str:
    """ERPNext: a checkout URL for a Sales Invoice, Sales Order, Dunning or POS Invoice."""
    from frappe_paystack.integrations.erpnext.api import create_payment_link as create

    return create(doctype, docname, amount, currency)


@frappe.whitelist()
def is_enabled_for_company(company: str) -> bool:
    """ERPNext: whether an enabled Paystack account collects for a company."""
    from frappe_paystack.integrations.erpnext.api import is_enabled_for_company as check

    return check(company)


@frappe.whitelist()
def saved_cards(customer: str, company: Optional[str] = None) -> list:
    """ERPNext: a Customer's chargeable saved cards (no authorization codes)."""
    from frappe_paystack.integrations.erpnext.api import saved_cards as cards

    return cards(customer, company)


@frappe.whitelist()
def charge_saved_card(doctype: str, docname: str, authorization: str, amount: Any = None) -> str:
    """ERPNext: charge a Customer's saved card for a document; returns the payment."""
    from frappe_paystack.integrations.erpnext.api import charge_saved_card as charge

    return charge(doctype, docname, authorization, amount)
