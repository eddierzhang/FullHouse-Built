"""Module B's arithmetic (spec section 6): what was bought beyond what the recipes say was used, with no counts.

Over any window, purchases equal usage plus the change in stock. The change in stock is unknown without counts,
but it is rarely more than one delivery, so a 28-day window treats one typical delivery as noise:

    excess_qty      = purchased_qty - theoretical_qty
    unexplained_qty = excess_qty - noise_qty            (noise_qty: the median quantity per delivery, last 8 weeks)
    weekly_dollars  = unexplained_qty * average_unit_price_in_window / 4

It cannot say whether the gap is waste, over-portioning or theft; it says where it is and how large.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from statistics import median

CALC_VERSION = "b1"
FOUR = Decimal("0.0001")
SHARE = Decimal("0.000001")
WINDOW_DAYS = 28
NOISE_DAYS = 56


@dataclass(frozen=True)
class Variance:
    theoretical_qty: Decimal
    purchased_qty: Decimal
    noise_qty: Decimal
    excess_qty: Decimal
    unexplained_qty: Decimal
    avg_unit_price: Decimal
    weekly_dollars: Decimal
    #: Unexplained as a share of theoretical; None when nothing was expected.
    pct_of_theoretical: Decimal | None

    def qualifies(self, min_pct: Decimal, min_dollars: Decimal) -> bool:
        """Worth a finding: more than nothing, at least `min_pct` of expected use, and at least `min_dollars` a week."""
        return (self.unexplained_qty > 0 and self.pct_of_theoretical is not None and self.pct_of_theoretical >= min_pct
                and self.weekly_dollars >= min_dollars)

    def data_gap(self, gap_pct: Decimal) -> bool:
        """Less was bought, even with a delivery's slack, than 85% of what the recipes used: the data is wrong, not the kitchen."""
        return self.theoretical_qty > 0 and self.purchased_qty + self.noise_qty < gap_pct * self.theoretical_qty


def variance(theoretical_qty: Decimal, purchased_qty: Decimal, noise_qty: Decimal, avg_unit_price: Decimal) -> Variance:
    excess = purchased_qty - theoretical_qty
    unexplained = excess - noise_qty
    weekly = (unexplained * avg_unit_price / 4).quantize(FOUR, rounding=ROUND_HALF_UP)
    pct = (unexplained / theoretical_qty).quantize(SHARE, rounding=ROUND_HALF_UP) if theoretical_qty > 0 else None
    return Variance(theoretical_qty, purchased_qty, noise_qty, excess, unexplained, avg_unit_price, weekly, pct)


def noise(delivery_quantities: list[Decimal]) -> Decimal:
    """The median quantity per delivery; zero when there was none."""
    return Decimal(median(delivery_quantities)) if delivery_quantities else Decimal(0)


def coverage(covered_net: Decimal, total_net: Decimal) -> Decimal:
    """Net sales of items with a confirmed, fully priced recipe over all net sales. No sales is no coverage."""
    if total_net <= 0:
        return Decimal(0)
    return (covered_net / total_net).quantize(SHARE, rounding=ROUND_HALF_UP)


def state_for(recipe_coverage: Decimal, learning: Decimal, live: Decimal) -> str:
    """Below 0.80 setup; 0.80 to below 0.90 learning; 0.90 or above live."""
    return "live" if recipe_coverage >= live else "learning" if recipe_coverage >= learning else "setup"
