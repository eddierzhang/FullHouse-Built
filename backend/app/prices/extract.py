"""Reading an invoice -- pasted text, a photo, a PDF -- into lines, and checking what was read.

Nothing here saves anything. The reader returns what it saw, each line with
how sure it was, and `check` says what doesn't add up. The person sees both
before the invoice is kept, and an invoice that fails a check is kept as
"needs review" and left out of prices until someone says it looks right.

Three readers, picked by what the site has (`available`):

* Claude, with an Anthropic key. The PDF or photo goes to the model with a
  fixed schema to fill: header and lines, a confidence on each. The model is
  told to copy, not correct.
* A model on the OpenAI-compatible API (LLM_API_KEY, LLM_MODEL): a photo goes
  as an image, a PDF as its text, with the same schema as a function to call.
  Gemini's free tier reads photos; a text-only model still reads PDFs.
* "text", with no model: a PDF that carries text is read by rule, the way
  pasted text is. A photo has to be typed up. The demo and the tests read the
  PDFs this app wrote itself this way (see scripts/make_fixture_invoices.py).
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import logging
import re
from datetime import date
from typing import Any

import httpx

from app.config import settings
from app.prices import schemas

logger = logging.getLogger(__name__)

#: Under this, a line or a header field counts as read with doubt.
SURE = 0.8
#: Arithmetic is checked to the cent, with a cent of rounding either way.
TOLERANCE = 0.02
#: About 12 MB of file.
MAX_BYTES = 12 * 1024 * 1024

LINE_RE = re.compile(
    r"^(?P<desc>.+?)\s{2,}(?P<qty>\d+(?:\.\d+)?)\s+(?:(?P<pack>[A-Za-z#/0-9.]{1,10})\s+)?"
    r"(?P<price>\d+(?:,\d{3})*(?:\.\d+)?)(?:\s+(?P<ext>\d+(?:,\d{3})*(?:\.\d+)?))?\s*$"
)
DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b|\b(\d{4})-(\d{2})-(\d{2})\b")
TOTAL_RE = re.compile(r"^(?:invoice\s+)?total\b[^\d]*(\d+(?:,\d{3})*(?:\.\d+)?)\s*$", re.I)
REFERENCE_RE = re.compile(r"\binvoice\s*(?:#|no\.?|number)?\s*[:#]?\s*([A-Z0-9-]{3,30})\b", re.I)
SUPPLIER_WORDS = ("foods", "farms", "dairy", "produce", "wine", "distribut", "warehouse", "supply", "provisions", "bakery")
SKIP_RE = re.compile(r"^(invoice|date|qty|quantity|description|total|subtotal|tax|ship to|sold to|bill to|terms|page|item|pack|price|amount)\b", re.I)


class ReaderError(Exception):
    """The file couldn't be read here. The message is for the person: it says what to do instead."""


#: The model Claude reads with when INVOICE_READER_MODEL doesn't name one.
CLAUDE_READER_MODEL = "claude-sonnet-4-5"


def available() -> schemas.Reader:
    """Which reader this site has. Only "claude" and "openai" read photos; the others read PDFs that carry text."""
    provider = (settings.llm_provider or "").strip().lower()
    if provider == "anthropic" and settings.anthropic_api_key:
        return "claude"
    if provider == "openai" and settings.llm_api_key and (settings.invoice_reader_model or settings.llm_model):
        return "openai"
    return "text"


def reads_photos() -> bool:
    return available() in schemas.READS_PHOTOS


# --- pasted text --------------------------------------------------------------------------


def _number(text: str) -> float:
    return float(text.replace(",", ""))


def _date_from(match: re.Match) -> date | None:
    try:
        if match.group(1):
            year = int(match.group(3))
            return date(year + 2000 if year < 100 else year, int(match.group(1)), int(match.group(2)))
        return date(int(match.group(4)), int(match.group(5)), int(match.group(6)))
    except ValueError:
        return None


