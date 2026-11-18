"""Module B golden tests (spec sections 6 and 12): the arithmetic, with no database."""

from decimal import Decimal as D

from app.margin_labor import costing, usage_math, words
from test_margin_labor_costing import BURGER, ITEMS, PRICES, RECIPES, _price_of


def test_golden_usage_variance_chicken_gives_13000_g_unexplained_and_28_60_a_week():
    v = usage_math.variance(theoretical_qty=D(82000), purchased_qty=D(104000), noise_qty=D(9000), avg_unit_price=D("0.0088"))
    assert (v.excess_qty, v.unexplained_qty, v.weekly_dollars) == (D(22000), D(13000), D("28.6000"))
    assert v.pct_of_theoretical == D("0.158537") and words.percent(v.pct_of_theoretical) == "15.9%"
    assert v.qualifies(min_pct=D("0.05"), min_dollars=D(25)) and not v.data_gap(D("0.85"))


def test_a_gap_must_be_positive_material_and_worth_the_dollars():
    small = usage_math.variance(D(82000), D(94000), D(9000), D("0.0088"))   # 3,000 g: 3.7% of expected
    cheap = usage_math.variance(D(82000), D(104000), D(9000), D("0.0050"))  # 13,000 g at half a cent: $16.25 a week
    under = usage_math.variance(D(82000), D(88000), D(9000), D("0.0088"))   # inside one delivery
    assert [v.qualifies(D("0.05"), D(25)) for v in (small, cheap, under)] == [False, False, False]
    assert under.unexplained_qty == D(-3000) and cheap.weekly_dollars == D("16.2500")
    assert not usage_math.variance(D(0), D(5000), D(0), D("0.01")).qualifies(D("0.05"), D(1))  # nothing expected: no percentage, no finding


def test_the_data_gap_rule_fires_when_purchases_and_a_delivery_are_under_85_percent_of_expected():
    assert usage_math.variance(D(100000), D(70000), D(14999), D("0.01")).data_gap(D("0.85"))       # 84,999 against 85,000
    assert not usage_math.variance(D(100000), D(70000), D(15000), D("0.01")).data_gap(D("0.85"))  # exactly 85%
    assert not usage_math.variance(D(0), D(0), D(0), D("0.01")).data_gap(D("0.85"))


def test_noise_is_the_median_delivery_and_coverage_sets_the_state():
    assert usage_math.noise([D(8000), D(9000), D(12000)]) == D(9000) and usage_math.noise([D(8000), D(10000)]) == D(9000)
    assert usage_math.noise([]) == D(0)
    assert usage_math.coverage(D(850), D(1000)) == D("0.850000") and usage_math.coverage(D(0), D(0)) == D(0)
    states = [usage_math.state_for(D(c), D("0.80"), D("0.90")) for c in ("0.7999", "0.80", "0.85", "0.8999", "0.90", "1")]
    assert states == ["setup", "learning", "learning", "learning", "live", "live"]


def test_what_a_portion_uses_comes_from_the_recipe_alone_through_yield_and_sub_recipes():
    use = costing.usage(BURGER, RECIPES, ITEMS)
    assert use["beef"] == D(150) and use["bun"] == D(1) and use["base"] == D(20)
    assert use["onion"].quantize(D("0.0001")) == D("35.2941")  # 30 g usable at 85% yield
    # No price is needed to know what was used.
    assert costing.cost_recipe(BURGER, RECIPES, ITEMS, _price_of({})).use_per_portion == use
    assert costing.cost_recipe(BURGER, RECIPES, ITEMS, _price_of(PRICES)).use_per_portion == use
