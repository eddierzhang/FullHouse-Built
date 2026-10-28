"""The agent's reading of the invoices: matching, questions, the weekly digest, drafts.

Everything here is relative. A price went up, or another supplier charged
less; never "the right price". Where the evidence is thin the engine
withholds -- no card, no recommendation -- and where a line can't be
matched it writes a question rather than guessing. Five questions a week
per restaurant; the rest wait, in dollar order.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.agents.definitions import AGENT_KEY
from app.audit.context import AGENT, Actor, actor_context
from app.prices import schemas
from app.prices.models import (
    ANSWERED,
    DRAFT,
    HELD,
    OPEN,
    AgentQuestion,
    Invoice,
    InvoiceLine,
    ItemAlias,
    OutreachDraft,
    PriceDigest,
    PriceItem,
    SupplierRep,
    ThreadMessage,
)
from app.restaurant.models import Supplier

COMPLETE = "complete"
NEEDS_REVIEW = "needs_review"

#: The agent asks at most this many questions a week per restaurant. Beyond it, lines wait.
QUESTIONS_PER_WEEK = 5
#: The digest looks back this far, and the sparkline shows it.
WINDOW_WEEKS = 8
#: A cross-supplier comparison needs the other supplier's price from within this long.
COMPARE_DAYS = 28
#: Cards shown before "N smaller items".
MAX_CARDS = 10
#: "Not useful" hides an item from the digest for this long.
HIDE_WEEKS = 4
#: A match question is asked when the best existing item overlaps the printed line this much.
MATCH_THRESHOLD = 0.34

SECTION_LABELS = {"switch": "Switch supplier", "up": "Price went up", "owed": "Owed to you", "margin": "Menu margin", "due": "Coming due"}

#: Invoice shorthand, as broadline distributors print it.
ABBREVIATIONS = {
    "CHIX": "chicken", "CHKN": "chicken", "BRST": "breast", "BNLS": "boneless", "SKNLS": "skinless",
    "THGH": "thigh", "THIGH": "thigh", "GRN": "green", "GRND": "ground", "HVY": "heavy", "CRM": "cream",
    "MOZZ": "mozzarella", "FRSH": "fresh", "FRESH": "fresh", "PARM": "parmesan", "REGG": "reggiano",
    "UNS": "unsalted", "UNSLTD": "unsalted", "SLTD": "salted", "WHL": "whole", "WHT": "white",
    "AP": "all-purpose", "OIL": "oil", "FRY": "fryer", "CLR": "clear", "JIB": "jug", "EVOO": "olive oil",
    "EV": "extra virgin", "TOM": "tomato", "TOMATO": "tomato", "ROMA": "roma", "LETT": "lettuce",
    "NAPKIN": "napkin", "NAPKINS": "napkins", "DNR": "dinner", "FL": "foil", "ALUM": "aluminum",
    "LG": "large", "SM": "small", "MED": "medium", "JMB": "jumbo", "XL": "extra large",
    "CS": "", "CT": "", "EA": "", "BG": "", "LB": "", "LBS": "", "QT": "", "GAL": "", "OZ": "", "ML": "", "PK": "",
}
NOISE = re.compile(r"[^A-Z0-9#/ ]+")
PACK_PATTERNS = [
    # "4/10#", "6/1#": n packs of m lb
    (re.compile(r"\b(\d+)\s*/\s*(\d+(?:\.\d+)?)\s*#"), lambda m: (float(m[1]) * float(m[2]), "lb")),
    (re.compile(r"\b(\d+)\s*/\s*(\d+(?:\.\d+)?)\s*LBS?\b"), lambda m: (float(m[1]) * float(m[2]), "lb")),
    (re.compile(r"\b(\d+)\s*/\s*QT\b"), lambda m: (float(m[1]), "qt")),
    (re.compile(r"\b(\d+)\s*/\s*(\d+)\s*GAL\b"), lambda m: (float(m[1]) * float(m[2]), "gal")),
    (re.compile(r"\b(\d+)\s*/\s*(\d+)\s*ML\b"), lambda m: (float(m[1]), "bottle")),
    (re.compile(r"\b(\d+)\s*X\s*(\d+)\s*ML\b"), lambda m: (float(m[1]), "bottle")),
    (re.compile(r"\b(\d+)\s*/\s*(\d+)\s*OZ\b"), lambda m: (float(m[1]), "ea")),
    (re.compile(r"\b(\d+(?:\.\d+)?)\s*#"), lambda m: (float(m[1]), "lb")),
    (re.compile(r"\b(\d+(?:\.\d+)?)\s*LBS?\b"), lambda m: (float(m[1]), "lb")),
    (re.compile(r"\b(\d+)\s*CT\b"), lambda m: (float(m[1]), "ct")),
    (re.compile(r"\b(\d+)\s*PK\b"), lambda m: (float(m[1]), "ct")),
    (re.compile(r"\b(\d+)\s*/\s*(\d+)\b"), lambda m: (float(m[1]), "ea")),
]


def normalize(description: str) -> str:
    """The key an alias is stored under: upper case, one space, no punctuation but # and /."""
    return re.sub(r"\s+", " ", NOISE.sub(" ", description.upper())).strip()


def parse_pack(*texts: str | None) -> tuple[float, str] | None:
    """How many of what one pack holds, read from the printed line: "24CT" -> (24, "ct"), "4/10#" -> (40, "lb")."""
    for text in texts:
        if not text:
            continue
        upper = text.upper()
        for pattern, read in PACK_PATTERNS:
            match = pattern.search(upper)
            if match:
                size, unit = read(match)
                if size > 0:
                    return size, unit
    return None


def words(description: str) -> list[str]:
    """The printed line as plain words, abbreviations expanded, sizes dropped."""
    out = []
    for token in normalize(description).split():
        if re.fullmatch(r"[\d/#.]+(CT|LB|LBS|QT|GAL|OZ|ML|CS|EA|PK)?", token):
            continue
        expanded = ABBREVIATIONS.get(token, token.lower())
        out.extend(w for w in expanded.split() if w)
    return out


