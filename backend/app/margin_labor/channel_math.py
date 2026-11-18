"""Module E's arithmetic (spec section 9): what an item keeps per unit on a sales channel after commission,
fees, packaging and food cost. Labor is not allocated.

    unit_revenue      = line net / line qty                 (after restaurant-funded discounts, before tax)
    line_share        = line net / order net
    commission_unit   = order commission * line_share / line qty
    processing_unit   = order processing fee * line_share / line qty
    fixed_unit        = (fixed fee per order + packaging cost per order) * line_share / line qty
    unit_contribution = unit_revenue - commission_unit - processing_unit - fixed_unit - plate_cost

    price           = (plate_cost + fixed_unit + target_contribution) / (1 - commission_pct - processing_pct)
    suggested_price = price rounded up to the restaurant's price ending
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.margin_labor.costing import round_to_ending

CALC_VERSION = "e1"
FOUR = Decimal("0.0001")
SHARE = Decimal("0.000001")
CENT = Decimal("0.01")
NOTE = "Check your agreement with the platform before changing prices."


@dataclass(frozen=True)
class OrderFees:
    """What one order cost to sell, in dollars for the whole order."""

    commission: Decimal = Decimal(0)
    processing: Decimal = Decimal(0)
    #: The platform's fixed fee and the restaurant's packaging.
    fixed: Decimal = Decimal(0)


@dataclass(frozen=True)
class UnitFigures:
    unit_revenue: Decimal
    commission_unit: Decimal
    processing_unit: Decimal
    fixed_unit: Decimal
    #: None when the item has no fully priced recipe: what it keeps is then not known.
    plate_cost: Decimal | None
    unit_contribution: Decimal | None


def line_figures(line_net: Decimal, line_qty: Decimal, order_net: Decimal, fees: OrderFees, plate_cost: Decimal | None) -> UnitFigures | None:
    """One order line's figures per unit. None for a line with no quantity or an order with no sales."""
    if line_qty <= 0 or order_net <= 0:
        return None
    revenue = line_net / line_qty
    share = line_net / order_net
    commission = fees.commission * share / line_qty
    processing = fees.processing * share / line_qty
    fixed = fees.fixed * share / line_qty
    kept = None if plate_cost is None else revenue - commission - processing - fixed - plate_cost
    return UnitFigures(revenue, commission, processing, fixed, plate_cost, kept)


def average(figures: list[tuple[UnitFigures, Decimal]]) -> UnitFigures | None:
    """Per-unit figures over many lines: each line weighted by its units, so the result is dollars per unit sold."""
    units = sum((q for _f, q in figures), Decimal(0))
    if units <= 0:
        return None
    mean = lambda pick: (sum((pick(f) * q for f, q in figures), Decimal(0)) / units).quantize(FOUR, rounding=ROUND_HALF_UP)  # noqa: E731
    costed = all(f.plate_cost is not None for f, _q in figures)
    revenue, commission, processing, fixed = mean(lambda f: f.unit_revenue), mean(lambda f: f.commission_unit), mean(lambda f: f.processing_unit), mean(lambda f: f.fixed_unit)
    plate = mean(lambda f: f.plate_cost) if costed else None
    return UnitFigures(revenue, commission, processing, fixed, plate, None if plate is None else revenue - commission - processing - fixed - plate)


def contribution_pct(figures: UnitFigures) -> Decimal | None:
    if figures.unit_contribution is None or figures.unit_revenue <= 0:
        return None
    return (figures.unit_contribution / figures.unit_revenue).quantize(SHARE, rounding=ROUND_HALF_UP)


def weekly_impact(target_contribution: Decimal, unit_contribution: Decimal, units_in_window: Decimal) -> Decimal:
    """(target_contribution - unit_contribution) * units_in_window / 4"""
    return ((target_contribution - unit_contribution) * units_in_window / 4).quantize(FOUR, rounding=ROUND_HALF_UP)


def below_target(unit_contribution: Decimal, dine_in_contribution: Decimal | None) -> bool:
    """A finding's condition: the item loses money on the platform, or keeps under half of what it keeps dine-in."""
    return unit_contribution < 0 or (dine_in_contribution is not None and unit_contribution < dine_in_contribution / 2)


@dataclass(frozen=True)
class ChannelPrice:
    price: Decimal
    suggested_price: Decimal
    #: What the suggested price keeps per unit: at or above the target.
    contribution: Decimal


def channel_price(plate_cost: Decimal, fixed_unit: Decimal, target_contribution: Decimal, commission_pct: Decimal, processing_pct: Decimal,
                  ending: Decimal = Decimal("0.00")) -> ChannelPrice | None:
    """The platform price at which the item keeps its target. None when the platform's rates take everything."""
    keep = 1 - commission_pct - processing_pct
    if keep <= 0:
        return None
    price = ((plate_cost + fixed_unit + target_contribution) / keep).quantize(CENT, rounding=ROUND_HALF_UP)
    suggested = round_to_ending((plate_cost + fixed_unit + target_contribution) / keep, ending, up=True)
    return ChannelPrice(price, suggested, (suggested * keep - plate_cost - fixed_unit).quantize(CENT, rounding=ROUND_HALF_UP))
