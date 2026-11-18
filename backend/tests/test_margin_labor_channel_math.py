"""Module E golden tests (spec sections 9 and 12): the arithmetic, with no database."""

from decimal import Decimal as D

from app.margin_labor import channel_math
from app.margin_labor.channel_math import OrderFees


def test_golden_channel_reprice_13_89_suggested_14_00_keeps_5_58():
    out = channel_math.channel_price(plate_cost=D("4.00"), fixed_unit=D("0.50"), target_contribution=D("5.50"), commission_pct=D("0.25"), processing_pct=D("0.03"))
    assert (out.price, out.suggested_price, out.contribution) == (D("13.89"), D("14.00"), D("5.58"))
    assert out.contribution >= D("5.50")
    assert channel_math.channel_price(D("4.00"), D("0.50"), D("5.50"), D("0.25"), D("0.03"), D("0.99")).suggested_price == D("13.99")
    assert channel_math.channel_price(D("4.00"), D("0.50"), D("5.50"), D("0.70"), D("0.30")) is None  # the rates take everything


def test_a_lines_figures_share_the_orders_fees_by_its_share_of_the_order():
    # A $25.00 order: a $13.00 burger and two $6.00 sides. 25% commission, 3% processing, $1.00 fixed and packaging.
    fees = OrderFees(commission=D("6.25"), processing=D("0.75"), fixed=D("1.00"))
    burger = channel_math.line_figures(D("13.00"), D(1), D("25.00"), fees, D("2.4406"))
    assert (burger.unit_revenue, burger.commission_unit, burger.processing_unit, burger.fixed_unit) == (D("13"), D("3.2500"), D("0.3900"), D("0.5200"))
    assert burger.unit_contribution == D("6.3994")
    sides = channel_math.line_figures(D("12.00"), D(2), D("25.00"), fees, D("0.4350"))
    assert (sides.unit_revenue, sides.commission_unit, sides.fixed_unit, sides.unit_contribution) == (D("6"), D("1.5000"), D("0.2400"), D("3.6450"))
    # Every dollar of the order's fees lands on a unit: nothing is lost or counted twice.
    assert burger.commission_unit + sides.commission_unit * 2 == fees.commission and burger.fixed_unit + sides.fixed_unit * 2 == fees.fixed
    assert channel_math.line_figures(D("5"), D(0), D("5"), fees, D(1)) is None and channel_math.line_figures(D("5"), D(1), D(0), fees, D(1)) is None
    assert channel_math.line_figures(D("5"), D(1), D("5"), fees, None).unit_contribution is None  # no recipe: not known, not zero


def test_figures_are_averaged_per_unit_sold_and_the_finding_rule_reads_them():
    fees = OrderFees(D("6.25"), D("0.75"), D("1.00"))
    a = channel_math.line_figures(D("13.00"), D(1), D("25.00"), fees, D("2.4406"))
    b = channel_math.line_figures(D("26.00"), D(2), D("26.00"), OrderFees(D("6.50"), D("0.78"), D("0.70")), D("2.4406"))
    mean = channel_math.average([(a, D(1)), (b, D(2))])
    assert (mean.unit_revenue, mean.commission_unit, mean.fixed_unit, mean.unit_contribution) == (D("13.0000"), D("3.2500"), D("0.4067"), D("6.5127"))
    assert channel_math.contribution_pct(mean) == D("0.500977")
    assert channel_math.average([]) is None
    # Below zero, or below half of what the item keeps dine-in.
    assert channel_math.below_target(D("-0.01"), D("8.00")) and channel_math.below_target(D("3.99"), D("8.00"))
    assert not channel_math.below_target(D("4.00"), D("8.00")) and not channel_math.below_target(D("0.10"), None)
    assert channel_math.weekly_impact(D("4.565"), D("0.415"), D(28)) == D("29.0500")
