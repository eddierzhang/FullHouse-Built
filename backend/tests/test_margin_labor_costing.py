"""Module A golden and property tests (spec sections 5 and 12): the arithmetic, with no database."""

import random
from decimal import Decimal as D

from app.margin_labor import costing, units
from app.margin_labor.costing import Item, Line, Price, RecipeDef

ITEMS = {"beef": Item("beef", "Ground beef", "g"), "bun": Item("bun", "Bun", "each"), "onion": Item("onion", "Onion", "g"),
         "base": Item("base", "Sauce base", "g")}
PRICES = {"beef": D("0.0120"), "bun": D("0.4500"), "onion": D("0.0020"), "base": D("0.0060")}
SAUCE = RecipeDef("sauce", "House sauce", (Line("s1", "Sauce base", D(500), "g", item_id="base"),), yield_qty=D(500), yield_unit="g", kind="sub_recipe")
BURGER = RecipeDef("burger", "Burger", (
    Line("l1", "Ground beef", D(150), "g", item_id="beef"),
    Line("l2", "Bun", D(1), "each", item_id="bun"),
    Line("l3", "Onion", D(30), "g", yield_pct=D("0.85"), item_id="onion"),
    Line("l4", "House sauce", D(20), "g", sub_recipe_id="sauce"),
))
RECIPES = {"sauce": SAUCE, "burger": BURGER}


def _price_of(prices):
    return lambda item_id: Price(prices[item_id]) if item_id in prices else None


def test_golden_plate_cost_the_burger_costs_2_4406_and_20_3_percent_at_12_dollars():
    costed = costing.cost_recipe(BURGER, RECIPES, ITEMS, _price_of(PRICES))
    assert [b["line_cost"] for b in costed.basis] == ["1.8000", "0.4500", "0.0706", "0.1200"]
    assert costed.basis[2]["purchased_qty"] == "35.29411765"  # 30 / 0.85
    assert costing.cost_recipe(SAUCE, RECIPES, ITEMS, _price_of(PRICES)).batch_cost == D("3.0000")  # $3.00 per 500 g batch
    assert costed.plate_cost == D("2.4406") and costed.batch_cost == D("2.4406")
    assert (costed.lines_priced, costed.lines_total, costed.unpriced) == (4, 4, [])
    pct = costing.food_cost_pct(costed.plate_cost, D("12.00"))
    assert pct == D("0.2034") and f"{pct * 100:.1f}%" == "20.3%"
    assert (D("12.00") - costed.plate_cost).quantize(D("0.01")) == D("9.56")
    # What one burger uses, after yield and through the sauce: the input Module B needs.
    assert costed.use_per_portion["onion"].quantize(D("0.0001")) == D("35.2941")
    assert costed.use_per_portion["base"] == D(20) and costed.use_per_portion["beef"] == D(150)


def test_golden_menu_reprice_with_the_cap_gives_13_dollars_and_32_3_percent():
    out = costing.suggest_price(D("4.20"), D("12.00"), D("0.30"), D("0.00"))
    assert (out.price_at_target, out.price_cap, out.suggested_price, out.cap_applied) == (D("14.00"), D("13.00"), D("13.00"), True)
    assert f"{out.food_cost_pct * 100:.1f}%" == "32.3%"
    # Without the cap in the way, the price at target is the suggestion; other endings round the same way.
    free = costing.suggest_price(D("3.70"), D("12.00"), D("0.30"))
    assert (free.suggested_price, free.cap_applied) == (D("13.00"), False)  # 12.3333 rounded up
    assert costing.suggest_price(D("4.20"), D("12.00"), D("0.30"), D("0.99")).price_cap == D("12.99")
    assert costing.suggest_price(D("3.60"), D("12.00"), D("0.30"), D("0.95")).price_at_target == D("12.95")
    assert costing.round_to_ending(D("13.20"), D("0.50"), up=False) == D("12.50") and costing.round_to_ending(D("13.20"), D("0.50"), up=True) == D("13.50")


