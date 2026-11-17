"""What was said, as a typed command. Pure: text and the restaurant's own lists go in, a command comes out.

    "walk-in: mozzarella sixteen pounds, basil three bunches"   -> count, two lines, area Walk-in
    "waste two pounds of chicken, dropped"                      -> waste, one line, reason dropped
    "sysco delivery, everything arrived except one case of romaine" -> receive, supplier Sysco, one exception
    "what did I pay for tomatoes last month"                    -> ask
    "show me what's low"                                        -> navigate, inventory

No model is involved, so it runs in a millisecond and costs nothing. Anything it can't place it says so:
an item it doesn't recognise comes back in `unmatched` with the closest names, for a person to pick; the
pick is remembered as that restaurant's spoken alias. Nothing here writes anything.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

# --- numbers ------------------------------------------------------------------------------------

_ONES = {"zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
         "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_FRACTIONS = {"half": 0.5, "quarter": 0.25, "quarters": 0.25, "third": 1 / 3, "thirds": 1 / 3}
_NUMBER_WORDS = set(_ONES) | set(_TENS) | set(_FRACTIONS) | {"hundred", "thousand", "and", "a", "an", "point", "dozen", "couple", "no", "none"}


def _is_digit(token: str) -> bool:
    return bool(re.fullmatch(r"\d+(\.\d+)?|\d+/\d+|\.\d+", token))


def read_number(tokens: list[str], start: int) -> tuple[float, int] | None:
    """A number phrase beginning at `start`: its value and how many tokens it took. None if there isn't one.
    Reads "16", "16.5", "1/2", "sixteen", "twenty five", "a hundred and ten", "two and a half", "a half",
    "three quarters", "one point five", "two dozen", "a couple"."""
    i, n = start, len(tokens)
    if i >= n:
        return None
    token = tokens[i]
    value: float | None = None
    if _is_digit(token):
        if "/" in token:
            top, bottom = token.split("/")
            if float(bottom) == 0:
                return None
            value = float(top) / float(bottom)
        else:
            value = float(token)
        i += 1
    elif token in ("no", "none", "zero"):
        return 0.0, 1
    elif token in ("a", "an"):
        # "a half", "a dozen", "a couple", "a hundred": the article is a one only before those.
        if i + 1 < n and tokens[i + 1] in ("half", "quarter", "third", "dozen", "couple", "hundred", "thousand"):
            value = 1.0
            i += 1
            if tokens[i] in _FRACTIONS:
                return _FRACTIONS[tokens[i]], i + 1 - start
            if tokens[i] == "couple":
                return 2.0, i + 1 - start
        else:
            return None
    elif token == "couple":
        return 2.0, 1
    elif token == "half":
        return 0.5, 1
    elif token in _ONES or token in _TENS:
        value = 0.0
        while i < n and (tokens[i] in _ONES or tokens[i] in _TENS):
            value += _ONES.get(tokens[i], _TENS.get(tokens[i], 0))
            i += 1
    else:
        return None
    # hundred / thousand, then anything below it
    while i < n and tokens[i] in ("hundred", "thousand"):
        value = (value or 1.0) * (100 if tokens[i] == "hundred" else 1000)
        i += 1
        if i < n and tokens[i] == "and" and i + 1 < n and (tokens[i + 1] in _ONES or tokens[i + 1] in _TENS):
            i += 1
        while i < n and (tokens[i] in _ONES or tokens[i] in _TENS):
            value += _ONES.get(tokens[i], _TENS.get(tokens[i], 0))
            i += 1
    # "point five"
    if i + 1 < n and tokens[i] == "point" and (tokens[i + 1] in _ONES or tokens[i + 1].isdigit()):
        i += 1
        digits = ""
        while i < n and (tokens[i] in _ONES and _ONES[tokens[i]] < 10 or tokens[i].isdigit()):
            digits += str(_ONES[tokens[i]]) if tokens[i] in _ONES else tokens[i]
            i += 1
        value += float(f"0.{digits}")
    # "and a half", "and three quarters", "three quarters"
    if i < n and tokens[i] in _FRACTIONS and value is not None and 0 < value < 10 and value == int(value) and tokens[i] in ("quarters", "thirds"):
        return value * _FRACTIONS[tokens[i]], i + 1 - start
    if i + 1 < n and tokens[i] == "and":
        j = i + 1
        if tokens[j] in ("a", "an") and j + 1 < n and tokens[j + 1] in _FRACTIONS:
            return value + _FRACTIONS[tokens[j + 1]], j + 2 - start
        if tokens[j] in _FRACTIONS:
            return value + _FRACTIONS[tokens[j]], j + 1 - start
        part = read_number(tokens, j)
        if part and j + part[1] < n and tokens[j + part[1]] in ("quarters", "thirds"):
            return value + part[0] * _FRACTIONS[tokens[j + part[1]]], j + part[1] + 1 - start
    if i < n and tokens[i] == "dozen":
        return value * 12, i + 1 - start
    return value, i - start


# --- units --------------------------------------------------------------------------------------

#: spoken word -> the unit as the app spells it
UNIT_WORDS = {
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb", "oz": "oz", "ounce": "oz", "ounces": "oz", "kg": "kg", "kilo": "kg", "kilos": "kg",
    "kilogram": "kg", "kilograms": "kg", "g": "g", "gram": "g", "grams": "g",
    "l": "L", "liter": "L", "liters": "L", "litre": "L", "litres": "L", "ml": "ml", "gallon": "gal", "gallons": "gal", "gal": "gal",
    "quart": "qt", "quarts": "qt", "qt": "qt",
    "case": "case", "cases": "case", "bag": "bag", "bags": "bag", "box": "box", "boxes": "box", "pack": "pack", "packs": "pack",
    "bunch": "bunch", "bunches": "bunch", "head": "head", "heads": "head", "loaf": "loaf", "loaves": "loaf", "bottle": "bottle",
    "bottles": "bottle", "can": "can", "cans": "can", "jar": "jar", "jars": "jar", "tray": "tray", "trays": "tray", "each": "each",
    "wheel": "wheel", "wheels": "wheel", "tub": "tub", "tubs": "tub",
}
#: To pounds, and to litres: units that convert among themselves.
_WEIGHT = {"lb": 1.0, "oz": 1 / 16, "kg": 2.20462, "g": 0.00220462}
_VOLUME = {"L": 1.0, "ml": 0.001, "gal": 3.78541, "qt": 0.946353}


def to_item_unit(quantity: float, said_unit: str | None, item: dict) -> tuple[float, bool]:
    """`quantity` in the item's own unit, and whether the conversion is sure. A pack word ("case") multiplies by
    the item's pack size; weights and volumes convert; anything else is taken as the item's unit and flagged."""
    unit = item["unit"]
    if said_unit is None or said_unit == unit:
        return quantity, True
    if item.get("pack_size") and item.get("pack_unit") and said_unit == item["pack_unit"]:
        return round(quantity * item["pack_size"], 4), True
    for table in (_WEIGHT, _VOLUME):
        if said_unit in table and unit in table:
            return round(quantity * table[said_unit] / table[unit], 4), True
    own = (item.get("conversions") or {}).get(said_unit.lower())  # this restaurant's "1 lb = 8 bunches"
    if own:
        return round(quantity * own, 4), True
    return quantity, False


# --- items --------------------------------------------------------------------------------------

_FILLER = {"of", "the", "some", "about", "around", "roughly", "just", "we", "have", "has", "got", "there", "is", "are", "i", "its", "it's", "left",
           "in", "on", "at", "for", "to", "count", "counted", "now", "only", "fresh", "more", "s"}


def _singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("oes") and len(word) > 4:
        return word[:-2]
    if word.endswith("ves") and len(word) > 4:
        return word[:-3] + "f"
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def _key(text: str) -> str:
    return " ".join(_singular(w) for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in _FILLER)


def match_item(phrase: str, items: list[dict], aliases: dict[str, str]) -> tuple[dict | None, list[dict]]:
    """The item a phrase names, or None with the closest candidates when it is unclear."""
    key = _key(phrase)
    if not key:
        return None, []
    by_id = {i["id"]: i for i in items}
    # An item's own name always means that item: an alias can add a way of saying it, never take its name away.
    for item in items:
        if _key(item["name"]) == key:
            return item, []
    for said, item_id in aliases.items():
        if _key(said) == key and item_id in by_id:
            return by_id[item_id], []
    scored = []
    for item in items:
        name = _key(item["name"])
        words, said = set(name.split()), set(key.split())
        score = difflib.SequenceMatcher(None, key, name).ratio()
        if said and said <= words:
            score = max(score, 0.86 + 0.1 * len(said) / len(words))   # "chicken" for "Chicken Breast"
        elif words and words <= said:
            score = max(score, 0.9)                                    # "fresh mozzarella cheese" for "Mozzarella"
        scored.append((score, item))
    scored.sort(key=lambda pair: -pair[0])
    if not scored or scored[0][0] < 0.72:
        return None, [item for score, item in scored[:3] if score >= 0.45]
    if len(scored) > 1 and scored[1][0] >= 0.72 and scored[0][0] - scored[1][0] < 0.04:
        return None, [scored[0][1], scored[1][1]]                      # two that fit as well as each other: ask
    return scored[0][1], []


# --- the command --------------------------------------------------------------------------------


@dataclass
class Line:
    inventory_item_id: str
    name: str
    quantity: float
    unit: str
    said: str
    sure: bool = True

    def as_dict(self) -> dict:
        return {"inventory_item_id": self.inventory_item_id, "name": self.name, "quantity": self.quantity, "unit": self.unit, "said": self.said, "sure": self.sure}


@dataclass
class Command:
    intent: str  # count | waste | receive | ask | navigate | unknown
    lines: list[Line] = field(default_factory=list)
    #: Phrases that named no item for sure: {"said", "quantity", "unit", "choices": [{"id", "name"}]}
    unmatched: list[dict] = field(default_factory=list)
    area: str | None = None
    reason: str | None = None
    supplier: str | None = None
    #: For receive: True when "everything arrived", with `lines` then being what did NOT (the amount short).
    all_arrived: bool = False
    question: str | None = None
    route: str | None = None

    def as_dict(self) -> dict:
        return {"intent": self.intent, "lines": [l.as_dict() for l in self.lines], "unmatched": self.unmatched, "area": self.area,
                "reason": self.reason, "supplier": self.supplier, "all_arrived": self.all_arrived, "question": self.question, "route": self.route}


_WASTE = re.compile(r"\b(waste[ds]?|wasting|threw (out|away)|throw(n|ing)? (out|away)|toss(ed)?|binned|chucked|spoil(ed|t)?|went bad|gone bad|"
                    r"dropped|spill(ed|t)?|burn(ed|t)|overcooked|comp(ed|ped)?|staff meals?|family meals?|expired|rotten|mou?ldy|wilted)\b")
_REASONS = [
    ("staff_meal", r"staff meals?|family meals?"), ("comped", r"comp(ed|ped)?\b"), ("overcooked", r"burn(ed|t)|overcooked|sent back"),
    ("dropped", r"dropped|spill(ed|t)?|fell|knocked over|tipped"), ("prep", r"trim(med|mings?)?|prep loss|peel(s|ings)?"),
    ("spoiled", r"spoil(ed|t)?|went bad|gone bad|expired|rotten|mou?ldy|wilted|out of date|off\b"),
]
_RECEIVE = re.compile(r"\b(deliver(y|ies|ed)|arrived|came in|showed up|dropped off|check(ing)? in|receiv(e|ed|ing))\b")
_ASK = re.compile(r"^(what|how|which|who|when|where|why|did|do|does|is|are|can|could|should|tell me|give me|compare)\b")
_NAVIGATE = re.compile(r"^(show( me)?|go to|open|take me to|pull up|bring up|see)\b")
_ROUTES = [
    ("inventory", r"low|running out|stock|inventory|on hand"), ("receive", r"deliver(y|ies)|coming in|arriving|on order"),
    ("count", r"count sheet|count\b"), ("variance", r"waste|variance|shrink|missing"), ("digest", r"digest|price(s)?|savings this week"),
    ("insights", r"savings|costs?|prime cost|spend"), ("autopilot", r"autopilot|order days|pars?\b"), ("approvals", r"recommend|approv|inbox"),
    ("staff", r"schedule|shifts?|rota|staff"), ("menu", r"menu|dishes"), ("invoices", r"invoices?"), ("suppliers", r"suppliers?"),
    ("profit", r"profit|revenue|sales"), ("overview", r"today|home|dashboard"),
]
# A full stop pauses; the point in "7.5" does not.
#: More entries than anyone says in one breath; a longer transcript is cut here.
MAX_ENTRIES = 60
_PAUSE = re.compile(r"[,;:]|\.(?!\d)|\band then\b|\bthen\b")
_JOINERS = {"and", "plus", "also", "with"}


def _tokens(text: str) -> list[str]:
    return re.findall(r"\d+/\d+|\d*\.\d+|\d+|[a-z']+", text.lower().replace("-", " "))


def _entries(text: str) -> list[tuple[str, float | None, str | None, str]]:
    """Every (item phrase, amount, unit, words as said) in a stretch of speech.

    Speech arrives without commas, so the boundaries come from the shape of what is said: either the item
    then its amount ("mozzarella sixteen pounds basil three bunches") or the amount then its item ("sixteen
    pounds of mozzarella three bunches of basil"). An entry ends when the next one begins. "and" inside a
    number ("two and a half", "a hundred and ten") belongs to the number, because the number is read first.
    """
    out: list[tuple[str, float | None, str | None, str]] = []
    for piece in _PAUSE.split(text):
        tokens = _tokens(piece)
        words: list[str] = []
        said: list[str] = []
        amount: float | None = None
        unit: str | None = None
        item_first = False

        def flush() -> None:
            nonlocal words, said, amount, unit, item_first
            if words or amount is not None:
                out.append((" ".join(words), amount, unit, " ".join(said)))
            words, said, amount, unit, item_first = [], [], None, None, False

        i = 0
        while i < len(tokens):
            token = tokens[i]
            number = read_number(tokens, i)
            if number is not None and number[1] > 0:
                if amount is not None:
                    flush()  # a second amount: the next entry has begun
                item_first = bool(_key(" ".join(words)))
                amount = number[0]
                said += tokens[i:i + number[1]]
                i += number[1]
                # The unit follows the amount, sometimes after "a" or "of a": "half a pound", "three quarters of a pound".
                j = i
                while j < len(tokens) and j - i < 2 and tokens[j] in ("of", "a", "an"):
                    j += 1
                if j < len(tokens) and tokens[j] in UNIT_WORDS:
                    unit = UNIT_WORDS[tokens[j]]
                    said += tokens[i:j + 1]
                    i = j + 1
                continue
            if token in _JOINERS:
                flush()
            elif amount is not None and item_first and token not in _FILLER:
                flush()  # item, amount, and now another word: the next item
                words.append(token)
                said.append(token)
            else:
                words.append(token)
                said.append(token)
            i += 1
        flush()
    return out


def _lines(text: str, items: list[dict], aliases: dict[str, str], default_amount: float | None = None) -> tuple[list[Line], list[dict]]:
    lines, unmatched = [], []
    for phrase, amount, unit, said in _entries(text)[:MAX_ENTRIES]:
        if amount is None:
            amount = default_amount
        if not _key(phrase) or amount is None:
            continue  # an amount with no item, or an item with no amount, is chatter, not an entry
        item, choices = match_item(phrase, items, aliases)
        if item is None:
            unmatched.append({"said": phrase, "quantity": amount, "unit": unit, "choices": [{"id": c["id"], "name": c["name"]} for c in choices]})
            continue
        quantity, sure = to_item_unit(amount, unit, item)
        lines.append(Line(inventory_item_id=item["id"], name=item["name"], quantity=quantity, unit=item["unit"], said=said, sure=sure))
    return lines, unmatched


def _strip(text: str, pattern: re.Pattern | str) -> str:
    return re.sub(pattern, " ", text)


_NOT_A_NAME = {"the", "los", "las", "les", "san", "new", "old", "big", "best", "fresh", "farm", "farms", "foods", "food", "company"}


def _supplier_said(plain: str, suppliers: list[str]) -> str | None:
    """The supplier named in what was said: its whole name, or its first word when that word is a real name
    (not "The") and no other supplier starts with it. Two that fit is none: the person is asked."""
    firsts: dict[str, int] = {}
    for name in suppliers:
        firsts[name.lower().split()[0]] = firsts.get(name.lower().split()[0], 0) + 1
    found = []
    for name in suppliers:
        whole, first = name.lower(), name.lower().split()[0]
        if re.search(rf"\b{re.escape(whole)}\b", plain):
            return name
        if len(first) >= 4 and first not in _NOT_A_NAME and firsts[first] == 1 and re.search(rf"\b{re.escape(first)}\b", plain):
            found.append(name)
    return found[0] if len(found) == 1 else None


def parse(transcript: str, items: list[dict], aliases: dict[str, str] | None = None, areas: list[str] | None = None,
          suppliers: list[str] | None = None) -> Command:
    """Turn one utterance into a command. `items` are {"id", "name", "unit", "pack_size"?, "pack_unit"?}."""
    aliases, areas, suppliers = aliases or {}, areas or [], suppliers or []
    raw = " ".join((transcript or "").strip().split())
    text = raw.lower().replace("’", "'")
    plain = re.sub(r"[.!?]+$", "", text).strip()
    if not plain:
        return Command(intent="unknown")

    if _NAVIGATE.search(plain) and not re.search(r"\d", plain):
        for route, pattern in _ROUTES:
            if re.search(pattern, plain):
                return Command(intent="navigate", route=route)
    if raw.endswith("?") or _ASK.search(plain):
        return Command(intent="ask", question=raw)

    supplier = _supplier_said(plain, suppliers)
    if _RECEIVE.search(plain) and (supplier or re.search(r"\b(everything|all (of it|here|arrived)|deliver(y|ed))\b", plain)):
        all_here = bool(re.search(r"\b(everything|all of it|all here|all arrived|all there|complete|in full)\b", plain))
        tail = re.split(r"\b(except|but|minus|short|missing)\b", plain, maxsplit=1)
        exceptions = "".join(tail[1:]) if len(tail) > 1 else ("" if all_here else plain)
        exceptions = _strip(exceptions, r"\b(except|but|minus|short|missing|they|were|was|we|are|on|for|by)\b")
        if supplier:
            exceptions = _strip(exceptions, rf"\b({re.escape(supplier.lower())}|{re.escape(supplier.lower().split()[0])})\b")
        exceptions = _strip(_strip(exceptions, _RECEIVE), r"\b(everything|all|arrived|here|order)\b")
        lines, unmatched = _lines(exceptions, items, aliases, default_amount=1 if len(tail) > 1 else None)
        return Command(intent="receive", lines=lines, unmatched=unmatched, supplier=supplier, all_arrived=all_here or len(tail) > 1)

    if _WASTE.search(plain):
        reason = next((key for key, pattern in _REASONS if re.search(pattern, plain)), "other")
        body = _strip(plain, _WASTE)
        body = _strip(body, r"\b(because|it|they|was|were|got|that|which|sent back|out of date|knocked over|tipped|fell|prep loss|trim(med|mings?)?|off|bad)\b")
        lines, unmatched = _lines(body, items, aliases)
        return Command(intent="waste", lines=lines, unmatched=unmatched, reason=reason)

    area = None
    body = plain
    for name in sorted(areas, key=len, reverse=True):
        spoken = name.lower().replace("-", " ")
        found = re.search(rf"\b(in (the )?)?{re.escape(spoken)}\b:?", body.replace("-", " "))
        if found:
            area = name
            body = body.replace("-", " ")
            body = (body[:found.start()] + " " + body[found.end():]).strip(" :,")
            break
    lines, unmatched = _lines(body, items, aliases)
    if lines or unmatched:
        return Command(intent="count", lines=lines, unmatched=unmatched, area=area)
    return Command(intent="unknown", question=raw)


def read_back(command: Command) -> str:
    """The command said back in a sentence, for the person to hear before anything is saved."""
    def amount(line: Line) -> str:
        return f"{line.quantity:g} {line.unit} of {line.name}"

    listed = ", ".join(amount(l) for l in command.lines)
    extra = f" I didn't catch {', '.join(u['said'] for u in command.unmatched)}." if command.unmatched else ""
    if command.intent == "count":
        where = f" in {command.area}" if command.area else ""
        return (f"Count{where}: {listed}." if listed else "I didn't catch any items.") + extra
    if command.intent == "waste":
        from app.stock.engine import WASTE_REASONS

        return (f"Waste: {listed}. Reason: {WASTE_REASONS.get(command.reason or 'other', 'Other').lower()}." if listed else "I didn't catch what was wasted.") + extra
    if command.intent == "receive":
        who = command.supplier or "the supplier"
        if command.all_arrived and not command.lines:
            return f"{who} delivery: everything arrived." + extra
        if command.all_arrived:
            return f"{who} delivery: everything arrived except {listed}." + extra
        return (f"{who} delivery: {listed} arrived." if listed else f"{who} delivery. Tell me what arrived.") + extra
    if command.intent == "ask":
        return f"Asking: {command.question}"
    if command.intent == "navigate":
        return f"Opening {command.route}."
    return "I didn't catch that. Try: mozzarella sixteen pounds, or waste two pounds of chicken, dropped."