def parse_text(text: str, confidence: float | None = None) -> schemas.ParseOut:
    """Invoice text as lines: "DESCRIPTION  QTY  PACK  PRICE  TOTAL". Header lines give the supplier, date and total."""
    lines, skipped = [], []
    supplier_guess, date_guess, total, reference = None, None, None, None
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        if date_guess is None and (m := DATE_RE.search(stripped)):
            date_guess = _date_from(m)
        if reference is None and (m := REFERENCE_RE.search(stripped)) and not LINE_RE.match(stripped):
            reference = m.group(1)[:60]
        if m := TOTAL_RE.match(stripped):
            total = _number(m.group(1))
            continue
        if m := LINE_RE.match(stripped):
            ext = _number(m.group("ext")) if m.group("ext") else None
            lines.append(schemas.LineIn(
                description=m.group("desc").strip(), qty=float(m.group("qty")), pack_text=m.group("pack"),
                unit_price=_number(m.group("price")), extended=ext, confidence=confidence,
            ))
            continue
        lowered = stripped.lower()
        if supplier_guess is None and any(w in lowered for w in SUPPLIER_WORDS):
            supplier_guess = stripped[:255]
        elif not SKIP_RE.match(lowered) and not DATE_RE.search(stripped):
            skipped.append(number)
    return schemas.ParseOut(lines=lines, skipped=skipped, supplier_guess=supplier_guess, date_guess=date_guess,
                            total=total, reference=reference)


# --- checks -------------------------------------------------------------------------------


def check(lines: list[schemas.LineIn], total: float | None, header_doubt: list[str] = ()) -> list[str]:
    """What doesn't add up, one sentence each. Empty when the invoice can be counted as read."""
    reasons: list[str] = []
    for number, line in enumerate(lines, start=1):
        if line.extended is not None and abs(line.qty * line.unit_price - line.extended) > TOLERANCE:
            reasons.append(
                f"Line {number}: {line.qty:g} × ${line.unit_price:,.2f} is ${line.qty * line.unit_price:,.2f}; "
                f"the invoice says ${line.extended:,.2f}."
            )
    if total is not None and lines:
        added = sum(l.extended if l.extended is not None else l.qty * l.unit_price for l in lines)
        if abs(added - total) > TOLERANCE:
            reasons.append(f"Lines add to ${added:,.2f}; the invoice says ${total:,.2f}.")
    for number, line in enumerate(lines, start=1):
        if line.confidence is not None and line.confidence < SURE:
            reasons.append(f"Line {number} was hard to read: “{line.description[:60]}”. Check it against the invoice.")
    reasons.extend(header_doubt)
    return reasons


# --- the readers --------------------------------------------------------------------------


def decode(data_base64: str) -> bytes:
    try:
        data = base64.b64decode(data_base64, validate=True)
    except (binascii.Error, ValueError) as err:
        raise ReaderError("That file didn't arrive whole. Try again.") from err
    if not data:
        raise ReaderError("That file is empty.")
    if len(data) > MAX_BYTES:
        raise ReaderError("That file is over 12 MB. A smaller photo or a one-page PDF reads fine.")
    return data


def read(media_type: str, data: bytes) -> schemas.ReadOut:
    """The invoice in `data` as lines, checked. Raises ReaderError when this site can't read it."""
    reader = available()
    if reader == "claude":
        return _read_claude(media_type, data)
    if reader == "openai":
        return _read_openai(media_type, data)
    return _read_text(media_type, data, reader)


# --- PDFs as text ---------------------------------------------------------------------------

_TJ_RE = re.compile(rb"\((?P<text>(?:\\.|[^\\)])*)\)\s*Tj")


def pdf_text(data: bytes) -> str:
    """The text of a PDF, a line per printed line; empty for a scan or an image-only PDF.

    PDFs this app wrote (uncompressed, one string per line) are read exactly, by their
    own strings. Anything else goes through pypdf, which lays each page out by position.
    """
    plain = []
    for match in _TJ_RE.finditer(data):
        raw = match.group("text")
        plain.append(re.sub(rb"\\([()\\])", rb"\1", raw).decode("latin-1"))
    if plain:
        return "\n".join(plain)
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages[:MAX_PAGES]]
    except Exception as err:  # noqa: BLE001 - a broken PDF reads as empty, and the person is told to type it
        logger.info("pdf text extraction failed: %s", err)
        return ""
    return "\n".join(pages)


#: Pages read from one PDF: an invoice is a page or two; a statement is not an invoice.
MAX_PAGES = 4


