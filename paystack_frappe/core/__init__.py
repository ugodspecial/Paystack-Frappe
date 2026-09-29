"""
Paystack gateway core for Frappe Payments.

Everything in this package imports only the standard library, ``frappe``,
``payments`` and ``requests``. It never imports ERPNext and never names a
consuming application; business behaviour is selected by the reference
DocType adapter registry (``core.adapters``). CI enforces this boundary with
``paystack_frappe.tests.core.test_import_boundary``.
"""
