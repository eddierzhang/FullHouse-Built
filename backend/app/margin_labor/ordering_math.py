"""Module C's arithmetic (spec section 7): from an ingredient's daily demand range to an order in whole packs,
with no stock counts.

    sigma_day     = (ingredient_p90 - ingredient_p10) / 2.563
    period_demand = sum(ingredient_p50 over period) + z * sqrt(sum(sigma_day^2 over period))
    need          = period_demand * (1 + loss_factor)
    raw_qty       = need - est_on_hand_at_delivery
    order_qty     = 0 if raw_qty <= 0, else ceil(raw_qty / pack_size) * pack_size

    z           = 0.8416 (80% service level) when shelf_life_days <= 5, else 1.6449 (95%)
    loss_factor = unexplained_qty / theoretical_qty from Module B, limited to 0 to 0.15; 0 when Module B is not live

The coverage period runs from this order's delivery date up to, not including, the supplier's next delivery date;
when the shelf life is shorter than that, only the first `shelf_life_days` dates are summed.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal

CALC_VERSION = "c1"
SPAN = Decimal("2.563")        # the width of a normal 10th-to-90th percentile range, in standard deviations
Z_SHORT_LIFE = Decimal("0.8416")
Z_LONG_LIFE = Decimal("1.6449")
SHORT_LIFE_DAYS = 5
PAR_Z = Decimal("0.6745")      # the 75th percentile
MAX_LOSS = Decimal("0.15")
QTY = Decimal("0.0001")


def sigma_day(p10: Decimal, p90: Decimal) -> Decimal:
    return (p90 - p10) / SPAN


def loss_factor(unexplained_qty: Decimal | None, theoretical_qty: Decimal | None, live: bool) -> Decimal:
    if not live or unexplained_qty is None or theoretical_qty is None or theoretical_qty <= 0:
        return Decimal(0)
    return max(Decimal(0), min(MAX_LOSS, unexplained_qty / theoretical_qty)).quantize(Decimal("0.000001"))


@dataclass(frozen=True)
class OrderQty:
    days_used: int
    sum_p50: Decimal
    root_sum_variance: Decimal
    z: Decimal
    period_demand: Decimal
    need: Decimal
    raw_qty: Decimal
    packs: int
    order_qty: Decimal


def order_quantity(days: list[tuple[Decimal, Decimal, Decimal]], shelf_life_days: int | None, loss: Decimal, est_on_hand_at_delivery: Decimal,
                   pack_size: Decimal) -> OrderQty:
    """`days` are (p10, p50, p90) of ingredient demand for each business date of the coverage period, in base units."""
    used = days[:shelf_life_days] if shelf_life_days is not None and 0 < shelf_life_days < len(days) else days
    z = Z_SHORT_LIFE if shelf_life_days is not None and shelf_life_days <= SHORT_LIFE_DAYS else Z_LONG_LIFE
    total = sum((p50 for _lo, p50, _hi in used), Decimal(0))
    root = sum((sigma_day(lo, hi) ** 2 for lo, _mid, hi in used), Decimal(0)).sqrt()
    period = total + z * root
    need = period * (1 + loss)
    raw = need - est_on_hand_at_delivery
    packs = 0 if raw <= 0 or pack_size <= 0 else int((raw / pack_size).to_integral_value(rounding=ROUND_CEILING))
    return OrderQty(len(used), total.quantize(QTY), root.quantize(QTY), z, period.quantize(QTY), need.quantize(QTY), raw.quantize(QTY), packs,
                    (pack_size * packs).quantize(QTY))


def par_today(p10: Decimal, p50: Decimal, p90: Decimal) -> Decimal:
    """p50_usage + 0.6745 * sigma_day: the 75th percentile of today's usage."""
    return (p50 + PAR_Z * sigma_day(p10, p90)).quantize(QTY)


def next_estimate(previous: Decimal, received: Decimal, theoretical_usage: Decimal, loss: Decimal) -> Decimal:
    """est_on_hand = max(0, previous_est + received_qty - theoretical_usage * (1 + loss_factor))"""
    return max(Decimal(0), previous + received - theoretical_usage * (1 + loss))


def at_delivery(est_on_hand: Decimal, expected_use_until_delivery: Decimal) -> Decimal:
    """est_on_hand_at_delivery = max(0, est_on_hand - expected usage at p50 between now and delivery)"""
    return max(Decimal(0), est_on_hand - expected_use_until_delivery)
