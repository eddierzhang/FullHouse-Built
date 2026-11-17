"""What makes the Margin & Labor Engine recompute: a new invoice price, or a saved recipe.

The app has no `price_points` table and never emitted an event for a matched invoice line, so this watches the
session instead of changing the code that writes invoices and recipes. After a flush it notes which items got a
new or changed price and which recipes were saved; just before the commit it emits one `price_point.created` and
one `recipe.saved` event per restaurant, and the subscribers in `app.platform.handlers` queue the recost. Nothing
is emitted for a restaurant with none of the modules on.
"""

from __future__ import annotations

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.prices.models import Invoice, InvoiceLine
from app.recipes.models import Recipe, RecipeLine

KEY = "margin_labor_pending"
WATCHED = (InvoiceLine, Invoice, Recipe, RecipeLine)
PRICE_FIELDS = ("item_id", "pack_size", "unit_price", "qty", "credited")
RECIPE_FIELDS = ("status", "yield_qty", "yield_unit", "kind", "portions", "pos_item_id", "menu_item_id")
LINE_FIELDS = ("item_id", "sub_recipe_id", "qty", "unit", "yield_pct")


def _changed(obj, fields) -> bool:
    state = inspect(obj)
    return any(state.attrs[name].history.has_changes() for name in fields)


def _note(session: Session, flush_context) -> None:
    watched = [(obj, obj in session.new, obj in session.deleted) for group in (session.new, session.dirty, session.deleted)
               for obj in group if isinstance(obj, WATCHED)]
    if not watched:
        return
    pending = session.info.setdefault(KEY, {"items": {}, "invoices": {}, "recipes": {}})
    for obj, fresh, gone in watched:
        if isinstance(obj, InvoiceLine) and obj.restaurant_id and obj.item_id and (fresh or gone or _changed(obj, PRICE_FIELDS)):
            pending["items"].setdefault(obj.restaurant_id, set()).add(obj.item_id)
        elif isinstance(obj, Invoice) and obj.restaurant_id and not fresh and (gone or _changed(obj, ("status", "invoice_date"))):
            pending["invoices"].setdefault(obj.restaurant_id, set()).add(obj.id)
        elif isinstance(obj, Recipe) and (fresh or gone or _changed(obj, RECIPE_FIELDS)):
            pending["recipes"].setdefault(obj.restaurant_id, set()).add(obj.id)
        elif isinstance(obj, RecipeLine) and (fresh or gone or _changed(obj, LINE_FIELDS)):
            pending["recipes"].setdefault(obj.restaurant_id, set()).add(obj.recipe_id)


def _emit(session: Session) -> None:
    if session.new or session.dirty or session.deleted:
        session.flush()  # the commit's own flush comes after this hook: flush now, so what it writes is seen
    pending = session.info.pop(KEY, None)
    if not pending or not (pending["items"] or pending["invoices"] or pending["recipes"]):
        return
    from app.platform import events, flags

    for restaurant_id, invoice_ids in pending["invoices"].items():
        for (item_id,) in session.query(InvoiceLine.item_id).filter(InvoiceLine.invoice_id.in_(sorted(invoice_ids)), InvoiceLine.item_id.isnot(None)):
            pending["items"].setdefault(restaurant_id, set()).add(item_id)
    on: dict[str, bool] = {}
    for kind, event_type, table in (("items", "price_point.created", "price_items"), ("recipes", "recipe.saved", "recipes")):
        for restaurant_id, ids in pending[kind].items():
            if restaurant_id not in on:
                on[restaurant_id] = flags.any_margin_labor(session, restaurant_id)
            if on[restaurant_id] and ids:
                events.emit(session, restaurant_id, event_type, table, None, {"ids": sorted(ids)[:500]})


def _forget(session: Session) -> None:
    session.info.pop(KEY, None)


_installed = False


def install() -> None:
    global _installed
    if _installed:
        return
    event.listen(Session, "after_flush", _note)
    event.listen(Session, "before_commit", _emit)
    event.listen(Session, "after_rollback", _forget)
    _installed = True