def _read_text(media_type: str, data: bytes, reader: schemas.Reader) -> schemas.ReadOut:
    """A PDF that carries text, read by rule: no model, so a photo can't be read this way."""
    if media_type != "application/pdf":
        raise ReaderError("This site reads PDFs only. Type the lines from the photo.")
    text = pdf_text(data)
    if not text.strip():
        raise ReaderError("That PDF has no text in it, only a picture. Type the lines instead.")
    parsed = parse_text(text, confidence=1.0)
    if not parsed.lines:
        raise ReaderError("Couldn't find invoice lines in that PDF. Type them instead.")
    reasons = check(parsed.lines, parsed.total)
    return schemas.ReadOut(
        supplier_guess=parsed.supplier_guess, date_guess=parsed.date_guess, reference=parsed.reference,
        total=parsed.total, lines=parsed.lines, needs_review=bool(reasons), reasons=reasons, reader=reader,
    )


# Claude: the file and a schema to fill. Copy, don't correct; say how sure.

READ_TOOL = {
    "name": "record_invoice",
    "description": "Record exactly what is printed on one supplier invoice. Copy the text as printed; never correct, "
                   "round or infer a number. For anything hard to read, keep the best reading and lower its confidence.",
    "input_schema": {
        "type": "object",
        "properties": {
            "supplier_name": {"type": ["string", "null"], "description": "The supplier, as printed in the header."},
            "supplier_confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "invoice_date": {"type": ["string", "null"], "description": "YYYY-MM-DD."},
            "date_confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reference": {"type": ["string", "null"], "description": "The invoice number, as printed."},
            "total": {"type": ["number", "null"], "description": "The invoice total as printed, after tax and credits."},
            "total_confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "lines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string", "description": "The item text exactly as printed, including codes and pack strings."},
                        "quantity": {"type": "number"},
                        "pack_text": {"type": ["string", "null"], "description": "The pack or unit column as printed, e.g. CS, 24 CT, 4/1 GAL."},
                        "unit_price": {"type": "number", "description": "Price per billed unit, as printed."},
                        "line_total": {"type": ["number", "null"], "description": "The extended amount, as printed."},
                        "delivered_quantity": {"type": ["number", "null"], "description": "Only if the invoice shows a shipped or delivered quantity different from the billed one."},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": ["description", "quantity", "unit_price", "confidence"],
                },
            },
        },
        "required": ["lines", "supplier_confidence", "date_confidence", "total_confidence"],
    },
}

READ_PROMPT = (
    "This is one supplier invoice for a restaurant. Record it with record_invoice. Copy every line item as printed, "
    "in order, with its quantity, pack text, unit price and line total. Do not add, merge, correct or round anything. "
    "Give each line and each header field a confidence between 0 and 1 for how clearly you could read it."
)


def _read_claude(media_type: str, data: bytes) -> schemas.ReadOut:
    import anthropic

    block_type = "document" if media_type == "application/pdf" else "image"
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=settings.llm_timeout_seconds)
    try:
        message = client.messages.create(
            model=settings.invoice_reader_model or CLAUDE_READER_MODEL,
            max_tokens=8000,
            tools=[READ_TOOL],
            tool_choice={"type": "tool", "name": "record_invoice"},
            messages=[{
                "role": "user",
                "content": [
                    {"type": block_type, "source": {"type": "base64", "media_type": media_type,
                                                    "data": base64.b64encode(data).decode("ascii")}},
                    {"type": "text", "text": READ_PROMPT},
                ],
            }],
        )
    except anthropic.APIError as err:
        logger.warning("invoice reader failed: %s", err)
        raise ReaderError("Couldn't reach the reader just now. Try again, or type the lines.") from err
    recorded: dict[str, Any] | None = None
    for block in message.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "record_invoice":
            recorded = dict(block.input)
    if not recorded or not recorded.get("lines"):
        raise ReaderError("Couldn't make out any invoice lines. Try a sharper photo, or type them.")
    return from_recorded(recorded)


# The OpenAI-compatible API: the same schema as a function to call. A photo goes as an image
# (Gemini, OpenAI and Groq's vision models take one as a data URL); a PDF goes as its text, so a
# text-only model reads it too.


def _function_schema(schema: Any) -> Any:
    """record_invoice's schema with each `["string", "null"]` as plain `"string"`: some function-calling
    APIs (Gemini's) refuse a list of types. A field the model can't read is left out instead."""
    if isinstance(schema, dict):
        out = {}
        for key, value in schema.items():
            if key == "type" and isinstance(value, list):
                value = next((t for t in value if t != "null"), "string")
            out[key] = _function_schema(value)
        return out
    if isinstance(schema, list):
        return [_function_schema(v) for v in schema]
    return schema


def _post(url: str, **kwargs) -> httpx.Response:
    return httpx.post(url, **kwargs)


