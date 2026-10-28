"""Structured reading of a document: a schema to fill, by Claude, by an OpenAI-compatible model, or by rule.

The invoice reader (app.prices.extract) came first and keeps its own schema and checks. This
is the general form of it: `read(media_type, data, schema, instruction)` returns the fields
the model filled, each with a confidence where the schema asks for one; `classify` says what
kind of document it is, with a confidence. With no model key, `read` is rule-based from the
PDF's text and says so by returning `reader="text"`.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from datetime import date, datetime
from typing import Any

import httpx

from app.config import settings
from app.prices import extract

logger = logging.getLogger(__name__)

DOC_TYPES = ("invoice", "price_list", "recipe", "license", "permit", "inspection", "warranty", "other")

CLASSIFY_TOOL = {
    "name": "classify_document",
    "description": "Say what kind of document this is for a restaurant's back office.",
    "input_schema": {
        "type": "object",
        "properties": {
            "doc_type": {"type": "string", "enum": list(DOC_TYPES)},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "title": {"type": ["string", "null"], "description": "A short title: the supplier and what it is, or the permit's name."},
        },
        "required": ["doc_type", "confidence"],
    },
}

#: Words that give a document's kind away when there is no model to ask. Checked in order.
RULES = [
    ("price_list", ("price list", "pricelist", "price sheet", "quote", "quotation", "prices effective", "effective date")),
    ("inspection", ("inspection report", "health inspection", "inspector", "violations", "food safety inspection")),
    ("permit", ("permit",)),
    ("license", ("license", "licence", "liquor license")),
    ("warranty", ("warranty", "warranted", "coverage period")),
    ("recipe", ("recipe", "ingredients", "yield", "method", "prep list", "portions")),
    ("invoice", ("invoice", "bill to", "remit to", "amount due", "invoice #", "invoice no")),
]


def text_of(media_type: str, data: bytes) -> str:
    if media_type == "application/pdf":
        return extract.pdf_text(data)
    if media_type.startswith("text/"):
        return data.decode("utf-8", "replace")
    return ""


def classify(media_type: str, data: bytes) -> tuple[str, float, str | None]:
    """(doc_type, confidence, title). Under 0.9 the reviewer picks the type."""
    reader = extract.available()
    if reader in ("claude", "openai"):
        try:
            out = _call_model(media_type, data, CLASSIFY_TOOL, "Classify this document. Choose the single best doc_type and say how sure you are.")
            if out and out.get("doc_type") in DOC_TYPES:
                return out["doc_type"], float(out.get("confidence") or 0), out.get("title")
        except ReadError as err:
            logger.info("classification by model failed, falling back to rules: %s", err)
    text = text_of(media_type, data).lower()
    if not text.strip():
        return "other", 0.3, None
    for doc_type, words in RULES:
        if any(w in text for w in words):
            strong = doc_type != "invoice" or "invoice" in text
            return doc_type, 0.92 if strong else 0.8, None
    return "other", 0.5, None


class ReadError(RuntimeError):
    """The document couldn't be read; the message is for the person."""


def read(media_type: str, data: bytes, schema: dict, instruction: str, text_rule=None) -> tuple[dict, str]:
    """The schema filled from the document, and which reader did it ("claude", "openai", "text")."""
    reader = extract.available()
    if reader in ("claude", "openai"):
        out = _call_model(media_type, data, schema, instruction)
        if out is None:
            raise ReadError("The reader returned nothing for this document.")
        return out, reader
    if text_rule is None:
        raise ReadError("This site has no model connected, and this kind of document can't be read by rule.")
    text = text_of(media_type, data)
    if not text.strip():
        raise ReadError("That file has no text in it, only a picture. A model key is needed to read photos.")
    return text_rule(text), "text"


