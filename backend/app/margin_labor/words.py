"""Numbers and dates as the interface writes them (spec section 10.5), for the few sentences the server composes
(Why panels, findings, digest cards). Dollars without cents at $100 or more; percentages to one decimal."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal


def dollars(value) -> str:
    amount = Decimal(str(value))
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    if amount >= 100:
        return f"{sign}${amount.quantize(Decimal(1), rounding=ROUND_HALF_UP):,}"
    return f"{sign}${amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,}"


def unit_price(value, unit: str) -> str:
    """A price per gram needs more places than a price per case: four significant figures, no trailing zeros."""
    amount = Decimal(str(value))
    if amount >= 1:
        return f"{dollars(amount)} per {unit}"
    text = f"{amount:.6f}".rstrip("0")
    if len(text.split(".")[1]) < 2:
        text = f"{amount:.2f}"
    return f"${text} per {unit}"


def percent(fraction) -> str:
    return f"{(Decimal(str(fraction)) * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"


def qty(value) -> str:
    amount = Decimal(str(value))
    text = f"{amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def day(value: date) -> str:
    """Tue Nov 17"""
    return f"{value.strftime('%a %b')} {value.day}"
