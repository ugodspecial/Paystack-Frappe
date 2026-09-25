"""
Reference DocType adapters.

The gateway never interprets the consuming document. When a consuming app
needs behaviour beyond the Payments v1 contract (``on_payment_authorized``),
it registers an adapter for its DocType in its own ``hooks.py``::

    paystack_reference_adapters = {
        "My DocType": "my_app.paystack.MyDocTypeAdapter",
    }

Adapters subclass ``ReferenceAdapter`` and override only what they need. The
default adapter implements the v1 contract, so Web Forms, LMS, Education and
any other Payments consumer work without registering anything.
"""

from __future__ import annotations

from typing import Any, Optional

import frappe

from frappe_paystack.core.constants import HOOK_REFERENCE_ADAPTERS


class ReferenceAdapter:
    """Default behaviour for a reference DocType (the Payments v1 contract)."""

    def __init__(self, doctype: Optional[str] = None):
        self.doctype = doctype

    # ----------------------------------------------------------------- session creation

    def resolve_account(self, reference_doctype: str, reference_docname: str) -> Optional[str]:
        """Return the Paystack Gateway Setting that should collect this document, or None."""
        return None

    def prepare_request(self, request: dict, setting: Any) -> dict:
        """
        Adjust a normalised payment request before a session is created.

        May change ``amount`` and ``currency`` (the amount Paystack is asked to
        charge). The consumer's original kwargs are kept on the session as they
        were passed.
        """
        return request

    def on_session_created(self, session: Any) -> None:
        """Stamp adapter-owned fields on a new session (e.g. the company it books against)."""

    def get_payer(self, session: Any) -> dict:
        """
        Return the payer as ``{"party_type", "party", "email", "name"}``.

        Keys may be missing. The default knows only the session's payer email.
        """
        return {}

    # ----------------------------------------------------------------- checkout

    def is_payable(self, session: Any) -> tuple:
        """Return (payable, reason). The session's own status and expiry are checked by the core."""
        return True, None

    def get_checkout_context(self, session: Any) -> list:
        """Return extra ``{"label", "value"}`` rows for the checkout page."""
        return []

    # ----------------------------------------------------------------- completion

    def notification_user(self, session: Any) -> Optional[str]:
        """
        Return the user the consumer callback runs as, or None for the session default.

        The default (None) is the session's ``run_as_user``: the user who started
        the payment.
        """
        return None

    def notify_success(self, session: Any, reference_doc: Any) -> Any:
        """
        Tell the consumer the payment succeeded and return its redirect, if any.

        The default is the Payments v1 contract. ``frappe.flags.data`` is set
        by the caller.
        """
        return reference_doc.run_method("on_payment_authorized", "Completed")

    def notify_failure(self, session: Any, reference_doc: Any, message: str) -> None:
        """Tell the consumer the payment failed (a no-op unless it implements the hook)."""
        if hasattr(reference_doc, "on_payment_failed"):
            reference_doc.run_method("on_payment_failed", message)

    def after_success(self, session: Any) -> None:
        """Run follow-up work after the consumer was notified (e.g. accounting)."""

    def on_refund(self, refund: Any) -> None:
        """React to a refund status change."""


def _registry() -> dict:
    """
    Return {doctype: dotted path}.

    frappe.get_hooks merges dict hooks from every installed app into
    {doctype: [path, ...]} in app install order, so the last path wins and an
    app installed later can override an earlier registration.
    """
    hooks = frappe.get_hooks(HOOK_REFERENCE_ADAPTERS) or {}
    registry = {}
    if isinstance(hooks, dict):
        for doctype, paths in hooks.items():
            if isinstance(paths, (list, tuple)) and paths:
                registry[doctype] = paths[-1]
            elif isinstance(paths, str):
                registry[doctype] = paths
    return registry


def get_adapter(doctype: Optional[str]) -> ReferenceAdapter:
    """Return the adapter registered for a DocType, or the default adapter."""
    cache = getattr(frappe.local, "paystack_adapters", None)
    if cache is None:
        cache = frappe.local.paystack_adapters = {}

    if doctype in cache:
        return cache[doctype]

    adapter: ReferenceAdapter
    path = _registry().get(doctype) if doctype else None
    if path:
        adapter = frappe.get_attr(path)(doctype)
    else:
        adapter = ReferenceAdapter(doctype)

    cache[doctype] = adapter
    return adapter


def clear_adapter_cache() -> None:
    frappe.local.paystack_adapters = {}