def _call_model(media_type: str, data: bytes, schema: dict, instruction: str) -> dict | None:
    reader = extract.available()
    if reader == "claude":
        import anthropic

        block_type = "document" if media_type == "application/pdf" else "image"
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=settings.llm_timeout_seconds)
        try:
            message = client.messages.create(
                model=settings.invoice_reader_model or extract.CLAUDE_READER_MODEL, max_tokens=8000, tools=[schema],
                tool_choice={"type": "tool", "name": schema["name"]},
                messages=[{"role": "user", "content": [
                    {"type": block_type, "source": {"type": "base64", "media_type": media_type, "data": base64.b64encode(data).decode("ascii")}},
                    {"type": "text", "text": instruction},
                ]}],
            )
        except anthropic.APIError as err:
            raise ReadError(f"Couldn't reach the reader just now: {err}") from err
        for block in message.content:
            if getattr(block, "type", None) == "tool_use" and block.name == schema["name"]:
                return dict(block.input)
        return None
    # OpenAI-compatible: a PDF as its text, a photo as an image.
    if media_type == "application/pdf":
        text = extract.pdf_text(data)
        if not text.strip():
            raise ReadError("That PDF has no text in it, only a picture.")
        content: list[dict] = [{"type": "text", "text": f"{instruction}\n\nThe document's text:\n\n{text[:extract.MAX_TEXT_CHARS]}"}]
    else:
        content = [{"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"}},
                   {"type": "text", "text": instruction}]
    function = {"name": schema["name"], "description": schema["description"], "parameters": extract._function_schema(schema["input_schema"])}
    try:
        response = extract._post(
            f"{settings.llm_base_url.rstrip('/')}/chat/completions", headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json={"model": settings.invoice_reader_model or settings.llm_model, "messages": [{"role": "user", "content": content}],
                  "tools": [{"type": "function", "function": function}], "tool_choice": {"type": "function", "function": {"name": schema["name"]}}},
            timeout=settings.llm_timeout_seconds,
        )
    except httpx.HTTPError as err:
        raise ReadError(f"Couldn't reach the reader just now: {err}") from err
    if response.status_code >= 400:
        raise ReadError(f"The reader returned {response.status_code}.")
    body = response.json()
    try:
        message = body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return None
    for call in message.get("tool_calls") or []:
        function_call = call.get("function") or {}
        if function_call.get("name") == schema["name"]:
            arguments = function_call.get("arguments")
            if isinstance(arguments, dict):
                return arguments
            try:
                return json.loads(arguments or "{}")
            except json.JSONDecodeError:
                return None
    text = message.get("content")
    if isinstance(text, str) and "{" in text:
        try:
            return json.loads(text[text.index("{"):text.rindex("}") + 1])
        except (ValueError, json.JSONDecodeError):
            return None
    return None


# --- schemas and their rule-based readers -------------------------------------------------------

PRICE_LIST_TOOL = {
    "name": "record_price_list",
    "description": "Record a supplier's price list or quote exactly as printed. Copy, never correct or infer.",
    "input_schema": {
        "type": "object",
        "properties": {
            "supplier": {"type": ["string", "null"]},
            "supplier_confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "effective_date": {"type": ["string", "null"], "description": "YYYY-MM-DD, when these prices start."},
            "date_confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "lines": {"type": "array", "items": {"type": "object", "properties": {
                "raw_description": {"type": "string"}, "raw_code": {"type": ["string", "null"]},
                "pack_text": {"type": ["string", "null"]}, "unit_price": {"type": "number"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            }, "required": ["raw_description", "unit_price", "confidence"]}},
        },
        "required": ["lines", "supplier_confidence", "date_confidence"],
    },
}
PRICE_LIST_PROMPT = ("This is a supplier's price list or quote for a restaurant. Record every priced line as printed with "
                     "record_price_list: description, code, pack text and the price per pack. Give each a confidence.")

RECIPE_TOOL = {
    "name": "record_recipe",
    "description": "Record a recipe card or prep list as written: the dish, its yield, and each ingredient with quantity and unit.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": ["string", "null"]},
            "yield_qty": {"type": ["number", "null"]},
            "yield_unit": {"type": ["string", "null"], "description": "plates, portions, quarts..."},
            "lines": {"type": "array", "items": {"type": "object", "properties": {
                "ingredient_text": {"type": "string"}, "qty": {"type": ["number", "null"]}, "unit": {"type": ["string", "null"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            }, "required": ["ingredient_text", "confidence"]}},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["lines", "confidence"],
    },
}
RECIPE_PROMPT = "This is a chef's recipe card or prep list. Record it with record_recipe, one line per ingredient as written."

COMPLIANCE_TOOL = {
    "name": "record_compliance_document",
    "description": "Record a license, permit, inspection report or warranty: who issued it, what it is for, and its dates.",
    "input_schema": {
        "type": "object",
        "properties": {
            "issuer": {"type": ["string", "null"]},
            "title": {"type": ["string", "null"], "description": "The license, permit, inspection or warranty's own name."},
            "issued_on": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
            "expires_on": {"type": ["string", "null"], "description": "YYYY-MM-DD; for an inspection, when the next is due if stated."},
            "asset_name": {"type": ["string", "null"], "description": "For a warranty: the equipment it covers."},
            "vendor": {"type": ["string", "null"], "description": "For a warranty: who sold the equipment."},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["confidence"],
    },
}
COMPLIANCE_PROMPT = "This is a license, permit, inspection report or warranty for a restaurant. Record its issuer, title and dates with record_compliance_document."

_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})|(\d{1,2}/\d{1,2}/\d{4})|([A-Z][a-z]+ \d{1,2}, \d{4})")
_MONEY_LINE = re.compile(r"^(?P<desc>.+?)\s+\$?(?P<price>\d{1,5}\.\d{2})\s*$")


