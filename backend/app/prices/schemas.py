"""What the price-intelligence API sends and takes. The contract the frontend is built against.

Routes (all under /api/v1/prices, signed in; "manager" = owner or admin):

- GET  /status                         -> StatusOut        onboarding progress: invoices in, suppliers, first digest
- GET  /digest                         -> DigestOut        this week's digest, built on first request
- POST /digest/rebuild  (manager)      -> DigestOut        recompute now (after new invoices)
- GET  /digest/talking-points          -> TalkingPointsOut five lines for the manager meeting
- GET  /items                          -> list[ItemRow]
- GET  /items/{id}                     -> ItemDetail
- PATCH /items/{id}      (manager)     <- ItemPatch        pack size correction ("Wrong?")
- POST /items/{id}/hide  (manager)     -> ItemRow          "Not useful": hidden from digests for 4 weeks
- POST /items/{id}/unhide (manager)    -> ItemRow
- POST /items/{id}/move  (manager)     <- MoveIn -> ItemRow   records the owner's decision to move; nothing is ordered
- GET  /questions                      -> list[QuestionOut]   open, by dollar impact
- POST /questions/{id}/answer          <- AnswerIn -> QuestionOut
- GET  /thread                         -> list[MessageOut]    oldest first
- POST /thread                         <- AskIn -> list[MessageOut]   the new messages (yours, then the agent's or a pending one)
- POST /drafts           (manager)     <- DraftIn -> DraftOut
- GET  /drafts/{id}                    -> DraftOut
- PATCH /drafts/{id}     (manager)     <- DraftPatch -> DraftOut
- POST /drafts/{id}/send (manager)     -> DraftOut   marks it sent and remembers the rep; the mail itself goes from the owner's mail app
- POST /drafts/{id}/discard (manager)  -> DraftOut
- POST /invoices/parse                 <- ParseIn -> ParseOut    pasted or forwarded invoice text as lines, nothing saved
- POST /invoices/read    (manager)     <- ReadIn -> ReadOut      a photo or PDF read into lines, with confidence; nothing saved
- POST /invoices         (manager)     <- InvoiceIn -> InvoiceSaved
- GET  /invoices                       -> list[InvoiceRow]
- POST /invoices/{id}/confirm (manager) -> InvoiceRow   "Looks right": an invoice that needed a check now counts
- POST /digest/email     (manager)     -> EmailOut     this week's digest, mailed to the caller now
- GET  /digest/email/preview (manager) -> text/html    the mail as it would be sent
- POST /digest/cron                    -> CronOut      header X-Cron-Secret; mails every restaurant's digest once a week
- GET  /digest/not-useful/{token}      -> text/html    the link in the mail: hides the item for 4 weeks
- GET  /billcom/status   (manager)     -> BillcomStatusOut
- POST /billcom/sync     (manager)     -> BillcomSyncOut   pulls bills from Bill.com; idempotent on the bill id
- POST /billcom/webhook                -> {"ok": true}     header X-Billcom-Secret; a bill changed, pull again
- GET  /bookkeeper/clients             -> list[ClientRow]   bookkeepers only
- GET  /bookkeeper/clients/{rid}/questions          -> list[QuestionOut]
- POST /bookkeeper/clients/{rid}/questions/{id}/answer <- AnswerIn -> QuestionOut
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CATEGORIES = ("food", "dry", "paper", "beer", "wine", "liquor")
#: Beer, wine and liquor come from one distributor each: trend and short deliveries only, no switching.
SINGLE_SOURCE = frozenset({"beer", "wine", "liquor"})
SOURCES = ("billcom", "email", "photo", "pdf", "typed")
SECTIONS = ("switch", "up", "owed", "margin", "due")
DRAFT_KINDS = ("price_challenge", "credit_claim", "short_delivery")

Category = Literal[CATEGORIES]  # type: ignore[valid-type]
Source = Literal[SOURCES]  # type: ignore[valid-type]
Section = Literal[SECTIONS]  # type: ignore[valid-type]
DraftKind = Literal[DRAFT_KINDS]  # type: ignore[valid-type]


class SupplierRef(BaseModel):
    id: str
    name: str


#: What reads a photo or PDF here: Claude, a vision model on the OpenAI-compatible API, or "text" --
#: PDFs that carry text, read by rule, no model. Only the first two read photos.
Reader = Literal["claude", "openai", "text"]
READS_PHOTOS = ("claude", "openai")


class StatusOut(BaseModel):
    invoice_count: int
    #: What reads a photo or PDF into lines on this site: "claude", "scripted" (demo and tests; reads
    #: only the PDFs this app wrote itself), or null when photos and PDFs have to be typed up.
    reader: Reader | None = None
    #: Bill.com credentials are set on this site, so "Connect Bill.com" can pull bills.
    billcom_configured: bool = False
    #: Where the weekly digest is mailed: the signed-in person's address, or null when mail is off.
    digest_email_to: str | None = None
    suppliers: list[SupplierRef]
    #: The Monday the first digest arrives, or arrived.
    first_digest: date | None
    questions_open: int
    #: One line for the bottom of onboarding: "2 invoices from Chef's Warehouse. First digest Monday."
    progress: str
    #: Where forwarded invoices go. Null when this site can't receive mail.
    forward_address: str | None


class Source_(BaseModel):
    """A chip under an agent message: the invoice (date + supplier) or item it used."""

    kind: Literal["invoice", "item", "digest", "document", "recipe"]
    id: str
    label: str


class CardAction(BaseModel):
    kind: Literal["move", "draft", "reprice", "calendar"]
    #: "Move kale" or "Draft message".
    label: str
    #: For a draft: which kind, to which supplier.
    draft_kind: DraftKind | None = None
    supplier_id: str | None = None


class Card(BaseModel):
    section: Section
    #: The item, or for a menu-margin card the recipe, or for a coming-due card the calendar item.
    item_id: str
    item_name: str
    recipe_id: str | None = None
    calendar_item_id: str | None = None
    #: Dollars per week (switch, up) or dollars outstanding (owed).
    impact: float
    #: The comparison in plain words: item, supplier, dollars.
    sentence: str
    action: CardAction
    sources: list[Source_]


class DigestSection(BaseModel):
    key: Section
    label: str
    total: float
    cards: list[Card]


class PrimeLine(BaseModel):
    """The week's prime cost at the top of the digest, or what to connect to see it."""

    week_of: date
    complete: bool
    prime_pct: float | None
    food_pct: float | None
    paper_pct: float | None
    labor_pct: float | None
    sales_net: float | None
    labor_dollars: float | None
    purchases: float
    prompt: str | None


