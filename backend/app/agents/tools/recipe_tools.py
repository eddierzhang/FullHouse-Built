"""Recipes, agreed prices and menu prices: three ways the agent proposes, never writes.

Each tool writes exactly one proposal (`agent_actions`) within its weekly cap, through the
same functions the pipeline uses, so a proposal from a tool and one from a document look the
same in Recommendations. None touches recipes, prices or the menu directly; the write guard
would refuse it if one tried.
"""

from __future__ import annotations

import json

from anthropic import beta_tool
from sqlalchemy.orm import Session

from app.agents.models import AgentRun
from app.prices import engine
from app.prices.models import InvoiceLine, PriceItem
from app.recipes.models import Recipe


def build_tools(db: Session, run: AgentRun, agent_definition_id: str) -> list:
    restaurant_id = run.restaurant_id

    @beta_tool
    def list_recipes() -> str:
        """The restaurant's recipes: id, name, POS item, status (proposed or confirmed), and the latest plate cost.
        Use it before proposing a recipe, so you don't propose one that exists.
        """
        from app.recipes.models import PlateCost

        out = []
        for r in db.query(Recipe).filter(Recipe.restaurant_id == restaurant_id).order_by(Recipe.name):
            cost = db.query(PlateCost).filter(PlateCost.recipe_id == r.id).order_by(PlateCost.week_of.desc()).first()
            out.append({"id": r.id, "name": r.name, "pos_item_id": r.pos_item_id, "status": r.status,
                        "plate_cost": cost.cost if cost else None, "lines": [{"item_id": l.item_id, "qty": l.qty, "unit": l.unit} for l in r.lines]})
        return json.dumps(out)

    @beta_tool
    def propose_recipe(name: str, pos_item_id: str = "", ingredients_json: str = "") -> str:
        """Propose a recipe for a dish: its lines matched to the items on the restaurant's invoices, with one
        yes/no question for the owner. Ingredients you don't know are left as a question, never guessed.

        Args:
            name: The dish, as the POS or the chef names it.
            pos_item_id: The POS item id from sales, when there is one.
            ingredients_json: Optional JSON list of {"text", "qty", "unit"}; empty infers from the name and the invoices.
        """
        from app.recipes import engine as recipes

        ingredients = None
        if ingredients_json.strip():
            try:
                ingredients = json.loads(ingredients_json)
            except json.JSONDecodeError:
                return "Error: ingredients_json is not valid JSON."
        action = recipes.propose_recipe(db, restaurant_id, name=name.strip(), pos_item_id=pos_item_id.strip() or None,
                                        ingredients=ingredients, run=run)
        db.commit()
        if action is None:
            return "Held: the weekly cap of five recipe proposals is reached, or nothing could be inferred."
        return f"Recipe proposal {action.id} for {name} submitted for the owner, with {len(action.payload['lines'])} lines."

    @beta_tool
    def propose_agreed_price(item_id: str, supplier_id: str, unit_price: float, effective_from: str = "", cited: str = "") -> str:
        """Propose an agreed price: what the owner says they agreed with a supplier for an item, per the item's unit.
        From a price list the owner handed over, or when they say "this is the price we agreed".

        Args:
            item_id: The item, from list_price_items.
            supplier_id: The supplier.
            unit_price: The agreed price per the item's unit (per pound, per bunch).
            effective_from: YYYY-MM-DD; today when blank.
            cited: The line or document it rests on, in words.
        """
        from datetime import date

        from app.documents.registry import propose_agreed_price as propose
        from app.restaurant.models import Supplier

        item = db.get(PriceItem, item_id)
        supplier = db.get(Supplier, supplier_id)
        if item is None or item.restaurant_id != restaurant_id or supplier is None or supplier.restaurant_id != restaurant_id:
            return "Error: no such item or supplier for this restaurant."
        if unit_price <= 0:
            return "Error: the price must be positive."
        try:
            effective = date.fromisoformat(effective_from) if effective_from.strip() else date.today()
        except ValueError:
            return "Error: effective_from must be YYYY-MM-DD."
        made = propose(db, restaurant_id, supplier, item, round(unit_price, 4), effective, None, cited or "the owner's word")
        db.commit()
        return "Agreed price proposal submitted for the owner." if made else "Held: the weekly cap is reached, or the same proposal is already waiting."

    @beta_tool
    def propose_menu_reprice(recipe_id: str) -> str:
        """Suggest a new price for a dish whose plate cost moved: before and after cost, the suggested change, the price
        points cited. The owner changes the price in the POS; nothing here writes to a menu.

        Args:
            recipe_id: The recipe, from list_recipes or a menu-margin card.
        """
        from app.recipes import engine as recipes

        recipe = db.get(Recipe, recipe_id)
        if recipe is None or recipe.restaurant_id != restaurant_id:
            return "Error: no such recipe."
        action = recipes.propose_reprice(db, restaurant_id, recipe, run=run)
        db.commit()
        if action is None:
            return "Held: no plate cost yet, or the weekly cap of five is reached."
        return f"Reprice suggestion {action.id} for {recipe.name} submitted: {action.payload['note']}"

    @beta_tool
    def explain_finding(item_id: str) -> str:
        """Why a card is in the digest, in two sentences with the lines it rests on. Read-only.

        Args:
            item_id: The item or recipe on the card.
        """
        digest = engine.latest_digest(db, restaurant_id)
        for card in digest.cards:
            if card.get("item_id") == item_id:
                return json.dumps({"section": card["section"], "sentence": card["sentence"], "impact": card["impact"], "sources": card["sources"]})
        line = db.query(InvoiceLine).filter(InvoiceLine.item_id == item_id).count()
        return f"No card for that id this week; {line} invoice lines are matched to it."

    @beta_tool
    def propose_calendar_item(document_id: str) -> str:
        """From a read license, permit, inspection report or warranty: a dated calendar item (and, for a warranty,
        the equipment) as a proposal. Cap ten a week.

        Args:
            document_id: The document, from the Documents page.
        """
        from app.calendar import engine as calendar
        from app.documents.models import Document

        document = db.get(Document, document_id)
        if document is None or document.restaurant_id != restaurant_id:
            return "Error: no such document."
        made = calendar.propose_from_document(db, document, run=run)
        db.commit()
        if not made:
            return "Nothing proposed: no expiry date was read, the weekly cap is reached, or it is proposed already."
        return f"Calendar proposal {made[0].id}: {made[0].payload['title']} due {made[0].payload['due_on']}."

    return [list_recipes, propose_recipe, propose_agreed_price, propose_menu_reprice, explain_finding, propose_calendar_item]