def test_a_recipe_with_an_unpriced_line_is_a_floor_and_says_which_line():
    prices = {k: v for k, v in PRICES.items() if k != "onion"}
    costed = costing.cost_recipe(BURGER, RECIPES, ITEMS, _price_of(prices))
    assert (costed.lines_priced, costed.lines_total, costed.complete) == (3, 4, False)
    assert costed.plate_cost == D("2.3700") and costed.unpriced == ["Onion (no invoice price)"]
    # A unit that doesn't convert, an unmatched line, a bad yield and an unconfirmed sub-recipe are all unpriced, never guessed.
    odd = RecipeDef("odd", "Odd", (Line("a", "Beef by the cup", D(1), "cup", item_id="beef"), Line("b", "Mystery", D(1), "each"),
                                   Line("c", "Onion", D(10), "g", yield_pct=D(0), item_id="onion"), Line("d", "Sauce", D(1), "each", sub_recipe_id="sauce"),
                                   Line("e", "Draft", D(5), "g", sub_recipe_id="draft")))
    recipes = {**RECIPES, "odd": odd, "draft": RecipeDef("draft", "Draft", (), confirmed=False)}
    costed = costing.cost_recipe(odd, recipes, ITEMS, _price_of(PRICES))
    assert costed.lines_priced == 0 and len(costed.unpriced) == 5 and costed.plate_cost == D(0)


def test_units_convert_within_a_kind_and_through_an_items_own_conversion():
    assert units.base_of("lb") == ("g", D("453.592")) and units.base_of("gal")[0] == "ml" and units.base_of("ct") == ("each", D(1))
    assert units.to_base(D(2), "oz", "g") == D("56.6990") and units.to_base(D(1), "cup", "g") is None
    assert units.to_base(D(1), "cup", "g", {"cup": D(120)}) == D(120)  # this restaurant's cup of flour
    assert units.convert(D(1), "kg", "g") == D(1000) and units.convert(D(1), "g", "each") is None


def test_portions_divide_the_batch_and_a_cycle_is_cut_not_looped():
    soup = RecipeDef("soup", "Soup", (Line("a", "Onion", D(1000), "g", item_id="onion"),), portions=D(8))
    assert costing.cost_recipe(soup, {}, ITEMS, _price_of(PRICES)).plate_cost == D("0.2500")
    a = RecipeDef("a", "A", (Line("1", "B", D(1), "each", sub_recipe_id="b"),), yield_unit="each")
    b = RecipeDef("b", "B", (Line("2", "A", D(1), "each", sub_recipe_id="a"),), yield_unit="each")
    looped = costing.cost_recipe(a, {"a": a, "b": b}, ITEMS, _price_of(PRICES))
    assert looped.lines_priced == 0 and "uses this recipe" in looped.unpriced[0]
    assert costing.would_cycle({"a": a, "b": b}, "a", {"b"}) and not costing.would_cycle(RECIPES, "burger", {"sauce"})
    assert costing.would_cycle(RECIPES, "sauce", {"burger"}) and costing.would_cycle(RECIPES, "burger", {"burger"})


def test_recipes_are_ordered_sub_recipes_first_and_found_from_an_ingredient():
    assert costing.topological(RECIPES, {"burger", "sauce"}) == ["sauce", "burger"]
    assert costing.users_of(RECIPES, {"base"}) == {"sauce", "burger"}  # through the sauce
    assert costing.users_of(RECIPES, {"bun"}) == {"burger"} and costing.users_of(RECIPES, {"nothing"}) == set()


def test_property_plate_cost_never_falls_when_one_ingredient_price_rises():
    rng = random.Random(5)
    for _ in range(300):
        prices = {k: D(rng.randint(1, 9000)) / D(10000) for k in PRICES}
        before = costing.cost_recipe(BURGER, RECIPES, ITEMS, _price_of(prices)).plate_cost
        raised = dict(prices)
        key = rng.choice(sorted(prices))
        raised[key] = prices[key] + D(rng.randint(1, 5000)) / D(10000)
        assert costing.cost_recipe(BURGER, RECIPES, ITEMS, _price_of(raised)).plate_cost >= before


def test_property_the_same_inputs_give_the_same_cost_and_basis():
    one = costing.cost_recipe(BURGER, RECIPES, ITEMS, _price_of(PRICES))
    two = costing.cost_recipe(BURGER, RECIPES, ITEMS, _price_of(PRICES))
    assert (one.plate_cost, one.basis, one.use_per_portion) == (two.plate_cost, two.basis, two.use_per_portion)
