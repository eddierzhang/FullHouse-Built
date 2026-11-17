"""The owner's autopilot limits, as pure functions: a drafted order and a set of limits go in, every rule's
verdict comes out. Nothing here reads or writes the database, so each rule can be tested exhaustively, and
nothing under app/agents may import it: the agent proposes, this decides, and only an owner sets the limits.

An order goes out by itself only if every rule passes. A single failure holds it for a person, with the
failed rule named in plain words.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DraftLine:
    description: str
    #: Packs on this order, and the price a pack it is expected at.
    qty: float
    unit_price: float | None
    #: Invoices from this supplier carrying the item in the last eight weeks.
    times_bought: int
    #: Median packs a week over those eight weeks.
    median_qty: float
    #: The price a pack on the invoice before the latest one; None when there is only one.
    previous_price: float | None


@dataclass(frozen=True)
class Draft:
    supplier_name: str
    total: float
    lines: tuple[DraftLine, ...]
    #: How this supplier takes orders, and the address an emailed order goes to.
    channel: str = "email"
    order_email: str | None = None
    #: What autopilot has already sent this supplier in the last seven days.
    sent_this_week: float = 0.0
    #: Whether the stock figures the order rests on are current, and why not when they aren't.
    stock_fresh: bool = True
    stock_detail: str = ""
    #: Whether this site can send mail at all.
    can_send: bool = True


@dataclass(frozen=True)
class Limits:
    enabled: bool = False
    #: The restaurant's pause-all switch.
    paused: bool = False
    per_order_cap: float = 300.0
    weekly_cap: float = 900.0
    max_price_rise_pct: float = 10.0
    max_qty_multiple: float = 1.5
    min_history: int = 4


@dataclass(frozen=True)
class RuleResult:
    rule: str
    ok: bool
    detail: str

    def as_dict(self) -> dict:
        return {"rule": self.rule, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True)
class Verdict:
    rules: tuple[RuleResult, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return all(r.ok for r in self.rules)

    @property
    def failed(self) -> list[RuleResult]:
        return [r for r in self.rules if not r.ok]


def _money(value: float) -> str:
    return f"${value:,.2f}"


def evaluate(draft: Draft, limits: Limits) -> Verdict:
    """Every rule, in the order an owner would ask them. All are always evaluated, so a held order names
    everything wrong with it at once."""
    rules: list[RuleResult] = []

    def rule(name: str, ok: bool, good: str, bad: str) -> None:
        rules.append(RuleResult(name, bool(ok), good if ok else bad))

    rule("autopilot_on", limits.enabled, f"Autopilot is on for {draft.supplier_name}.", f"Autopilot is off for {draft.supplier_name}.")
    rule("not_paused", not limits.paused, "Autopilot is not paused.", "Autopilot is paused for the whole restaurant.")
    by_email = draft.channel == "email" and bool(draft.order_email)
    rule("by_email", by_email and draft.can_send, f"{draft.supplier_name} takes orders by email at {draft.order_email}.",
         "This site has no mailbox to send from." if by_email else
         (f"{draft.supplier_name} takes orders by {draft.channel}, which a person has to do." if draft.channel != "email"
          else f"There is no order address for {draft.supplier_name}."))
    rule("per_order_cap", draft.total <= limits.per_order_cap + 1e-9,
         f"{_money(draft.total)} is within your {_money(limits.per_order_cap)} limit for one order.",
         f"{_money(draft.total)} is over your {_money(limits.per_order_cap)} limit for one order.")
    week = draft.sent_this_week + draft.total
    rule("weekly_cap", week <= limits.weekly_cap + 1e-9,
         f"{_money(week)} this week is within your {_money(limits.weekly_cap)} weekly limit.",
         f"With {_money(draft.sent_this_week)} already sent this week, this order would pass your {_money(limits.weekly_cap)} weekly limit.")
    rule("has_lines", len(draft.lines) > 0, f"{len(draft.lines)} lines.", "The order has no lines.")

    new = [l for l in draft.lines if l.times_bought < limits.min_history]
    rule("known_items", not new, f"Every item has been bought from {draft.supplier_name} at least {limits.min_history} times in eight weeks.",
         "; ".join(f"{l.description} has been bought {l.times_bought} time{'s' if l.times_bought != 1 else ''} in eight weeks" for l in new[:3])
         + (f"; and {len(new) - 3} more" if len(new) > 3 else "") + f" (you asked for at least {limits.min_history}).")

    big = [l for l in draft.lines if l.median_qty > 0 and l.qty > l.median_qty * limits.max_qty_multiple + 1e-9]
    rule("usual_quantity", not big, f"No line is more than {limits.max_qty_multiple:g} times its usual week.",
         "; ".join(f"{l.description}: {l.qty:g} against a usual {l.median_qty:g}" for l in big[:3])
         + (f"; and {len(big) - 3} more" if len(big) > 3 else "") + f" (your limit is {limits.max_qty_multiple:g} times the usual).")

    def rise(line: DraftLine) -> float:
        if not line.previous_price or line.unit_price is None or line.previous_price <= 0:
            return 0.0
        return (line.unit_price - line.previous_price) / line.previous_price * 100

    dearer = [l for l in draft.lines if rise(l) > limits.max_price_rise_pct + 1e-9]
    rule("price_steady", not dearer, f"No price rose more than {limits.max_price_rise_pct:g}% since the invoice before.",
         "; ".join(f"{l.description} went from {_money(l.previous_price or 0)} to {_money(l.unit_price or 0)}, up {rise(l):.0f}%" for l in dearer[:3])
         + (f"; and {len(dearer) - 3} more" if len(dearer) > 3 else "") + f" (your limit is {limits.max_price_rise_pct:g}%).")

    rule("stock_fresh", draft.stock_fresh, draft.stock_detail or "The stock figures are current.",
         draft.stock_detail or "The stock figures are out of date.")
    return Verdict(tuple(rules))