class DigestOut(BaseModel):
    id: str
    week_start: date
    #: Present when connections are on for the restaurant.
    prime: PrimeLine | None = None
    total: float
    item_count: int
    supplier_count: int
    sections: list[DigestSection]
    #: Cards past the tenth: "4 smaller items".
    hidden_count: int
    questions_open: int
    built_at: datetime
    #: Which invoices it rests on, for the footer line.
    invoices_through: date | None
    supplier_names: list[str]


class TalkingPointsOut(BaseModel):
    lines: list[str]
    sources: list[Source_]


ItemState = Literal["ok", "insufficient", "awaiting", "hidden"]


class ItemRow(BaseModel):
    id: str
    name: str
    category: str
    pack_size: float
    pack_unit: str
    unit: str
    state: ItemState
    #: Last price paid per pack, where it came from, and when.
    last_price: float | None
    last_supplier: SupplierRef | None
    last_date: date | None
    #: Change per pack over the last 8 weeks at the current supplier; null when flat or unknown.
    change_8w: float | None
    hidden_until: date | None
    questions_open: int


class SeriesPoint(BaseModel):
    week_start: date
    #: Per pack. Null for a week with no invoice.
    price: float | None


class Series(BaseModel):
    supplier: SupplierRef
    current: bool
    points: list[SeriesPoint]


class LineRow(BaseModel):
    id: str
    invoice_id: str
    invoice_date: date
    supplier: SupplierRef
    #: As printed on the invoice.
    description: str
    qty: float
    pack_text: str | None
    #: Per pack as billed, and per canonical unit as derived.
    unit_price: float
    per_unit: float | None
    pack_size: float | None
    extended: float
    delivered_qty: float | None
    credited: float


class OrderBrief(BaseModel):
    """An open purchase order, as the price pages mention it."""

    id: str
    quantity: float
    unit: str
    supplier_name: str | None
    status: Literal["ordered", "shipped"]
    expected_on: date | None
    overdue: bool


class StockLink(BaseModel):
    """The inventory item a price item is counted as, and how it stands."""

    inventory_item_id: str
    name: str
    unit: str
    quantity_on_hand: float
    reorder_threshold: float
    low_stock: bool
    #: False when the invoice's unit can't be read in the stock unit (lb against L): then nothing is booked.
    converts: bool
    #: A reorder waiting under Recommendations for this stock item, if there is one.
    pending_action_id: str | None
    #: An order already placed and not yet received.
    on_order: OrderBrief | None = None