def _read_openai(media_type: str, data: bytes) -> schemas.ReadOut:
    if media_type == "application/pdf":
        text = pdf_text(data)
        if not text.strip():
            raise ReaderError("That PDF has no text in it, only a picture. Take a photo of the invoice instead.")
        content: list[dict] = [{"type": "text", "text": f"{READ_PROMPT}\n\nThe invoice's text:\n\n{text[:MAX_TEXT_CHARS]}"}]
    else:
        content = [
            {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"}},
            {"type": "text", "text": READ_PROMPT},
        ]
    model = settings.invoice_reader_model or settings.llm_model
    function = {"name": READ_TOOL["name"], "description": READ_TOOL["description"],
                "parameters": _function_schema(READ_TOOL["input_schema"])}
    try:
        response = _post(
            f"{settings.llm_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": content}],
                "tools": [{"type": "function", "function": function}],
                "tool_choice": {"type": "function", "function": {"name": READ_TOOL["name"]}},
            },
            timeout=settings.llm_timeout_seconds,
        )
    except httpx.HTTPError as err:
        logger.warning("invoice reader failed: %s", err)
        raise ReaderError("Couldn't reach the reader just now. Try again, or type the lines.") from err
    if response.status_code >= 400:
        logger.warning("invoice reader: %s returned %s: %s", model, response.status_code, response.text[:300])
        raise ReaderError(
            f"The reader ({model}) couldn't take that file ({response.status_code}). "
            "Try a PDF, a different model in LLM_MODEL, or type the lines."
        )
    recorded = _recorded_from_chat(response.json())
    if not recorded or not recorded.get("lines"):
        raise ReaderError("Couldn't make out any invoice lines. Try a sharper photo, or type them.")
    return from_recorded(recorded, reader="openai")


#: Invoice text sent to the model, at most; a page of lines is a few thousand characters.
MAX_TEXT_CHARS = 30000


def _recorded_from_chat(body: dict) -> dict[str, Any] | None:
    """The record_invoice arguments out of a chat completion: the function call, or JSON the model wrote instead."""
    try:
        message = body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return None
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        if function.get("name") == READ_TOOL["name"]:
            arguments = function.get("arguments")
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


def from_recorded(recorded: dict[str, Any], reader: schemas.Reader = "claude") -> schemas.ReadOut:
    """A filled record_invoice schema as a ReadOut, checked."""
    lines: list[schemas.LineIn] = []
    for raw in recorded.get("lines") or []:
        try:
            qty = float(raw.get("quantity") or 0)
            price = float(raw.get("unit_price") or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0 or price < 0 or not str(raw.get("description") or "").strip():
            continue
        total = raw.get("line_total")
        delivered = raw.get("delivered_quantity")
        lines.append(schemas.LineIn(
            description=str(raw["description"]).strip()[:200], qty=qty,
            pack_text=(str(raw.get("pack_text") or "").strip()[:40] or None), unit_price=price,
            extended=round(float(total), 2) if total is not None else None,
            delivered_qty=float(delivered) if delivered is not None else None,
            confidence=max(0.0, min(1.0, float(raw.get("confidence", 0)))),
        ))
    if not lines:
        raise ReaderError("Couldn't make out any invoice lines. Try a sharper photo, or type them.")
    date_guess = None
    if recorded.get("invoice_date"):
        try:
            date_guess = date.fromisoformat(str(recorded["invoice_date"])[:10])
        except ValueError:
            date_guess = None
    total = recorded.get("total")
    header_doubt = []
    if recorded.get("supplier_name") and float(recorded.get("supplier_confidence", 1)) < SURE:
        header_doubt.append("The supplier's name was hard to read. Check it.")
    if date_guess and float(recorded.get("date_confidence", 1)) < SURE:
        header_doubt.append("The invoice date was hard to read. Check it.")
    if total is not None and float(recorded.get("total_confidence", 1)) < SURE:
        header_doubt.append("The total was hard to read. Check it.")
    total = round(float(total), 2) if total is not None else None
    reasons = check(lines, total, header_doubt)
    return schemas.ReadOut(
        supplier_guess=(str(recorded.get("supplier_name") or "").strip()[:255] or None), date_guess=date_guess,
        reference=(str(recorded.get("reference") or "").strip()[:60] or None), total=total, lines=lines,
        needs_review=bool(reasons), reasons=reasons, reader=reader,
    )