def propose_name(description: str, pack: tuple[float, str] | None) -> str:
    """A canonical name for a line no item matches: "Heavy cream, 12 qt"."""
    ws = words(description)
    # Adjectives printed after the noun come first in plain English: "CRM HVY" -> "heavy cream".
    if len(ws) >= 2 and ws[1] in ("heavy", "green", "fresh", "ground", "whole", "white", "boneless", "unsalted"):
        ws = [ws[1], ws[0]] + ws[2:]
    name = " ".join(ws) if ws else description.strip()
    name = name[:1].upper() + name[1:]
    if pack:
        size, unit = pack
        if unit == "ea" and size == 1:
            return name[:120]
        return f"{name}, {size:g} {unit}"[:120]
    return name[:120]


def similarity(a: str, b: str) -> float:
    """Word overlap between two descriptions, 0 to 1."""
    wa, wb = set(words(a)), set(words(b))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


#: Units that read as words take an "s"; abbreviations (lb, ct, qt, gal) don't.
WORD_UNITS = frozenset({"bottle", "loaf", "bag", "head", "bunch", "can", "jar", "tub", "bottles"})


def unit_words(size: float, unit: str) -> str:
    """"24 ct", "12 bottles", "1 bottle"."""
    word = unit.rstrip("s") if unit in WORD_UNITS else unit
    if word in WORD_UNITS and size != 1:
        word += "s"
    return f"{size:g} {word}"


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def money(value: float) -> str:
    return f"${value:,.2f}"


def short_date(day: date) -> str:
    return f"{day.strftime('%b')} {day.day}"


# --- ingesting ------------------------------------------------------------------------


def find_candidate(db: Session, restaurant_id: str | None, description: str) -> tuple[PriceItem | None, float]:
    """The existing item the printed line most resembles, and how closely."""
    best, score = None, 0.0
    for item in db.query(PriceItem).filter(PriceItem.restaurant_id == restaurant_id):
        s = similarity(description, item.name)
        if s > score:
            best, score = item, s
    return best, score


def questions_this_week(db: Session, restaurant_id: str | None, week: date) -> int:
    return (
        db.query(AgentQuestion)
        .filter(AgentQuestion.restaurant_id == restaurant_id, AgentQuestion.week_start == week,
                AgentQuestion.status.in_((OPEN, ANSWERED)))
        .count()
    )


def ask_about_line(db: Session, line: InvoiceLine, today: date, changed_from: float | None = None) -> AgentQuestion:
    """Write the one question that would let this line count: which item, how big a pack, or whether the pack changed."""
    description = line.description
    pack = parse_pack(description, line.pack_text)
    item = db.get(PriceItem, line.item_id) if line.item_id else None
    if item is not None and changed_from is not None and pack is not None:
        # Matched, and the printed pack isn't the one confirmed before. Per-unit prices wait on the answer.
        new_size, unit = pack[0], item.unit
        question = AgentQuestion(
            kind="repack",
            text=f"Pack changed? This says {unit_words(new_size, unit)} per {item.pack_unit}; you had {unit_words(changed_from, unit)}.",
            options=[{"key": "yes", "label": f"Yes, {unit_words(new_size, unit)} now"},
                     {"key": "no", "label": f"No, still {unit_words(changed_from, unit)}"}],
            proposed_item_id=item.id, proposed_pack_size=new_size,
        )
    elif item is not None:
        # Matched, pack unknown: the item's own pack is the assumption to confirm.
        size, unit = item.pack_size, item.unit
        question = AgentQuestion(
            kind="pack", text=f"Is this {unit_words(size, unit)} per {item.pack_unit}?",
            options=[{"key": "yes", "label": f"Yes, {unit_words(size, unit)}"}, {"key": "no", "label": "No"}],
            proposed_item_id=item.id, proposed_pack_size=size,
        )
    else:
        candidate, score = find_candidate(db, line.restaurant_id, description)
        if candidate is not None and score >= MATCH_THRESHOLD:
            question = AgentQuestion(
                kind="match", text=f"Is this the same as {candidate.name}?",
                options=[{"key": "yes", "label": "Yes"}, {"key": "no", "label": "No, it's different"}],
                proposed_item_id=candidate.id, proposed_pack_size=pack[0] if pack else None,
            )
        else:
            name = propose_name(description, pack)
            question = AgentQuestion(
                kind="match", text=f"Is this the same as {name}?",
                options=[{"key": "yes", "label": "Yes"}, {"key": "no", "label": "No, it's different"}],
                proposed_name=name, proposed_pack_size=pack[0] if pack else None,
            )
    week = week_start(today)
    question.restaurant_id = line.restaurant_id
    question.line_id = line.id
    question.dollar_impact = line.extended
    question.week_start = week
    question.status = OPEN if questions_this_week(db, line.restaurant_id, week) < QUESTIONS_PER_WEEK else HELD
    db.add(question)
    db.flush()
    if question.status == OPEN:
        db.add(ThreadMessage(restaurant_id=line.restaurant_id, role="agent", text=question.text,
                             sources=[invoice_source(line)], question_id=question.id))
    return question


def invoice_source(line: InvoiceLine) -> dict:
    invoice = line.invoice
    return {"kind": "invoice", "id": invoice.id, "label": f"{short_date(invoice.invoice_date)}, {_supplier_name(line)}"}


def _supplier_name(line_or_id) -> str:
    from sqlalchemy.orm import object_session

    supplier_id = line_or_id.supplier_id if isinstance(line_or_id, InvoiceLine) else line_or_id
    db = object_session(line_or_id) if isinstance(line_or_id, InvoiceLine) else None
    supplier = db.get(Supplier, supplier_id) if db else None
    return supplier.name if supplier else "the supplier"


