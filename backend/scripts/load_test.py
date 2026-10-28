"""Scale proof: synthetic restaurants on CSV connectors, the Monday pipeline on several workers, the figures printed.

    python scripts/load_test.py --restaurants 1000 --invoices 50 --workers 4 [--db sqlite:////tmp/load.db]

Builds N restaurants, each with three suppliers, eight weeks of sales and labor as a CSV connector would deliver
them, M invoices whose lines match through confirmed aliases (so the pipeline, not the person, is what is timed),
and one fixture invoice PDF to extract. Then it queues, for every restaurant, the Monday jobs -- extract, pnl, the
weekly detect (plate costs, onboarding) and the digest rebuild -- and runs them on W worker threads, then renders
every digest to HTML as the mail would.

Prints: wall-clock for the digests, p95 extraction latency, the cross-tenant check on a random sample of 100
digests, and agent cost per restaurant per week. Uses a database of its own (SQLite by default) and never the app's.
"""

from __future__ import annotations

import argparse
import os
import random
import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "fixtures" / "invoices" / "worldwide-produce-2026-10-21.pdf"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--restaurants", type=int, default=100)
    parser.add_argument("--invoices", type=int, default=50)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--db", default=f"sqlite:////tmp/fullhouse-load-{int(time.time())}.db")
    parser.add_argument("--sample", type=int, default=100)
    args = parser.parse_args()

    os.environ["DATABASE_URL"] = args.db
    os.environ["LLM_PROVIDER"] = "scripted"
    os.environ["WORKER_INPROCESS"] = "false"
    os.environ["SCHEDULER_ENABLED"] = "false"
    os.environ["STORAGE_DIR"] = f"/tmp/fullhouse-load-files-{int(time.time())}"

    from app.bootstrap import setup

    setup()
    from app.agents.definitions import AGENT_DEF
    from app.agents.models import AgentDefinition, AgentRun
    from app.audit.context import SYSTEM_ACTOR, actor_context
    from app.db.session import Base, SessionLocal, engine as db_engine
    from app.platform import flags, jobs
    from app.platform import handlers  # noqa: F401
    from app.platform.models import Job
    from app.prices import engine, mail
    from app.prices.models import Invoice, InvoiceLine, ItemAlias, PriceDigest, PriceItem
    from app.restaurant.models import Restaurant, Supplier
    from app.sales.models import LaborDay, SalesDay, SalesItem
    from app.documents.models import Document
    from app import storage

    Base.metadata.create_all(db_engine)
    rng = random.Random(7)
    today = date.today()
    monday = engine.week_start(today)
    db = SessionLocal()
    print(f"database: {args.db}")
    print(f"building {args.restaurants} restaurants x {args.invoices} invoices ...", flush=True)
    t0 = time.monotonic()

    ITEMS = [("Tomatoes, 25 lb case", "food", "lb", "case", 25, ("TOMATO ROMA 25#", "TOMATOES ROMA 25LB", "TOM RMA 25#")),
             ("Kale, green, 24 ct", "food", "ct", "case", 24, ("KALE GRN 24CT", "KALE GREEN 24 CT", "KALE 24CT")),
             ("Chicken breast, 40 lb case", "food", "lb", "case", 40, ("CHIX BRST BNLS 4/10#", "CHICKEN BREAST 40LB", "CHIX BRST 40#")),
             ("Olive oil, 4 gal case", "food", "gal", "case", 4, ("OLIVE OIL EV 4/1GAL", "OLIVE OIL 4/1 GAL", "EVOO 4X1GAL")),
             ("Napkins, 3000 ct", "paper", "ct", "case", 3000, ("NAPKIN DNR 3000CT", "NAPKINS 3000", "NAPKIN 3000CT")),
             ("Flour, 50 lb bag", "dry", "lb", "bag", 50, ("FLOUR AP 50#", "FLOUR 50LB", "AP FLOUR 50#"))]
    pdf = FIXTURE.read_bytes()

    with actor_context(SYSTEM_ACTOR):
        definition = db.query(AgentDefinition).filter_by(key=AGENT_DEF["key"]).first()
        if definition is None:
            cols = {c.key for c in AgentDefinition.__table__.columns}
            definition = AgentDefinition(**{"role": "boss", **{k: v for k, v in dict(AGENT_DEF).items() if k in cols and v is not None}})
            db.add(definition)
            db.flush()
        restaurant_ids = []
        for n in range(args.restaurants):
            restaurant = Restaurant(name=f"Load Test {n:04d}", timezone="America/Chicago", completed_steps=["details", "hours"],
                                    setup_completed_at=datetime.now(timezone.utc))
            db.add(restaurant)
            db.flush()
            rid = restaurant.id
            restaurant_ids.append(rid)
            flags.enable_all(db, rid)
            suppliers = []
            for name in ("Sysco Foods", "Green Valley Farms", "Harbor Dairy Co."):
                s = Supplier(restaurant_id=rid, name=name, lead_time_days=2)
                db.add(s)
                suppliers.append(s)
            db.flush()
            items = []
            for name, category, unit, pack_unit, pack_size, printed in ITEMS:
                item = PriceItem(restaurant_id=rid, name=name, category=category, unit=unit, pack_unit=pack_unit, pack_size=pack_size)
                db.add(item)
                items.append((item, printed))
            db.flush()
            for item, printed in items:
                for supplier, text in zip(suppliers, printed):
                    db.add(ItemAlias(restaurant_id=rid, supplier_id=supplier.id, description_key=engine.normalize(text), item_id=item.id, pack_size=item.pack_size))
            # Eight weeks of sales and labor, as the CSV connector writes them.
            for back in range(1, 57):
                day = today - timedelta(days=back)
                weekend = day.weekday() >= 4
                db.add(SalesDay(restaurant_id=rid, date=day, net_sales=round(2400 + (800 if weekend else 0) + rng.uniform(-200, 200), 2), source="csv"))
                db.add(LaborDay(restaurant_id=rid, date=day, hours=60 + (12 if weekend else 0), dollars=round(850 + (170 if weekend else 0), 2), source="csv"))
                for pos_id, pos_name, sold, price in (("MARG", "Margherita Pizza", 24, 14.0), ("CAES", "Kale Caesar", 16, 12.0)):
                    units = sold + (5 if weekend else 0) + rng.randint(-2, 2)
                    db.add(SalesItem(restaurant_id=rid, date=day, pos_item_id=pos_id, pos_item_name=pos_name, qty=units, net=round(units * price, 2), source="csv"))
            # The invoices, matched through the aliases: lines as printed, prices drifting a little.
            for k in range(args.invoices):
                supplier_index = k % 3
                supplier = suppliers[supplier_index]
                day = today - timedelta(days=(args.invoices - k) * (56 // max(args.invoices, 1)) + 1)
                invoice = Invoice(restaurant_id=rid, supplier_id=supplier.id, invoice_date=day, reference=f"L{n:04d}-{k:03d}", source="billcom", status="complete")
                db.add(invoice)
                db.flush()
                for position, (item, printed) in enumerate(items[:4], start=1):
                    drift = 1 + 0.004 * k + rng.uniform(-0.02, 0.02)
                    base = {"lb": 1.1, "ct": 1.3, "gal": 17.0}[item.unit]
                    unit_price = round(base * item.pack_size * drift * (1.0 if supplier_index else 0.96), 2)
                    qty = rng.randint(1, 6)
                    db.add(InvoiceLine(restaurant_id=rid, invoice_id=invoice.id, supplier_id=supplier.id, line_no=position, description=printed[supplier_index],
                                       qty=qty, pack_text="CS", unit_price=unit_price, extended=round(qty * unit_price, 2), item_id=item.id, pack_size=item.pack_size,
                                       stocked_at=datetime.now(timezone.utc)))
            key = storage.new_key(rid, "invoice", "worldwide.pdf")
            storage.put(key, pdf, "application/pdf")
            db.add(Document(restaurant_id=rid, doc_type="other", file_key=key, filename="worldwide.pdf", media_type="application/pdf", source="upload", status="received"))
            if n % 50 == 49:
                db.commit()
                print(f"  {n + 1} restaurants built ({time.monotonic() - t0:.0f}s)", flush=True)
        db.commit()
        build_seconds = time.monotonic() - t0
        print(f"built in {build_seconds:.0f}s: {db.query(Invoice).count()} invoices, {db.query(InvoiceLine).count()} lines", flush=True)

        # The Monday pipeline, queued for every restaurant.
        week_of = monday - timedelta(days=7)
        documents = {d.restaurant_id: d.id for d in db.query(Document)}
        for rid in restaurant_ids:
            jobs.enqueue(db, "extract", f"extract:{documents[rid]}", {"document_id": documents[rid]}, restaurant_id=rid)
            jobs.enqueue(db, "pnl", f"pnl:{rid}:{week_of}", {"restaurant_id": rid, "week_of": week_of.isoformat()}, restaurant_id=rid)
            jobs.enqueue(db, "detect", f"workflow:{rid}:digest:{monday}", {"restaurant_id": rid, "today": today.isoformat()}, restaurant_id=rid)
        db.commit()
        queued = db.query(Job).count()
    db.close()

    print(f"running {queued} jobs on {args.workers} workers ...", flush=True)
    t1 = time.monotonic()
    import threading

    workers = [jobs.Worker(name=f"load-{i}", poll_seconds=0.05) for i in range(args.workers)]
    threads = [threading.Thread(target=w.run, daemon=True) for w in workers]
    for t in threads:
        t.start()
    db = SessionLocal()
    while True:
        left = db.query(Job).filter(Job.status.in_(("queued", "active"))).count()
        if left == 0:
            break
        db.rollback()
        time.sleep(0.5)
    for w in workers:
        w.stop()
    for t in threads:
        t.join(timeout=5)
    pipeline_seconds = time.monotonic() - t1

    # Render every digest as the mail would (the artifact the spec times).
    t2 = time.monotonic()
    rendered = 0
    with actor_context(SYSTEM_ACTOR):
        for rid in restaurant_ids:
            digest = engine.latest_digest(db, rid, today)
            restaurant = db.get(Restaurant, rid)
            mail.render(db, digest, restaurant, "https://load.test", "https://load.test")
            rendered += 1
        db.commit()
    render_seconds = time.monotonic() - t2

    failed = db.query(Job).filter(Job.status == "failed").all()
    durations = {}
    for job in db.query(Job).filter(Job.status == "done"):
        durations.setdefault(job.queue, []).append(job.duration_ms or 0)

    def p95(values):
        if not values:
            return None
        values = sorted(values)
        return values[min(len(values) - 1, int(round(0.95 * len(values))) - 1)] / 1000

    # Cross-tenant check: in a random sample of digests, every invoice a card cites belongs to that restaurant.
    sample = rng.sample(restaurant_ids, min(args.sample, len(restaurant_ids)))
    leaks = 0
    checked_cards = 0
    for rid in sample:
        digest = db.query(PriceDigest).filter_by(restaurant_id=rid, week_start=monday).first()
        if digest is None:
            continue
        for card in digest.cards:
            checked_cards += 1
            for source in card.get("sources", []):
                if source.get("kind") == "invoice":
                    invoice = db.get(Invoice, source["id"])
                    if invoice is None or invoice.restaurant_id != rid:
                        leaks += 1
    runs = db.query(AgentRun).count()
    cost = sum(r.cost_usd or 0 for r in db.query(AgentRun))
    with_cards = sum(1 for d in db.query(PriceDigest) if d.cards)

    print("")
    print("=== Phase E figures ===")
    print(f"restaurants: {len(restaurant_ids)}  invoices: {len(restaurant_ids) * args.invoices}  workers: {args.workers}")
    print(f"build time (not timed by the spec): {build_seconds:.0f}s")
    print(f"Monday pipeline wall-clock ({queued} jobs: extract, pnl, detect+digest): {pipeline_seconds:.0f}s  ({pipeline_seconds / 60:.1f} min; the spec's bar is 20 min)")
    print(f"digest render for every restaurant: {render_seconds:.0f}s; digests with cards: {with_cards}/{len(restaurant_ids)}")
    for queue, values in sorted(durations.items()):
        print(f"  {queue:8s} jobs: {len(values):6d}  p50 {statistics.median(values) / 1000:.2f}s  p95 {p95(values):.2f}s")
    print(f"p95 extraction latency: {p95(durations.get('extract', [])) or 0:.2f}s (the spec's bar is 30 s)")
    print(f"failed jobs: {len(failed)}" + (f"  first: {failed[0].queue}: {failed[0].error[:160]}" if failed else ""))
    print(f"cross-tenant check: {checked_cards} cards in {len(sample)} sampled digests, {leaks} citing another restaurant's invoice")
    print(f"agent runs: {runs}; agent cost per restaurant per week: ${cost / max(len(restaurant_ids), 1):.4f} (rule-based provider: no model billed; the spec's bar is $0.50)")
    db.close()
    return 0 if not failed and leaks == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
