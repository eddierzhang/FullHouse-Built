"""The Digest's cards for these modules (spec section 10.9): at most one per module, each one sentence's worth of
facts, one number, at most one sparkline, and one place to go. A module still in setup shows a card only until
setup is done. The page writes the sentence; this returns the facts."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.platform import flags


def build(db: Session, restaurant_id: str, user) -> list[dict]:
    cards = []
    if flags.enabled(db, restaurant_id, flags.ML_MENU_COSTS):
        card = _menu_costs(db, restaurant_id)
        if card is not None:
            cards.append(card)
    if flags.enabled(db, restaurant_id, flags.ML_USAGE):
        card = _usage(db, restaurant_id)
        if card is not None:
            cards.append(card)
    if flags.enabled(db, restaurant_id, flags.ML_CHANNELS):
        card = _channels(db, restaurant_id)
        if card is not None:
            cards.append(card)
    return cards


def _channels(db: Session, restaurant_id: str) -> dict | None:
    from app.margin_labor import channels_read

    out = channels_read.overview(db, restaurant_id)
    if out["state"] == "setup":
        return {"module": "channels", "state": "setup", "route": "channels", "missing": [s["key"] for s in out["setup"] if not s["done"]]}
    if not out["header"]["items"]:
        return None
    top = next(i for i in out["items"] if i["finding"])
    return {"module": "channels", "state": "live", "route": "channels", "items": out["header"]["items"], "platform": out["header"]["platform"],
            "weekly_dollars": out["header"]["weekly_dollars"], "top": {"row_id": top["row_id"], "name": top["name"], "platform": top["platform"]}}


def _usage(db: Session, restaurant_id: str) -> dict | None:
    from app.margin_labor import usage_read

    out = usage_read.overview(db, restaurant_id)
    if out["state"] == "setup":
        # Shown only until setup is done: "Add 6 recipes to turn on Usage."
        return {"module": "usage", "state": "setup", "route": "usage", "recipes_needed": out["recipes_needed"], "has_sales": out["has_sales"]}
    flagged = [i for i in out["items"] if i["finding"]]
    if out["state"] != "live" or not flagged:
        return None
    top = flagged[0]
    return {"module": "usage", "state": "live", "route": "usage", "weekly_dollars": out["header"]["weekly_dollars"], "items": out["header"]["items"],
            "findings": len(flagged[:5]), "top": {"item_id": top["item_id"], "name": top["name"], "trend": top["trend"]}}


def _menu_costs(db: Session, restaurant_id: str) -> dict | None:
    from app.margin_labor import menu_costs_read

    out = menu_costs_read.overview(db, restaurant_id)
    if out["state"] == "setup":
        missing = [s["key"] for s in out["setup"] if not s["done"]]
        return {"module": "menu_costs", "state": "setup", "route": "menu-costs", "missing": missing}
    header = out["header"]
    if not header["dishes_over"]:
        return None  # nothing over target: no card
    top = next(d for d in out["dishes"] if d["finding"])
    return {"module": "menu_costs", "state": out["state"], "route": "menu-costs", "dishes_over": header["dishes_over"], "target": out["target"],
            "weekly_dollars": header["weekly_dollars"], "top": {"recipe_id": top["recipe_id"], "name": top["name"], "trend": top["trend"]}}
