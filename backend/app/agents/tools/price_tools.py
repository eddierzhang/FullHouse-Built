"""What the agent can look up about prices, and the one way it answers a question in the thread.

Reading tools only, plus `answer_price_question`, which writes the agent's
reply with the invoices and items it rests on. There is no tool that
sends, orders, pays or edits an invoice: the agent cannot do those, by
construction rather than by instruction.
"""

import json
from datetime import date, timedelta

from anthropic import beta_tool
from sqlalchemy.orm import Session

from app.agents.models import AgentRun
from app.prices import engine
from app.prices.models import Invoice, InvoiceLine, PriceItem, ThreadMessage
from app.restaurant.models import Supplier


def build_tools(db: Session, run: AgentRun, agent_definition_id: str) -> list:
    restaurant_id = run.restaurant_id

    def supplier_name(supplier_id: str) -> str:
        supplier = db.get(Supplier, supplier_id)
        return supplier.name if supplier else "Supplier"

    @beta_tool
    def list_price_items() -> str:
        """List the items the restaurant buys, as matched from its supplier invoices: each
        item's id, name, category, pack, the last price paid per pack, which supplier charged
        it and when, and the change per pack over the last eight weeks. Start here to find the
        item a question is about.
        """
        rows = engine.item_rows(db, restaurant_id)
        return json.dumps([
            {"id": r.id, "name": r.name, "category": r.category, "pack": f"{r.pack_size:g} {r.unit} per {r.pack_unit}",
             "last_price": r.last_price, "last_supplier": r.last_supplier.name if r.last_supplier else None,
             "last_date": r.last_date.isoformat() if r.last_date else None, "change_8w": r.change_8w, "state": r.state}
            for r in rows
        ])

    @beta_tool
    def item_price_history(item_id: str) -> str:
        """Every invoice line for one item over the last eight weeks: date, supplier, the
        description as printed, quantity, price per pack and per unit, and what was delivered.
        Use it to say what was paid, where, and when.

        Args:
            item_id: The item's id, from list_price_items.
        """
        item = db.get(PriceItem, item_id)
        if item is None or item.restaurant_id != restaurant_id:
            return "Error: no item with that id. Use ids from list_price_items."
        detail = engine.item_detail(db, item)
        return json.dumps({
            "item": item.name, "pack": detail.pack_sentence, "note": detail.note,
            "lines": [
                {"invoice_id": l.invoice_id, "date": l.invoice_date.isoformat(), "supplier": l.supplier.name,
                 "as_printed": l.description, "qty": l.qty, "per_pack": l.unit_price, "per_unit": l.per_unit,
                 "extended": l.extended, "delivered_qty": l.delivered_qty, "credited": l.credited}
                for l in detail.lines
            ],
        })

    @beta_tool
    def supplier_price_summary() -> str:
        """For each supplier: how many items the restaurant buys from it, this month's spend,
        and for every item also bought elsewhere, whether this supplier's latest price per
        unit was the lowest. Use it for "which supplier is cheapest on X".
        """
        today = date.today()
        facts = engine.gather(db, restaurant_id, today)
        suppliers = {s.id: s for s in db.query(Supplier).filter(Supplier.restaurant_id == restaurant_id)}
        out = {sid: {"supplier": s.name, "items": set(), "spend_28d": 0.0, "cheapest_on": [], "dearer_on": []} for sid, s in suppliers.items()}
        for f in facts.values():
            latest_by: dict[str, engine.Priced] = {}
            for p in sorted(f.priced, key=lambda p: p.day):
                latest_by[p.supplier_id] = p
                if p.supplier_id in out:
                    out[p.supplier_id]["items"].add(f.item.name)
                    if p.day > today - timedelta(days=28):
                        out[p.supplier_id]["spend_28d"] += p.line.extended
            if len(latest_by) > 1:
                low = min(latest_by.values(), key=lambda p: p.per_unit)
                for sid, p in latest_by.items():
                    if sid in out:
                        (out[sid]["cheapest_on"] if p is low else out[sid]["dearer_on"]).append(
                            {"item": f.item.name, "category": f.item.category, "per_pack": engine.per_pack(f.item, p.per_unit),
                             "date": p.day.isoformat()})
        return json.dumps([
            {**row, "items": sorted(row["items"]), "spend_28d": round(row["spend_28d"], 2)} for row in out.values()
        ])

    @beta_tool
    def short_deliveries(days: int = 90) -> str:
        """Invoice lines where fewer packs arrived than were billed, with any credit since
        issued and what is still owed, by supplier. Use it for "how much did X short me".

        Args:
            days: How far back to look. Defaults to 90.
        """
        since = date.today() - timedelta(days=max(1, min(int(days), 365)))
        rows = (
            db.query(InvoiceLine, Invoice.invoice_date).join(Invoice, Invoice.id == InvoiceLine.invoice_id)
            .filter(InvoiceLine.restaurant_id == restaurant_id, InvoiceLine.delivered_qty.isnot(None),
                    InvoiceLine.delivered_qty < InvoiceLine.qty, Invoice.invoice_date >= since)
            .order_by(Invoice.invoice_date.desc()).all()
        )
        return json.dumps([
            {"invoice_id": l.invoice_id, "date": d.isoformat(), "supplier": supplier_name(l.supplier_id),
             "item": l.item.name if l.item else l.description, "billed": l.qty, "delivered": l.delivered_qty,
             "per_pack": l.unit_price, "owed": round((l.qty - l.delivered_qty) * l.unit_price - l.credited, 2)}
            for l, d in rows
        ])

    @beta_tool
    def answer_price_question(answer: str, invoice_ids: list[str], item_ids: list[str]) -> str:
        """Post your answer in the owner's thread. Two sentences at most, with the numbers: what
        they paid, where, when, and what another supplier charged if that is the point. Past
        tense for facts, imperative for what to do. Never call a price "wrong" or quote a
        "market price"; say only what changed or who charged less. Every answer must name at
        least one invoice or item it rests on, which become the chips under it.

        Args:
            answer: The answer, in plain sentences.
            invoice_ids: The invoices the answer rests on, from item_price_history or short_deliveries.
            item_ids: The items it is about, from list_price_items.
        """
        text = (answer or "").strip()
        if not text:
            return "Error: the answer is empty."
        sources = []
        for item_id in dict.fromkeys(item_ids or []):
            item = db.get(PriceItem, item_id)
            if item is not None and item.restaurant_id == restaurant_id:
                sources.append({"kind": "item", "id": item.id, "label": item.name})
        for invoice_id in dict.fromkeys(invoice_ids or []):
            invoice = db.get(Invoice, invoice_id)
            if invoice is not None and invoice.restaurant_id == restaurant_id:
                sources.append({"kind": "invoice", "id": invoice.id,
                                "label": f"{engine.short_date(invoice.invoice_date)}, {supplier_name(invoice.supplier_id)}"})
        if not sources:
            return "Error: name at least one invoice or item the answer rests on."
        message = db.query(ThreadMessage).filter(ThreadMessage.run_id == run.id, ThreadMessage.status == "pending").first()
        if message is None:
            message = ThreadMessage(restaurant_id=restaurant_id, role="agent", run_id=run.id)
            db.add(message)
        message.text, message.sources, message.status = text, sources, "ok"
        db.commit()
        return "Answer posted."

    return [list_price_items, item_price_history, supplier_price_summary, short_deliveries, answer_price_question]
