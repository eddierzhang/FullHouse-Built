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


def apply_schedule_draft(db: Session, payload: dict) -> str:
    """Approving a schedule draft publishes it, as the Publish button does. Nobody is notified: a person tells the team."""
    from app.audit.context import get_actor
    from app.margin_labor import schedule
    from app.restaurant.inventory import site_scope

    out = schedule.publish(db, site_scope(db), payload["schedule_id"], get_actor().id or "a manager", confirm=True)
    extra = f" {len(out['warnings'])} thing{'s' if len(out['warnings']) != 1 else ''} to check were shown on the Labor page." if out["warnings"] else ""
    return f"Published the schedule for the week of {payload.get('week_of')}.{extra} Nobody was notified: tell the team, or export it."


def _explain_schedule_draft(payload: dict) -> Explanation:
    return Explanation(f"A draft schedule for the week of {payload.get('week_of')} is ready: {payload.get('hours_total')} hours, built from the people needed each half hour.",
                       "Approving publishes it. FullHouse notifies nobody and writes to no scheduler or payroll system.",
                       "Review and edit it on the Labor page first: lock the shifts you want kept, and regenerate around them. Then tell the team yourself.")


def register() -> None:
    appliers.APPLIERS.setdefault("channel_reprice", apply_channel_reprice)
    explain.EXTRA.setdefault("channel_reprice", _explain_channel_reprice)
    appliers.APPLIERS.setdefault("schedule_draft", apply_schedule_draft)
    explain.EXTRA.setdefault("schedule_draft", _explain_schedule_draft)
