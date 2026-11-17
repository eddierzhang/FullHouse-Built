"""Base units (spec section 4): every ingredient is costed in g, ml or each.

An item's base unit comes from the unit its invoices use (lb is a mass, so g). A recipe line written in another
unit of the same kind converts by the standard factor; across kinds (a cup of flour, an onion by the each) it
needs the item's own conversion (`item_conversions`), and without one the line is unpriced, never guessed.
"""

from __future__ import annotations

from decimal import Decimal

MASS = {"g": Decimal(1), "kg": Decimal(1000), "lb": Decimal("453.592"), "lbs": Decimal("453.592"), "oz": Decimal("28.3495")}
VOLUME = {"ml": Decimal(1), "l": Decimal(1000), "gal": Decimal("3785.41"), "qt": Decimal("946.353"), "pt": Decimal("473.176"),
          "cup": Decimal("236.588"), "fl oz": Decimal("29.5735"), "floz": Decimal("29.5735"), "tbsp": Decimal("14.7868"), "tsp": Decimal("4.92892")}
COUNT = {"each", "ea", "ct", "head", "bunch", "loaf", "bottle", "can", "jar", "piece", "pc", "slice", "clove", "plate", "portion", "serving"}


def clean(unit: str | None) -> str:
    return (unit or "").strip().lower().rstrip(".")


def base_of(unit: str | None) -> tuple[str, Decimal]:
    """(base unit, how many base units one `unit` is). An unknown unit is counted by the each."""
    u = clean(unit)
    if u in MASS:
        return "g", MASS[u]
    if u in VOLUME:
        return "ml", VOLUME[u]
    return "each", Decimal(1)


def kind_of(unit: str | None) -> str:
    u = clean(unit)
    return "mass" if u in MASS else "volume" if u in VOLUME else "count"


def to_base(qty: Decimal, unit: str | None, base_unit: str, own: dict[str, Decimal] | None = None) -> Decimal | None:
    """`qty` of `unit` in the item's base unit, or None when they don't convert.

    `own` is the item's own conversions (unit -> base units), which win over the standard ones: a restaurant's
    cup of flour is what it says it is.
    """
    u = clean(unit)
    if own and u in own:
        return qty * own[u]
    if base_unit == "g" and u in MASS:
        return qty * MASS[u]
    if base_unit == "ml" and u in VOLUME:
        return qty * VOLUME[u]
    if base_unit == "each" and kind_of(u) == "count":
        return qty
    return None


def convert(qty: Decimal, unit: str | None, to_unit: str | None) -> Decimal | None:
    """`qty` of `unit` as a number of `to_unit`, when both are the same kind of measure."""
    a, b = clean(unit), clean(to_unit)
    if a == b:
        return qty
    for table in (MASS, VOLUME):
        if a in table and b in table:
            return qty * table[a] / table[b]
    if kind_of(a) == "count" and kind_of(b) == "count" and a not in MASS and b not in MASS:
        return qty
    return None
