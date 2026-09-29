"""
Currencies, subunits and amount rules for Paystack.

Paystack takes every amount in the currency's subunit (x100). XOF has no
subunit in circulation, yet the API still takes x100 and ignores fractions,
so XOF amounts must be whole. Minimum amounts are Paystack's documented ones:
https://paystack.com/docs/api/#supported-currency
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Iterable, Optional

import frappe
from frappe import _

# Currencies Paystack documents as chargeable.
SUPPORTED_CURRENCIES = ("NGN", "GHS", "ZAR", "KES", "USD", "XOF")

# Every supported currency is sent to Paystack multiplied by 100.
SUBUNIT_FACTOR = 100

# Decimal places a major-unit amount may carry.
DECIMAL_PLACES = {"XOF": 0}
DEFAULT_DECIMAL_PLACES = 2

# Documented minimum charge per currency, in major units.
MINIMUM_AMOUNTS = {
    "NGN": Decimal("50"),
    "USD": Decimal("2"),
    "GHS": Decimal("0.10"),
    "ZAR": Decimal("1"),
    "KES": Decimal("3"),
    "XOF": Decimal("1"),
}


class CurrencyNotSupported(frappe.ValidationError):
    pass


class InvalidAmount(frappe.ValidationError):
    pass


def clean_currency(currency: Optional[str]) -> str:
    """Return a currency code trimmed and upper-cased. Never substitutes a default."""
    return (currency or "").strip().upper()


def is_supported(currency: Optional[str]) -> bool:
    return clean_currency(currency) in SUPPORTED_CURRENCIES


def ensure_supported(currency: Optional[str], allowed: Optional[Iterable[str]] = None) -> str:
    """
    Return the cleaned currency, or throw CurrencyNotSupported.

    `allowed` narrows the check to an account's own currencies.
    """
    code = clean_currency(currency)
    if not code:
        frappe.throw(_("A currency is required for a Paystack payment."), CurrencyNotSupported)

    if code not in SUPPORTED_CURRENCIES:
        frappe.throw(
            _("Paystack does not support payments in {0}. Supported currencies: {1}.").format(
                code, ", ".join(SUPPORTED_CURRENCIES)
            ),
            CurrencyNotSupported,
        )

    if allowed is not None:
        allowed = [clean_currency(c) for c in allowed if c]
        if code not in allowed:
            frappe.throw(
                _("This Paystack account is not set up to charge {0}. It charges: {1}.").format(
                    code, ", ".join(allowed) or _("nothing")
                ),
                CurrencyNotSupported,
            )

    return code


def to_decimal(amount: Any) -> Decimal:
    """Return amount as a Decimal, throwing InvalidAmount for anything non-numeric."""
    if isinstance(amount, Decimal):
        return amount
    try:
        if isinstance(amount, float):
            # repr() gives the shortest string that round-trips, avoiding 0.1 noise.
            return Decimal(repr(amount))
        return Decimal(str(amount).strip())
    except (InvalidOperation, ValueError, TypeError):
        frappe.throw(_("{0} is not a valid amount.").format(frappe.bold(str(amount))), InvalidAmount)


def decimal_places(currency: str) -> int:
    return DECIMAL_PLACES.get(clean_currency(currency), DEFAULT_DECIMAL_PLACES)


def validate_amount(amount: Any, currency: str) -> Decimal:
    """
    Return the amount as a Decimal after enforcing Paystack's rules.

    Refuses zero, negative, over-precise and below-minimum amounts with a clear
    message, before anything is sent to Paystack.
    """
    code = clean_currency(currency)
    value = to_decimal(amount)

    if not value.is_finite() or value <= 0:
        frappe.throw(_("The amount to pay must be greater than zero."), InvalidAmount)

    places = decimal_places(code)
    if value != value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP):
        if places == 0:
            frappe.throw(_("{0} amounts must be whole numbers.").format(code), InvalidAmount)
        frappe.throw(
            _("{0} amounts can have at most {1} decimal places.").format(code, places),
            InvalidAmount,
        )

    minimum = MINIMUM_AMOUNTS.get(code)
    if minimum is not None and value < minimum:
        frappe.throw(
            _("Paystack's minimum charge in {0} is {1}.").format(code, format_amount(minimum, code)),
            InvalidAmount,
        )

    return value


def to_minor(amount: Any, currency: str) -> int:
    """Return the integer subunit amount Paystack expects."""
    value = to_decimal(amount)
    places = decimal_places(currency)
    value = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return int((value * SUBUNIT_FACTOR).to_integral_value(rounding=ROUND_HALF_UP))


def from_minor(amount_minor: Any, currency: str = "") -> float:
    """Return a major-unit float from Paystack's subunit integer."""
    try:
        value = Decimal(str(amount_minor or 0))
    except (InvalidOperation, ValueError):
        value = Decimal(0)
    return float(value / SUBUNIT_FACTOR)


def format_amount(amount: Any, currency: str) -> str:
    try:
        return frappe.utils.fmt_money(float(to_decimal(amount)), currency=clean_currency(currency))
    except Exception:
        return f"{clean_currency(currency)} {amount}"


def split_currency_list(value: Optional[str]) -> list:
    """Return the codes in a comma/newline separated list, cleaned and de-duplicated."""
    codes = []
    for part in (value or "").replace("\n", ",").split(","):
        code = clean_currency(part)
        if code and code not in codes:
            codes.append(code)
    return codes