def ingest_invoice(
    db: Session, restaurant_id: str | None, supplier: Supplier, invoice_date: date, lines: list[schemas.LineIn],
    source: str = "typed", reference: str | None = None, today: date | None = None,
    total: float | None = None, external_id: str | None = None, stock: bool = True,
) -> tuple[Invoice, list[AgentQuestion]]:
    """Save a bill as printed, match what the restaurant has already confirmed, and ask about the rest.

    An invoice whose lines don't add up (to each other, or to `total`), or that was read with doubt, is
    saved as needing review: its questions are asked, but it stays out of prices until confirmed.
    With `stock`, what the matched lines delivered is booked into Inventory and reorders are
    proposed where the stock is low (app.prices.stock); a seed of old invoices passes False.
    """
    from app.prices.extract import check

    today = today or date.today()
    reasons = check(lines, total)
    invoice = Invoice(restaurant_id=restaurant_id, supplier_id=supplier.id, invoice_date=invoice_date,
                      reference=reference, source=source, total=total, external_id=external_id,
                      status=NEEDS_REVIEW if reasons else COMPLETE, review_reasons=reasons)
    db.add(invoice)
    db.flush()
    aliases = {
        a.description_key: a
        for a in db.query(ItemAlias).filter(ItemAlias.restaurant_id == restaurant_id, ItemAlias.supplier_id == supplier.id)
    }
    rows = []
    changed: dict[str, float] = {}
    for number, line in enumerate(lines, start=1):
        extended = line.extended if line.extended is not None else round(line.qty * line.unit_price, 2)
        row = InvoiceLine(
            restaurant_id=restaurant_id, invoice_id=invoice.id, supplier_id=supplier.id, line_no=number,
            description=line.description.strip()[:200], qty=line.qty, pack_text=(line.pack_text or "").strip() or None,
            unit_price=line.unit_price, extended=extended, delivered_qty=line.delivered_qty, confidence=line.confidence,
        )
        alias = aliases.get(normalize(row.description))
        if alias is not None:
            row.item_id = alias.item_id
            pack = parse_pack(row.description, row.pack_text)
            item = db.get(PriceItem, alias.item_id)
            if alias.pack_size and pack and item is not None and pack[1] == item.unit and pack[0] != alias.pack_size:
                # The same printed item, a different pack: asked, not assumed (a 12 ct billed as a 24 ct is a price rise).
                changed[id(row)] = alias.pack_size
            else:
                row.pack_size = alias.pack_size or (pack[0] if pack else None)
        db.add(row)
        rows.append(row)
    db.flush()
    # Biggest lines first, so the questions that matter most are the ones that fit under the cap.
    # The questions are the agent's, whoever brought the invoice in: the history says so.
    questions = []
    with actor_context(Actor(type=AGENT, id=AGENT_KEY, restaurant_id=restaurant_id)):
        for row in sorted(rows, key=lambda r: -r.extended):
            if row.item_id is None or row.pack_size is None:
                questions.append(ask_about_line(db, row, today, changed_from=changed.get(id(row))))
        db.flush()
    if stock:
        from app.prices import stock as stock_module

        stock_module.sync(db, restaurant_id, today, trigger=f"invoice from {supplier.name}")
    return invoice, questions


def promote_held(db: Session, restaurant_id: str | None, today: date) -> int:
    """At a new week, let the biggest waiting questions through, up to the cap."""
    week = week_start(today)
    room = QUESTIONS_PER_WEEK - questions_this_week(db, restaurant_id, week)
    if room <= 0:
        return 0
    held = (
        db.query(AgentQuestion)
        .filter(AgentQuestion.restaurant_id == restaurant_id, AgentQuestion.status == HELD)
        .order_by(AgentQuestion.dollar_impact.desc(), AgentQuestion.created_at)
        .limit(room)
        .all()
    )
    for question in held:
        question.status = OPEN
        question.week_start = week
        db.add(ThreadMessage(restaurant_id=restaurant_id, role="agent", text=question.text,
                             sources=[invoice_source(question.line)], question_id=question.id))
    db.flush()
    return len(held)


def _item_named(db: Session, restaurant_id: str | None, name: str, pack: tuple[float, str] | None,
                category: str = "food") -> PriceItem:
    name = name.strip()[:120]
    existing = (
        db.query(PriceItem).filter(PriceItem.restaurant_id == restaurant_id, PriceItem.name.ilike(name)).first()
    )
    if existing:
        return existing
    size, unit = pack if pack else (1.0, "ea")
    item = PriceItem(restaurant_id=restaurant_id, name=name, category=category, unit=unit, pack_size=size,
                     pack_unit="case" if size > 1 else "each")
    db.add(item)
    db.flush()
    return item


def answer_question(db: Session, question: AgentQuestion, answer: str, text: str | None, pack_size: float | None,
                    by: str) -> AgentQuestion:
    """Apply the owner's answer to the line, remember it for next time, and say so in the thread."""
    if question.status == ANSWERED:
        return question
    line = question.line
    pack = parse_pack(line.description, line.pack_text)
    item: PriceItem | None = None
    if question.kind == "match":
        if answer == "yes":
            item = (db.get(PriceItem, question.proposed_item_id) if question.proposed_item_id
                    else _item_named(db, line.restaurant_id, question.proposed_name or line.description, pack))
        elif answer == "other" and text:
            item = _item_named(db, line.restaurant_id, text, pack)
        else:
            # "No, it's different": a new item, named from the printed line, so it stops asking.
            item = _item_named(db, line.restaurant_id, propose_name(line.description, pack), pack)
        line.item_id = item.id
        size = pack_size or question.proposed_pack_size or (pack[0] if pack else None) or item.pack_size
        line.pack_size = size
    else:
        item = db.get(PriceItem, question.proposed_item_id)
        if answer == "yes":
            line.pack_size = question.proposed_pack_size
        elif question.kind == "repack" and answer == "no":
            # Still the pack confirmed before: the line counts at that pack, and the alias stands.
            known = (
                db.query(ItemAlias.pack_size)
                .filter(ItemAlias.restaurant_id == line.restaurant_id, ItemAlias.supplier_id == line.supplier_id,
                        ItemAlias.description_key == normalize(line.description)).first()
            )
            line.pack_size = known[0] if known and known[0] else None
        elif pack_size:
            line.pack_size = pack_size
        elif answer == "other" and text:
            read = parse_pack(text) or (
                (float(text.strip().split()[0]), item.unit if item else "ea") if text.strip().split()[0].replace(".", "").isdigit() else None
            )
            line.pack_size = read[0] if read else None
        # A plain "No" leaves the pack unknown: the line waits for "Wrong?" on the item page.
    if item is not None and line.pack_size is not None:
        key = normalize(line.description)
        alias = (
            db.query(ItemAlias)
            .filter(ItemAlias.restaurant_id == line.restaurant_id, ItemAlias.supplier_id == line.supplier_id,
                    ItemAlias.description_key == key)
            .first()
        )
        if alias is None:
            db.add(ItemAlias(restaurant_id=line.restaurant_id, supplier_id=line.supplier_id, description_key=key,
                             item_id=item.id, pack_size=line.pack_size))
        else:
            alias.item_id, alias.pack_size = item.id, line.pack_size
        # Every other unmatched line printed the same way, from the same supplier, follows.
        for other in db.query(InvoiceLine).filter(
            InvoiceLine.restaurant_id == line.restaurant_id, InvoiceLine.supplier_id == line.supplier_id,
            InvoiceLine.id != line.id, InvoiceLine.item_id.is_(None),
        ):
            if normalize(other.description) == key:
                other.item_id, other.pack_size = item.id, line.pack_size
    question.status = ANSWERED
    question.answer_key = answer
    question.answer_text = (text or "").strip()[:200] or None
    question.answered_at = datetime.now(timezone.utc)
    question.answered_by = by
    if item is not None and question.kind == "repack":
        size = line.pack_size
        said = (f"Noted. {item.name} from {_supplier_name(line)} is {unit_words(size, item.unit)} per {item.pack_unit} now."
                if answer == "yes" and size else f"Noted. {item.name} stays {unit_words(size, item.unit)} per {item.pack_unit}."
                if size else f"Noted. {item.name} waits for a pack size.")
        db.add(ThreadMessage(restaurant_id=line.restaurant_id, role="agent", text=said,
                             sources=[{"kind": "item", "id": item.id, "label": item.name}]))
    elif item is not None:
        suppliers = sorted({
            _supplier_name(sid) for (sid,) in db.query(InvoiceLine.supplier_id)
            .filter(InvoiceLine.item_id == item.id).distinct()
        })
        across = (f" {item.name} now compares across {' and '.join(suppliers)}." if len(suppliers) > 1
                  else f" {item.name} counts from the next invoice.")
        db.add(ThreadMessage(restaurant_id=line.restaurant_id, role="agent", text="Matched." + across,
                             sources=[{"kind": "item", "id": item.id, "label": item.name}]))
    db.flush()
    if item is not None and line.pack_size is not None:
        # A line that now counts may be a delivery not yet in stock, or price a reorder.
        from app.prices import stock as stock_module

        stock_module.sync(db, line.restaurant_id, trigger=f"{item.name} matched")
    return question