class StockRow(BaseModel):
    """For the Inventory page: what the invoices say about one stock item."""

    inventory_item_id: str
    item_id: str
    item_name: str
    unit: str
    converts: bool
    #: The last price paid, per `unit` of stock.
    per_unit: float | None
    last_supplier: SupplierRef | None
    last_date: date | None
    pending_action_id: str | None
    on_order: OrderBrief | None = None


class AgreedOut(BaseModel):
    id: str
    supplier: SupplierRef
    unit_price: float
    base_unit: str
    effective_from: date
    effective_to: date | None
    source_document_id: str | None


class RecipeRef(BaseModel):
    id: str
    name: str
    status: str


class ItemDetail(ItemRow):
    #: "Reading this as 24 per case."
    pack_sentence: str
    #: What it is counted as in Inventory, if anything, and whether that was the owner's choice.
    stock: StockLink | None = None
    inventory_link: Literal["auto", "owner"] = "auto"
    #: The agreed price in effect, per unit, with its supplier and document; and the recipes that use the item.
    agreed: AgreedOut | None = None
    used_in: list[RecipeRef] = []
    #: The digest's card for this item, if it has one this week.
    card: Card | None
    #: "Not enough matched invoices to compare yet." or "Answer 1 question to compare." or null.
    note: str | None
    series: list[Series]
    lines: list[LineRow]
    moved_to: SupplierRef | None


class ItemPatch(BaseModel):
    """The pack size ("Wrong?"), or what the item is counted as in Inventory (null: not counted)."""

    pack_size: float | None = Field(default=None, gt=0)
    pack_unit: str | None = Field(default=None, max_length=32)
    inventory_item_id: str | None = None


class AgreedIn(BaseModel):
    """"Set agreed price" on an item: becomes an `agreed_price` proposal, approved like any other."""

    supplier_id: str
    #: Per pack as the supplier quotes it, or per the item's unit; one of the two.
    per_pack: float | None = Field(default=None, gt=0)
    unit_price: float | None = Field(default=None, gt=0)
    effective_from: date | None = None
    cited: str | None = Field(default=None, max_length=200)


class MoveIn(BaseModel):
    supplier_id: str


class QuestionOption(BaseModel):
    key: str
    label: str


class CropLine(BaseModel):
    """One printed line of the invoice around the one asked about."""

    text: str
    hit: bool


class QuestionOut(BaseModel):
    id: str
    #: match: which item; pack: how big a pack; repack: the pack printed differs from the one confirmed before.
    kind: Literal["match", "pack", "repack"]
    text: str
    options: list[QuestionOption]
    #: The invoice crop: the line asked about and a neighbour either side.
    crop: list[CropLine]
    #: Whether a crop image exists; otherwise the printed line stands in (Bill.com, text email).
    has_image: bool
    supplier: SupplierRef
    invoice_id: str
    invoice_date: date
    line_id: str
    #: The line's dollars on that invoice: the ordering key.
    dollar_impact: float
    status: Literal["open", "answered", "held"]
    answer: str | None
    created_at: datetime


class AnswerIn(BaseModel):
    answer: Literal["yes", "no", "other"]
    #: For "other": the item's name (match) or the count per pack as text (pack).
    text: str | None = Field(default=None, max_length=200)
    pack_size: float | None = Field(default=None, gt=0)


class DraftSummary(BaseModel):
    id: str
    status: Literal["draft", "sent", "discarded"]
    subject: str
    to_name: str | None
    supplier: SupplierRef


class MessageOut(BaseModel):
    id: str
    role: Literal["you", "agent"]
    text: str
    sources: list[Source_]
    #: An agent question posed in the thread, with its state.
    question: QuestionOut | None
    #: A draft the agent wrote in the thread.
    draft: DraftSummary | None
    #: "pending" while a run is still answering.
    status: Literal["ok", "pending", "failed"]
    created_at: datetime


class AskIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    #: "Ask about this item": scopes the question.
    item_id: str | None = None


class DraftIn(BaseModel):
    item_id: str
    kind: DraftKind | None = None
    supplier_id: str | None = None


class DraftLine(BaseModel):
    line_id: str
    label: str


class DraftOut(BaseModel):
    id: str
    status: Literal["draft", "sent", "discarded"]
    kind: DraftKind
    supplier: SupplierRef
    item_id: str
    item_name: str
    to_name: str | None
    to_email: str | None
    #: Set when the rep was remembered from an earlier message.
    remembered_from: date | None
    subject: str
    body: str
    lines: list[DraftLine]
    created_at: datetime
    sent_at: datetime | None


