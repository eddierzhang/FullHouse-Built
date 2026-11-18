"""Prep sheets: tomorrow's forecast times the recipes, per ingredient, as a proposal. No stock on hand exists
here, so nothing is subtracted; the sheet says what the forecast calls for."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.agents import caps
from app.agents.models import AgentAction, AgentRun
from app.forecast.models import Forecast, PrepSheet
from app.prices.models import PriceItem
from app.recipes.engine import convert
from app.recipes.models import CONFIRMED, Recipe

DAYS_AHEAD = 2


def lines_for(db: Session, restaurant_id: str, day: date) -> list[dict]:
    recipes = {r.pos_item_id: r for r in db.query(Recipe).filter(Recipe.restaurant_id == restaurant_id, Recipe.status == CONFIRMED, Recipe.pos_item_id.isnot(None))}
    need: dict[str, dict] = {}
    for forecast in db.query(Forecast).filter(Forecast.restaurant_id == restaurant_id, Forecast.date == day):
        recipe = recipes.get(forecast.pos_item_id)
        if recipe is None or forecast.qty_pred <= 0:
            continue
        plates = forecast.qty_pred / (recipe.yield_qty or 1)
        for line in recipe.lines:
            if not line.item_id:
                continue
            item = db.get(PriceItem, line.item_id)
            if item is None:
                continue
            amount = convert(line.qty, line.unit, item.unit)
            unit = item.unit if amount is not None else line.unit
            amount = amount if amount is not None else line.qty
            row = need.setdefault(item.id, {"item_id": item.id, "item_name": item.name, "qty": 0.0, "unit": unit, "from": []})
            row["qty"] = round(row["qty"] + amount * plates, 3)
            row["from"].append({"recipe": recipe.name, "plates": round(forecast.qty_pred, 1), "forecast_id": forecast.id})
    return sorted(need.values(), key=lambda r: r["item_name"])


def propose(db: Session, restaurant_id: str, day: date, run: AgentRun | None = None, today: date | None = None) -> AgentAction | None:
    for action in db.query(AgentAction).filter(AgentAction.restaurant_id == restaurant_id, AgentAction.action_type == "prep_sheet", AgentAction.status == "pending"):
        if (action.payload or {}).get("date") == day.isoformat():
            return action
    lines = lines_for(db, restaurant_id, day)
    if not lines:
        return None
    if not caps.within(db, restaurant_id, "prep_sheet", today):
        return None
    if run is None:
        from app.restaurant.po_draft import _system_run

        run = _system_run(db, restaurant_id, f"Prep sheet for {day.isoformat()}")
    action = AgentAction(restaurant_id=restaurant_id, run_id=run.id, agent_definition_id=run.agent_definition_id, action_type="prep_sheet", payload={
        "date": day.isoformat(), "lines": lines, "item_name": day.isoformat(),
        "note": f"{len(lines)} ingredients for {day.strftime('%A %b %-d')}, from the forecast and your recipes: "
                + "; ".join(f"{l['item_name']} {l['qty']:g} {l['unit']}" for l in lines[:6]) + ("…" if len(lines) > 6 else "."),
        "cited_forecast_ids": sorted({f["forecast_id"] for l in lines for f in l["from"]}),
    })
    db.add(action)
    db.flush()
    return action


def run_workflow(db: Session, restaurant_id: str, today: date | None = None) -> list[AgentAction]:
    today = today or date.today()
    return [a for a in (propose(db, restaurant_id, today + timedelta(days=i), today=today) for i in range(1, DAYS_AHEAD + 1)) if a is not None]


def write_sheet(db: Session, restaurant_id: str, payload: dict, proposal_id: str | None) -> PrepSheet:
    day = date.fromisoformat(str(payload["date"])[:10])
    sheet = db.query(PrepSheet).filter(PrepSheet.restaurant_id == restaurant_id, PrepSheet.date == day).first()
    if sheet is None:
        sheet = PrepSheet(restaurant_id=restaurant_id, date=day)
        db.add(sheet)
    sheet.lines = payload.get("lines") or []
    sheet.proposal_id = proposal_id
    sheet.status = "confirmed"
    db.flush()
    return sheet