# --- the digest -----------------------------------------------------------------------


@dataclass
class Priced:
    """One matched line, priced per canonical unit."""

    line: InvoiceLine
    day: date
    supplier_id: str
    per_unit: float
    units: float


@dataclass
class ItemFacts:
    item: PriceItem
    priced: list[Priced] = field(default_factory=list)

    @property
    def dates(self) -> set[date]:
        return {p.day for p in self.priced}

    @property
    def latest(self) -> Priced | None:
        return max(self.priced, key=lambda p: (p.day, p.line.line_no)) if self.priced else None

    def weekly_units(self, today: date) -> float:
        since = today - timedelta(days=COMPARE_DAYS)
        total = sum(p.units for p in self.priced if p.day > since)
        return total / (COMPARE_DAYS / 7) if total else 0.0


def gather(db: Session, restaurant_id: str | None, today: date, item_id: str | None = None) -> dict[str, ItemFacts]:
    """Every matched, priced line in the window, by item."""
    since = today - timedelta(weeks=WINDOW_WEEKS)
    query = (
        db.query(InvoiceLine, Invoice.invoice_date)
        .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
        .filter(InvoiceLine.restaurant_id == restaurant_id, InvoiceLine.item_id.isnot(None),
                InvoiceLine.pack_size.isnot(None), Invoice.invoice_date > since, Invoice.invoice_date <= today,
                Invoice.status == COMPLETE)
    )
    if item_id:
        query = query.filter(InvoiceLine.item_id == item_id)
    facts: dict[str, ItemFacts] = {}
    items = {i.id: i for i in db.query(PriceItem).filter(PriceItem.restaurant_id == restaurant_id)}
    for line, day in query:
        item = items.get(line.item_id)
        if item is None or not line.pack_size:
            continue
        facts.setdefault(item.id, ItemFacts(item))
        facts[item.id].priced.append(Priced(line, day, line.supplier_id, line.unit_price / line.pack_size,
                                            line.qty * line.pack_size))
    return facts


def per_pack(item: PriceItem, per_unit: float) -> float:
    return round(per_unit * item.pack_size, 2)