class DraftPatch(BaseModel):
    to_name: str | None = Field(default=None, max_length=120)
    to_email: str | None = Field(default=None, max_length=254)
    subject: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, max_length=5000)


class LineIn(BaseModel):
    description: str = Field(min_length=1, max_length=200)
    qty: float = Field(gt=0)
    pack_text: str | None = Field(default=None, max_length=40)
    unit_price: float = Field(ge=0)
    extended: float | None = None
    delivered_qty: float | None = Field(default=None, ge=0)
    #: How sure the reader was of this line, 0-1. Null for a line a person typed.
    confidence: float | None = Field(default=None, ge=0, le=1)


READ_MEDIA_TYPES = ("application/pdf", "image/jpeg", "image/png", "image/webp", "image/gif")


class ReadIn(BaseModel):
    """A photo or PDF of one invoice, as the browser read it."""

    filename: str | None = Field(default=None, max_length=255)
    media_type: Literal[READ_MEDIA_TYPES]  # type: ignore[valid-type]
    #: The file, base64. 12 MB of file is about 16 MB of text.
    data_base64: str = Field(min_length=1, max_length=16_000_000)


class ReadOut(BaseModel):
    """What the reader saw. Nothing is saved until the person checks it and saves."""

    supplier_guess: str | None
    date_guess: date | None
    reference: str | None
    #: The total as printed on the invoice, when one was found.
    total: float | None
    lines: list[LineIn]
    #: True when a check failed: the lines don't add to the total, a line's arithmetic is off,
    #: or the reader was unsure (confidence under 0.8) about anything. `reasons` says which.
    needs_review: bool
    reasons: list[str]
    reader: Reader


class ParseIn(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


class ParseOut(BaseModel):
    lines: list[LineIn]
    #: Lines that couldn't be read, by number, so nothing is guessed.
    skipped: list[int]
    supplier_guess: str | None
    date_guess: date | None
    #: A "Total" line, when the text had one.
    total: float | None = None
    reference: str | None = None


class InvoiceIn(BaseModel):
    supplier_id: str | None = None
    #: A new supplier, by name, when there's no id.
    supplier_name: str | None = Field(default=None, max_length=255)
    invoice_date: date
    reference: str | None = Field(default=None, max_length=60)
    source: Source = "typed"
    lines: list[LineIn] = Field(min_length=1)
    #: The total printed on the invoice. When given, the lines have to add to it within $0.02 or the
    #: invoice is saved as needing a check and stays out of the digest until confirmed.
    total: float | None = Field(default=None, ge=0)


INVOICE_STATUSES = ("complete", "needs_review")
InvoiceStatus = Literal[INVOICE_STATUSES]  # type: ignore[valid-type]


class InvoiceSaved(BaseModel):
    id: str
    line_count: int
    matched: int
    questions_asked: int
    #: After the first invoice: the agent's one question about the biggest line.
    first_question: QuestionOut | None


class InvoiceRow(BaseModel):
    id: str
    supplier: SupplierRef
    invoice_date: date
    reference: str | None
    source: str
    line_count: int
    #: What the lines add to.
    total: float
    #: What the invoice says, when it was read or typed in.
    printed_total: float | None = None
    unmatched: int
    #: needs_review: a check failed when it came in; it stays out of the digest until "Looks right".
    status: InvoiceStatus = "complete"
    #: Why it needs a check, one sentence per reason. Empty when complete.
    review_reasons: list[str] = []


class EmailOut(BaseModel):
    subject: str
    sent_to: list[str]
    #: False when no mail key is set: the mail was printed to the server log instead.
    delivered: bool


class CronOut(BaseModel):
    sent: int
    skipped: int


class BillcomStatusOut(BaseModel):
    configured: bool
    #: The sandbox or production gateway in use, for the settings line.
    environment: Literal["sandbox", "production"] | None
    last_synced_at: datetime | None
    #: Where Bill.com should post bill.created / bill.updated events.
    webhook_path: str


class BillcomSyncOut(BaseModel):
    #: Bills Bill.com returned.
    pulled: int
    #: Saved as new invoices; the rest were already in (same bill id) or had no lines.
    added: int
    skipped: int
    questions_asked: int


class ClientRow(BaseModel):
    restaurant_id: str
    name: str
    questions_open: int
    last_digest: date | None
    flagged_month: float