def _parse_date(raw: str) -> str | None:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def price_list_by_rule(text: str) -> dict:
    lines = []
    supplier = None
    effective = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if supplier is None and not any(ch.isdigit() for ch in line) and len(line) < 60 and "price" not in low:
            supplier = line
        if effective is None and ("effective" in low or "valid" in low):
            found = _DATE.search(line)
            if found:
                effective = _parse_date(found.group(0))
        m = _MONEY_LINE.match(line)
        if m and not low.startswith(("total", "subtotal", "page")):
            desc = m.group("desc").strip(" .:-")
            if any(w in desc.lower() for w in ("price list", "effective", "quote")):
                continue
            pack = None
            pm = re.search(r"\b(\d+\s*(?:x|/)\s*[\d.]+\s*[A-Za-z#]+|\d+\s*(?:CT|LB|OZ|GAL|QT|EA|CS|PK)\b|CS|CASE|EA|EACH|BAG|BG)\s*$", desc, re.I)
            if pm:
                pack = pm.group(1).strip()
                desc_wo = desc[:pm.start()].strip(" ,-")
                desc = desc_wo if desc_wo else desc
            lines.append({"raw_description": desc[:200], "raw_code": None, "pack_text": pack, "unit_price": float(m.group("price")), "confidence": 1.0})
    return {"supplier": supplier, "supplier_confidence": 0.8 if supplier else 0.3, "effective_date": effective,
            "date_confidence": 0.9 if effective else 0.3, "lines": lines}


_QTY_LINE = re.compile(r"^(?P<qty>\d+(?:\.\d+)?|\d+/\d+)\s*(?P<unit>oz|lb|lbs|g|kg|cup|cups|tbsp|tsp|each|ea|bunch|clove|cloves|slice|slices|ml|l|qt|pt|pinch)?\s+(?P<ing>.+)$", re.I)


def recipe_by_rule(text: str) -> dict:
    name = None
    yield_qty = yield_unit = None
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if name is None and not _QTY_LINE.match(line) and "yield" not in low and "ingredients" not in low and "recipe" not in low:
            name = line.strip(" :")
            continue
        if low.startswith("yield"):
            m = re.search(r"(\d+(?:\.\d+)?)\s*([A-Za-z]+)", line)
            if m:
                yield_qty, yield_unit = float(m.group(1)), m.group(2)
            continue
        m = _QTY_LINE.match(line)
        if m:
            qty = m.group("qty")
            qty = float(qty) if "/" not in qty else float(qty.split("/")[0]) / float(qty.split("/")[1])
            lines.append({"ingredient_text": m.group("ing").strip(" ,"), "qty": qty, "unit": (m.group("unit") or "each").lower(), "confidence": 1.0})
    return {"name": name, "yield_qty": yield_qty or 1, "yield_unit": yield_unit or "plate", "lines": lines, "confidence": 0.9 if lines else 0.3}


def compliance_by_rule(text: str) -> dict:
    issuer = title = issued = expires = asset = None
    for raw in text.splitlines():
        line = raw.strip()
        low = line.lower()
        if not line:
            continue
        if title is None and any(w in low for w in ("permit", "license", "licence", "inspection", "warranty")) and len(line) < 80:
            title = line
        if issuer is None and any(w in low for w in ("department", "county", "city of", "issued by", "state of", "board")):
            issuer = line.replace("Issued by", "").strip(" :")
        dates = _DATE.findall(line)
        found = [next(d for d in g if d) for g in dates] if dates else []
        if found and any(w in low for w in ("expire", "valid until", "valid through", "through", "ends", "until")):
            expires = _parse_date(found[-1]) or expires
        elif found and any(w in low for w in ("issued", "effective", "date of inspection", "inspected", "purchased", "start")):
            issued = _parse_date(found[0]) or issued
        if asset is None and any(w in low for w in ("model", "equipment", "unit:", "serial")):
            asset = line
    return {"issuer": issuer, "title": title, "issued_on": issued, "expires_on": expires, "asset_name": asset, "vendor": None,
            "confidence": 0.9 if (title and expires) else 0.6 if (title or expires) else 0.3}


def as_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