def cards_for(db: Session, facts: ItemFacts, today: date, suppliers: dict[str, Supplier]) -> list[dict]:
    """The digest cards one item earns this week: at most one per section."""
    item = facts.item
    out: list[dict] = []
    if item.hidden_until and item.hidden_until > today:
        return out
    if len(facts.dates) < 2:
        return out  # not enough matched invoices to compare yet
    latest = facts.latest
    assert latest is not None
    weekly = facts.weekly_units(today)
    name = lambda sid: suppliers[sid].name if sid in suppliers else "the supplier"  # noqa: E731
    src = lambda p: {"kind": "invoice", "id": p.line.invoice_id, "label": f"{short_date(p.day)}, {name(p.supplier_id)}"}  # noqa: E731
    pack_word = item.pack_unit if item.pack_size > 1 else item.unit

    # Switch supplier: food, dry goods and paper only, and not while the owner is already moving it.
    moving = item.moved_at and item.moved_at.date() > today - timedelta(days=COMPARE_DAYS)
    if item.category not in schemas.SINGLE_SOURCE and weekly > 0 and not moving:
        recent = [p for p in facts.priced if p.day > today - timedelta(days=COMPARE_DAYS) and p.supplier_id != latest.supplier_id]
        if recent:
            # The other supplier's latest price, not its lowest ever.
            by_supplier: dict[str, Priced] = {}
            for p in sorted(recent, key=lambda p: p.day):
                by_supplier[p.supplier_id] = p
            other = min(by_supplier.values(), key=lambda p: p.per_unit)
            if other.per_unit < latest.per_unit * 0.995:
                impact = round((latest.per_unit - other.per_unit) * weekly, 2)
                if impact >= 0.5:
                    first = item.name.split(",")[0].strip().lower()
                    out.append({
                        "section": "switch", "item_id": item.id, "item_name": item.name, "impact": impact,
                        "sentence": (f"You paid {money(per_pack(item, latest.per_unit))} at {name(latest.supplier_id)} on "
                                     f"{short_date(latest.day)}. {name(other.supplier_id)} charged "
                                     f"{money(per_pack(item, other.per_unit))} on {short_date(other.day)}."),
                        "action": {"kind": "move", "label": f"Move {first}", "draft_kind": None,
                                   "supplier_id": other.supplier_id},
                        "sources": [src(latest), src(other)],
                    })

    # Price went up: at the supplier you buy from now, against the earliest price in the window.
    same = sorted((p for p in facts.priced if p.supplier_id == latest.supplier_id), key=lambda p: p.day)
    if len(same) >= 2 and weekly > 0:
        earliest = same[0]
        if latest.per_unit > earliest.per_unit * 1.01 and latest.day > earliest.day:
            impact = round((latest.per_unit - earliest.per_unit) * weekly, 2)
            if impact >= 0.5:
                pct = (latest.per_unit / earliest.per_unit - 1) * 100
                weeks = max(1, round((latest.day - earliest.day).days / 7))
                span = "a week" if weeks == 1 else f"{weeks} weeks"
                out.append({
                    "section": "up", "item_id": item.id, "item_name": item.name, "impact": impact,
                    "sentence": (f"{name(latest.supplier_id)} charged {money(per_pack(item, latest.per_unit))} a {pack_word} on "
                                 f"{short_date(latest.day)}, up from {money(per_pack(item, earliest.per_unit))} on "
                                 f"{short_date(earliest.day)}, {pct:.0f}% in {span}."),
                    "action": {"kind": "draft", "label": "Draft message", "draft_kind": "price_challenge",
                               "supplier_id": latest.supplier_id},
                    "sources": [src(latest), src(earliest)],
                })

    # Owed to you: billed more than arrived, and no credit yet.
    owed, sources, parts = 0.0, [], []
    for p in sorted(facts.priced, key=lambda p: p.day):
        line = p.line
        if line.delivered_qty is None or line.delivered_qty >= line.qty:
            continue
        short = round((line.qty - line.delivered_qty) * line.unit_price - line.credited, 2)
        if short <= 0:
            continue
        owed += short
        sources.append(src(p))
        later = [q for q in facts.priced if q.supplier_id == p.supplier_id and q.day > p.day]
        credit = f"No credit on the {short_date(max(later, key=lambda q: q.day).day)} invoice." if later else "No credit yet."
        parts.append(f"{name(p.supplier_id)} billed {line.qty:g} {pack_word}{'s' if line.qty != 1 else ''} on "
                     f"{short_date(p.day)} and delivered {line.delivered_qty:g}. {credit}")
    if owed > 0:
        out.append({
            "section": "owed", "item_id": item.id, "item_name": item.name, "impact": round(owed, 2),
            "sentence": " ".join(parts[:2]),
            "action": {"kind": "draft", "label": "Draft message", "draft_kind": "credit_claim",
                       "supplier_id": sources and _supplier_of(db, sources[-1]["id"])},
            "sources": sources[:3],
        })
    return out


def _supplier_of(db: Session, invoice_id: str) -> str | None:
    invoice = db.get(Invoice, invoice_id)
    return invoice.supplier_id if invoice else None


def build_digest(db: Session, restaurant_id: str | None, today: date | None = None) -> PriceDigest:
    """Write (or rewrite) this week's digest from everything matched so far."""
    today = today or date.today()
    week = week_start(today)
    promote_held(db, restaurant_id, today)
    suppliers = {s.id: s for s in db.query(Supplier).filter(Supplier.restaurant_id == restaurant_id)}
    cards: list[dict] = []
    from app.platform import flags
    from app.prices import agreed

    documents_on = flags.enabled(db, restaurant_id, flags.V2_DOCUMENTS)
    for facts in gather(db, restaurant_id, today).values():
        cards.extend(cards_for(db, facts, today, suppliers))
        if documents_on and not (facts.item.hidden_until and facts.item.hidden_until > today):
            over = agreed.overcharges(db, facts, today, suppliers)
            if over is not None:
                # The agreed price is the better basis for "owed": it replaces a short-delivery card for the same item.
                cards = [c for c in cards if not (c["section"] == "owed" and c["item_id"] == facts.item.id)]
                cards.append(over)
    if documents_on:
        from app.recipes import engine as recipes

        cards.extend(recipes.menu_cards(db, restaurant_id, today))
    if flags.enabled(db, restaurant_id, flags.V2_ORDERS):
        from app.restaurant import orders as orders_module

        cards.extend(orders_module.short_delivery_cards(db, restaurant_id, today))
    cards.sort(key=lambda c: -c["impact"])
    through = (
        db.query(Invoice.invoice_date).filter(Invoice.restaurant_id == restaurant_id, Invoice.invoice_date <= today)
        .order_by(Invoice.invoice_date.desc()).first()
    )
    digest = (
        db.query(PriceDigest).filter(PriceDigest.restaurant_id == restaurant_id, PriceDigest.week_start == week).first()
    )
    if digest is None:
        digest = PriceDigest(restaurant_id=restaurant_id, week_start=week)
        db.add(digest)
    digest.cards = cards
    digest.total = round(sum(c["impact"] for c in cards), 2)
    digest.item_count = len({c["item_id"] for c in cards})
    digest.supplier_count = len({s["label"].split(", ", 1)[1] for c in cards for s in c["sources"]
                                 if s.get("kind") == "invoice" and ", " in s["label"]})
    digest.invoices_through = through[0] if through else None
    digest.built_at = datetime.now(timezone.utc)
    db.flush()
    return digest


def latest_digest(db: Session, restaurant_id: str | None, today: date | None = None) -> PriceDigest:
    """This week's digest, built on first request."""
    today = today or date.today()
    digest = (
        db.query(PriceDigest).filter(PriceDigest.restaurant_id == restaurant_id, PriceDigest.week_start == week_start(today)).first()
    )
    return digest or build_digest(db, restaurant_id, today)


