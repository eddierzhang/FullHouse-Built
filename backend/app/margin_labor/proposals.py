"""The proposal kinds these modules add, registered beside the older ones: how approving one is carried out, and
the three sentences Recommendations shows for it. Approving never changes a price, an order or a schedule outside
FullHouse: a person does that."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.agents import appliers, explain
from app.agents.explain import Explanation


def apply_channel_reprice(db: Session, payload: dict) -> str:
    """Nothing changes here: the owner changes the price on the platform. Approval records that they mean to."""
    return (f"Noted: {payload.get('item_name', 'the item')} to be priced at ${payload.get('suggested_price', '?')} on {payload.get('platform', 'the platform')}. "
            f"{payload.get('check', '')} Change it on the platform; FullHouse never does.").replace("  ", " ")


def _explain_channel_reprice(payload: dict) -> Explanation:
    item, platform = payload.get("item_name") or "The item", payload.get("platform") or "the platform"
    issue = (f"{item} keeps ${float(payload.get('unit_contribution') or 0):.2f} an item on {platform} at ${float(payload.get('current_price') or 0):.2f}, "
             f"after commission, fees, packaging and food cost.")
    change = (f"Approving records that you mean to price it at ${float(payload.get('suggested_price') or 0):.2f} on {platform}. FullHouse never writes to a "
              "delivery platform, so nothing else changes.")
    know = f"{payload.get('check', '')} At that price the item keeps ${float(payload.get('contribution_at_suggested') or 0):.2f}. Change the price on the platform yourself."
    return Explanation(issue, change, know.strip())


def register() -> None:
    appliers.APPLIERS.setdefault("channel_reprice", apply_channel_reprice)
    explain.EXTRA.setdefault("channel_reprice", _explain_channel_reprice)