def recovered_to_date(db: Session, restaurant_id: str | None) -> float | None:
    """The dollars a week of every card that was acted on: the item moved, or a message to the rep sent.
    Summed over past digests; None until there is one."""
    rows = db.query(PriceDigest).filter(PriceDigest.restaurant_id == restaurant_id).order_by(PriceDigest.week_start).all()
    if not rows:
        return None
    sent_after: dict[str, date] = {}
    for draft in db.query(OutreachDraft).filter(OutreachDraft.restaurant_id == restaurant_id, OutreachDraft.sent_at.isnot(None)):
        day = draft.sent_at.date()
        sent_after[draft.item_id] = min(sent_after.get(draft.item_id, day), day)
    moved: dict[str, date] = {i.id: i.moved_at.date() for i in db.query(PriceItem).filter(PriceItem.restaurant_id == restaurant_id, PriceItem.moved_at.isnot(None))}
    total = 0.0
    for digest in rows:
        for card in digest.cards:
            item_id = card.get("item_id")
            acted = (card["section"] == "switch" and item_id in moved and moved[item_id] >= digest.week_start) or \
                    (card["section"] in ("up", "owed") and item_id in sent_after and sent_after[item_id] >= digest.week_start)
            if acted:
                total += float(card.get("impact") or 0)
    return round(total, 2)


def sections_for(db: Session, restaurant_id: str | None) -> list[str]:
    """The three sections always; menu margin and coming due once their phases are on."""
    from app.platform import flags

    keys = ["switch", "up", "owed"]
    if flags.enabled(db, restaurant_id, flags.V2_DOCUMENTS):
        keys.append("margin")
    if flags.enabled(db, restaurant_id, flags.V2_CALENDAR):
        keys.append("due")
    return keys


def digest_out(db: Session, digest: PriceDigest) -> schemas.DigestOut:
    from app.platform import flags
    from app.sales import pnl

    # Monday's digest reports on the week just ended: the one before the digest's own week.
    prime = pnl.prime_line(db, digest.restaurant_id, digest.week_start)
    cards = [schemas.Card(**c) for c in digest.cards]
    shown, hidden = cards[:MAX_CARDS], cards[MAX_CARDS:]
    sections = [
        schemas.DigestSection(key=key, label=label, total=round(sum(c.impact for c in cards if c.section == key), 2),
                              cards=[c for c in shown if c.section == key])
        for key, label in SECTION_LABELS.items() if key in sections_for(db, digest.restaurant_id)
    ]
    open_count = (
        db.query(AgentQuestion).filter(AgentQuestion.restaurant_id == digest.restaurant_id, AgentQuestion.status == OPEN).count()
    )
    names = sorted({
        s.name for s in db.query(Supplier).filter(Supplier.restaurant_id == digest.restaurant_id)
        if any(src.label.endswith(f", {s.name}") for c in cards for src in c.sources)
    })
    from app.forecast import engine as forecast

    accuracy = forecast.accuracy_last_week(db, digest.restaurant_id, digest.week_start) if flags.enabled(db, digest.restaurant_id, flags.V2_ORDERS) else None
    return schemas.DigestOut(
        prime=schemas.PrimeLine(**prime) if prime else None,
        forecast_accuracy=accuracy, recovered_to_date=recovered_to_date(db, digest.restaurant_id),
        id=digest.id, week_start=digest.week_start, total=digest.total, item_count=digest.item_count,
        supplier_count=digest.supplier_count, sections=sections, hidden_count=len(hidden), questions_open=open_count,
        built_at=digest.built_at, invoices_through=digest.invoices_through, supplier_names=names,
    )


def talking_points(db: Session, digest: PriceDigest) -> schemas.TalkingPointsOut:
    """Five lines for the manager meeting, in the owner's voice, from the digest."""
    lines = []
    for card in digest.cards[:5]:
        first = card["item_name"].split(",")[0].strip()
        if card["section"] == "switch":
            who = card["sentence"].split(". ")[1].split(" charged")[0]
            lines.append(f"{first} moves to {who} this week, {money(card['impact'])} a week less.")
        elif card["section"] == "up":
            who = card["sentence"].split(" charged")[0]
            lines.append(f"{first} went up at {who}, {money(card['impact'])} a week more. I'm asking the rep.")
        else:
            who = card["sentence"].split(" billed")[0]
            lines.append(f"{who} shorted us on {first.lower()}, {money(card['impact'])}. Credit claim goes out this week.")
    if not lines:
        lines.append("Nothing moved enough to raise this week.")
    sources = [{"kind": "digest", "id": digest.id, "label": f"Digest, {short_date(digest.week_start)}"}]
    return schemas.TalkingPointsOut(lines=lines[:5], sources=[schemas.Source_(**s) for s in sources])


# --- items ----------------------------------------------------------------------------


def _ref(supplier: Supplier | None) -> schemas.SupplierRef | None:
    return schemas.SupplierRef(id=supplier.id, name=supplier.name) if supplier else None


def item_rows(db: Session, restaurant_id: str | None, today: date | None = None) -> list[schemas.ItemRow]:
    today = today or date.today()
    facts = gather(db, restaurant_id, today)
    suppliers = {s.id: s for s in db.query(Supplier).filter(Supplier.restaurant_id == restaurant_id)}
    open_by_item = _open_questions_by_item(db, restaurant_id)
    rows = [
        _item_row(item, facts.get(item.id), suppliers, open_by_item.get(item.id, 0), today)
        for item in db.query(PriceItem).filter(PriceItem.restaurant_id == restaurant_id).order_by(PriceItem.name)
    ]
    # Most at stake first: items with a change, then the rest by name.
    rows.sort(key=lambda r: (-(abs(r.change_8w or 0)), r.name))
    return rows


def _open_questions_by_item(db: Session, restaurant_id: str | None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for q in db.query(AgentQuestion).filter(AgentQuestion.restaurant_id == restaurant_id, AgentQuestion.status == OPEN):
        key = q.proposed_item_id or q.line.item_id
        if key:
            counts[key] = counts.get(key, 0) + 1
    return counts


def _item_row(item: PriceItem, facts: ItemFacts | None, suppliers: dict[str, Supplier], open_count: int,
              today: date) -> schemas.ItemRow:
    latest = facts.latest if facts else None
    change = None
    if facts and latest:
        same = sorted((p for p in facts.priced if p.supplier_id == latest.supplier_id), key=lambda p: p.day)
        if len(same) >= 2 and same[0].day < latest.day:
            delta = per_pack(item, latest.per_unit) - per_pack(item, same[0].per_unit)
            change = round(delta, 2) if abs(delta) >= 0.01 else None
    if item.hidden_until and item.hidden_until > today:
        state = "hidden"
    elif open_count and (facts is None or len(facts.dates) < 2):
        state = "awaiting"
    elif facts is None or len(facts.dates) < 2:
        state = "insufficient"
    else:
        state = "ok"
    return schemas.ItemRow(
        id=item.id, name=item.name, category=item.category, pack_size=item.pack_size, pack_unit=item.pack_unit,
        unit=item.unit, state=state,
        last_price=per_pack(item, latest.per_unit) if latest else None,
        last_supplier=_ref(suppliers.get(latest.supplier_id)) if latest else None,
        last_date=latest.day if latest else None, change_8w=change, hidden_until=item.hidden_until,
        questions_open=open_count,
    )


def item_detail(db: Session, item: PriceItem, today: date | None = None) -> schemas.ItemDetail:
    today = today or date.today()
    facts = gather(db, item.restaurant_id, today, item_id=item.id).get(item.id)
    suppliers = {s.id: s for s in db.query(Supplier).filter(Supplier.restaurant_id == item.restaurant_id)}
    open_count = _open_questions_by_item(db, item.restaurant_id).get(item.id, 0)
    row = _item_row(item, facts, suppliers, open_count, today)
    digest = latest_digest(db, item.restaurant_id, today)
    card = next((schemas.Card(**c) for c in digest.cards if c["item_id"] == item.id), None)
    note = None
    if row.state == "insufficient":
        note = "Not enough matched invoices to compare yet."
    elif row.state == "awaiting":
        note = f"Answer {open_count} question{'s' if open_count != 1 else ''} to compare."
    elif row.state == "hidden":
        note = f"Hidden from the digest until {short_date(item.hidden_until)}."

    # Eight weeks ending with the latest invoice, one line per supplier, latest price in each week.
    # The weeks are seven-day windows ending on the latest invoice's day, not calendar weeks, so a
    # supplier that delivers weekly lands in every window whatever weekday the window closes on.
    series = []
    if facts:
        latest = facts.latest
        weeks = [latest.day - timedelta(days=6 + 7 * i) for i in reversed(range(WINDOW_WEEKS))]
        for sid in sorted({p.supplier_id for p in facts.priced}, key=lambda s: suppliers[s].name if s in suppliers else s):
            points = []
            for w in weeks:
                in_week = [p for p in facts.priced if p.supplier_id == sid and w <= p.day < w + timedelta(days=7)]
                points.append(schemas.SeriesPoint(
                    week_start=w, price=per_pack(item, max(in_week, key=lambda p: p.day).per_unit) if in_week else None))
            series.append(schemas.Series(supplier=_ref(suppliers.get(sid)) or schemas.SupplierRef(id=sid, name="Supplier"),
                                         current=bool(latest and latest.supplier_id == sid), points=points))

    lines = (
        db.query(InvoiceLine).join(Invoice, Invoice.id == InvoiceLine.invoice_id)
        .filter(InvoiceLine.item_id == item.id).order_by(Invoice.invoice_date.desc(), InvoiceLine.line_no).limit(40).all()
    )
    line_rows = [
        schemas.LineRow(
            id=l.id, invoice_id=l.invoice_id, invoice_date=l.invoice.invoice_date,
            supplier=_ref(suppliers.get(l.supplier_id)) or schemas.SupplierRef(id=l.supplier_id, name="Supplier"),
            description=l.description, qty=l.qty, pack_text=l.pack_text, unit_price=l.unit_price,
            per_unit=round(l.unit_price / l.pack_size, 4) if l.pack_size else None, pack_size=l.pack_size,
            extended=l.extended, delivered_qty=l.delivered_qty, credited=l.credited,
        )
        for l in lines
    ]
    moved = suppliers.get(item.moved_to_supplier_id) if item.moved_to_supplier_id else None
    from app.prices import stock as stock_module

    from app.platform import flags
    from app.prices import agreed as agreed_module

    agreed_out = None
    used_in: list[schemas.RecipeRef] = []
    if flags.enabled(db, item.restaurant_id, flags.V2_DOCUMENTS):
        current = agreed_module.current(db, item, today)
        if current is not None:
            agreed_out = schemas.AgreedOut(
                id=current.id, supplier=_ref(suppliers.get(current.supplier_id)) or schemas.SupplierRef(id=current.supplier_id, name="Supplier"),
                unit_price=current.unit_price, base_unit=current.base_unit, effective_from=current.effective_from,
                effective_to=current.effective_to, source_document_id=current.source_document_id,
            )
        from app.recipes.models import Recipe, RecipeLine

        used_in = [
            schemas.RecipeRef(id=r.id, name=r.name, status=r.status)
            for r in db.query(Recipe).join(RecipeLine, RecipeLine.recipe_id == Recipe.id)
            .filter(Recipe.restaurant_id == item.restaurant_id, RecipeLine.item_id == item.id).distinct().order_by(Recipe.name)
        ]
    return schemas.ItemDetail(
        **row.model_dump(), pack_sentence=f"Reading this as {unit_words(item.pack_size, item.unit)} per {item.pack_unit}.",
        card=card, note=note, series=series, lines=line_rows, moved_to=_ref(moved),
        stock=stock_module.link_out(db, item), inventory_link=item.inventory_link or "auto",
        agreed=agreed_out, used_in=used_in,
    )


# --- drafts ---------------------------------------------------------------------------


def draft_for(db: Session, item: PriceItem, supplier: Supplier, kind: str | None, today: date | None = None) -> OutreachDraft:
    """Three sentences and the lines they rest on. Nothing is sent from here."""
    today = today or date.today()
    facts = gather(db, item.restaurant_id, today, item_id=item.id).get(item.id)
    priced = sorted((p for p in (facts.priced if facts else []) if p.supplier_id == supplier.id), key=lambda p: -p.day.toordinal())
    pack_word = item.pack_unit if item.pack_size > 1 else item.unit
    rep = db.query(SupplierRep).filter(SupplierRep.restaurant_id == item.restaurant_id, SupplierRep.supplier_id == supplier.id).first()
    greeting = f"Hi {rep.name.split()[0]}," if rep and rep.name else "Hello,"
    short = [p for p in priced if p.line.delivered_qty is not None and p.line.delivered_qty < p.line.qty
             and (p.line.qty - p.line.delivered_qty) * p.line.unit_price > p.line.credited]
    if kind is None:
        kind = "credit_claim" if short else "price_challenge"
    lines: list[Priced] = []
    if kind in ("credit_claim", "short_delivery") and short:
        p = short[0]
        owed = round((p.line.qty - p.line.delivered_qty) * p.line.unit_price - p.line.credited, 2)
        subject = f"Credit for {item.name.split(',')[0].lower()}, {short_date(p.day)} invoice"
        body = (f"Your {short_date(p.day)} invoice bills {p.line.qty:g} {pack_word}{'s' if p.line.qty != 1 else ''} of "
                f"{item.name} at {money(p.line.unit_price)} each. Our delivery sheet shows {p.line.delivered_qty:g} received. "
                f"Please issue a credit of {money(owed)} or confirm the rest shipped.")
        lines = [p]
    else:
        kind = "price_challenge"
        latest = priced[0] if priced else None
        earliest = priced[-1] if len(priced) > 1 else None
        others = [p for p in (facts.priced if facts else []) if p.supplier_id != supplier.id
                  and p.day > today - timedelta(days=COMPARE_DAYS)]
        other = min(others, key=lambda p: p.per_unit) if others else None
        subject = f"{item.name}: price on the {short_date(latest.day) if latest else 'latest'} invoice"
        first = (f"Your {short_date(latest.day)} invoice bills {item.name} at {money(per_pack(item, latest.per_unit))} a {pack_word}."
                 if latest else f"Your latest invoice bills {item.name}.")
        if other is not None and latest and other.per_unit < latest.per_unit:
            second = f"Another supplier charged {money(per_pack(item, other.per_unit))} for the same {pack_word} the same month."
        elif earliest is not None and latest and latest.per_unit > earliest.per_unit:
            second = f"On {short_date(earliest.day)} it was {money(per_pack(item, earliest.per_unit))}."
        else:
            second = "That is more than we were paying."
        body = f"{first} {second} Can you match it on the next order, or tell me what is driving the difference?"
        lines = [p for p in (latest, earliest) if p is not None]
    draft = OutreachDraft(
        restaurant_id=item.restaurant_id, supplier_id=supplier.id, item_id=item.id, kind=kind,
        to_name=rep.name if rep else None, to_email=rep.email if rep else None,
        subject=subject[:200], body=f"{greeting}\n\n{body}\n\nThanks", line_ids=[p.line.id for p in lines], status=DRAFT,
    )
    db.add(draft)
    db.flush()
    return draft


def draft_out(db: Session, draft: OutreachDraft) -> schemas.DraftOut:
    supplier = db.get(Supplier, draft.supplier_id)
    rep = db.query(SupplierRep).filter(SupplierRep.restaurant_id == draft.restaurant_id, SupplierRep.supplier_id == draft.supplier_id).first()
    lines = []
    for line_id in draft.line_ids:
        line = db.get(InvoiceLine, line_id)
        if line is None:
            continue
        lines.append(schemas.DraftLine(
            line_id=line.id,
            label=f"{short_date(line.invoice.invoice_date)}, {supplier.name if supplier else 'supplier'}. "
                  f"{line.description}, {line.qty:g} at {money(line.unit_price)}",
        ))
    return schemas.DraftOut(
        id=draft.id, status=draft.status, kind=draft.kind,
        supplier=schemas.SupplierRef(id=supplier.id, name=supplier.name) if supplier else schemas.SupplierRef(id=draft.supplier_id, name="Supplier"),
        item_id=draft.item_id, item_name=draft.item.name, to_name=draft.to_name, to_email=draft.to_email,
        remembered_from=rep.remembered_on if rep and (rep.name or rep.email) and draft.status == DRAFT and (draft.to_name or draft.to_email) else None,
        subject=draft.subject, body=draft.body, lines=lines, created_at=draft.created_at, sent_at=draft.sent_at,
    )


def remember_rep(db: Session, draft: OutreachDraft, today: date | None = None) -> None:
    """After the first message, the rep's name and email stick to the supplier."""
    if not (draft.to_name or draft.to_email):
        return
    rep = db.query(SupplierRep).filter(SupplierRep.restaurant_id == draft.restaurant_id, SupplierRep.supplier_id == draft.supplier_id).first()
    if rep is None:
        rep = SupplierRep(restaurant_id=draft.restaurant_id, supplier_id=draft.supplier_id, remembered_on=today or date.today())
        db.add(rep)
    rep.name, rep.email = draft.to_name, draft.to_email
    db.flush()


# --- the thread -----------------------------------------------------------------------


def message_out(db: Session, message: ThreadMessage) -> schemas.MessageOut:
    from app.prices.routes import question_out  # noqa: PLC0415  (shared presenter lives with the routes)

    draft = message.draft
    supplier = db.get(Supplier, draft.supplier_id) if draft else None
    return schemas.MessageOut(
        id=message.id, role=message.role, text=message.text, sources=[schemas.Source_(**s) for s in message.sources],
        question=question_out(db, message.question) if message.question else None,
        draft=schemas.DraftSummary(
            id=draft.id, status=draft.status, subject=draft.subject, to_name=draft.to_name,
            supplier=schemas.SupplierRef(id=draft.supplier_id, name=supplier.name if supplier else "Supplier"),
        ) if draft else None,
        status=message.status, created_at=message.created_at,
    )


MEETING_WORDS = ("talking points", "meeting", "manager notes")


def is_meeting_prep(text: str) -> bool:
    return any(word in text.lower() for word in MEETING_WORDS)


def settle_pending(db: Session, restaurant_id: str | None) -> None:
    """A pending answer whose run has finished without one is marked failed, so it doesn't wait forever."""
    from app.agents.models import AgentRun

    from app.agents.providers import NO_MODEL

    pending = db.query(ThreadMessage).filter(ThreadMessage.restaurant_id == restaurant_id, ThreadMessage.status == "pending")
    changed = False
    for message in pending:
        run = db.get(AgentRun, message.run_id) if message.run_id else None
        if run is None or run.status in ("succeeded", "failed", "cancelled"):
            message.status = "failed"
            # A site with no model says so, so the owner knows what to set rather than how to rephrase.
            no_model = run is not None and run.status == "failed" and (run.error or "") == NO_MODEL
            message.text = NO_MODEL if no_model else "Couldn't answer that. Ask about an item, a supplier, or an invoice by name."
            changed = True
    if changed:
        db.flush()
