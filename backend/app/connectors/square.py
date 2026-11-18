"""Square: orders, with a cursor, into sales by day and by item. Read-only.

`map_orders` turns Square's order payloads into our rows and has a golden-file test
(fixtures/connectors/square_orders.json -> .expected.json); `sync` pages through the Orders
API from the connection's cursor (the last closed-at seen) to `until`.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

import httpx

from app.connectors.base import SyncResult, TenantContext

BASES = {"production": "https://connect.squareup.com", "sandbox": "https://connect.squareupsandbox.com"}
BACKFILL_DAYS = 90


def _money(block: dict | None) -> float:
    return (block or {}).get("amount", 0) / 100.0


def map_orders(orders: list[dict]) -> dict[str, list[dict]]:
    """Square orders -> {"sales_days": [...], "sales_items": [...]}. Net = total - tax - tip; the day is the closing day."""
    days: dict[str, dict] = {}
    items: dict[tuple[str, str], dict] = {}
    for order in orders:
        if order.get("state") == "CANCELED":
            continue
        closed = order.get("closed_at") or order.get("created_at")
        if not closed:
            continue
        day = closed[:10]
        total = _money(order.get("total_money"))
        tax = _money(order.get("total_tax_money"))
        tip = _money(order.get("total_tip_money"))
        net = round(total - tax - tip, 2)
        row = days.setdefault(day, {"date": day, "net_sales": 0.0, "gross_sales": 0.0, "tax": 0.0, "tips": 0.0, "covers": 0, "source": "pos"})
        row["net_sales"] = round(row["net_sales"] + net, 2)
        row["gross_sales"] = round(row["gross_sales"] + total, 2)
        row["tax"] = round(row["tax"] + tax, 2)
        row["tips"] = round(row["tips"] + tip, 2)
        row["covers"] += 1
        for line in order.get("line_items") or []:
            name = (line.get("name") or "Item").strip()
            item_id = line.get("catalog_object_id") or name
            qty = float(line.get("quantity") or 0)
            line_net = _money(line.get("total_money")) - _money(line.get("total_tax_money"))
            key = (day, item_id)
            item = items.setdefault(key, {"date": day, "pos_item_id": item_id, "pos_item_name": name, "qty": 0.0, "net": 0.0, "source": "pos"})
            item["qty"] = round(item["qty"] + qty, 4)
            item["net"] = round(item["net"] + line_net, 2)
    return {"sales_days": sorted(days.values(), key=lambda r: r["date"]),
            "sales_items": sorted(items.values(), key=lambda r: (r["date"], r["pos_item_name"]))}


class SquareConnector:
    kind = "pos"
    provider = "square"
    auth = "oauth"
    capabilities = ("sales_days", "sales_items", "menu")

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.transport = transport

    def _base(self) -> str:
        from app.connectors.oauth import environment

        return BASES[environment("square")]

    def _client(self, ctx: TenantContext) -> httpx.Client:
        from app.connectors import oauth

        credentials = ctx.credentials
        expires_at = credentials.get("expires_at")
        if expires_at and datetime.fromisoformat(expires_at.replace("Z", "+00:00")) < datetime.now(timezone.utc) + timedelta(minutes=5):
            credentials = oauth.refresh("square", credentials, self.transport)
            if ctx.connection is not None:
                from app.connectors.crypto import encrypt

                ctx.connection.credentials_enc = encrypt(credentials)
        return httpx.Client(base_url=self._base(), timeout=30, transport=self.transport,
                            headers={"Authorization": f"Bearer {credentials.get('access_token', '')}", "Square-Version": "2025-01-23"})

    def sync(self, ctx: TenantContext, cursor: str | None, until: datetime) -> SyncResult:
        """One page of orders closed since `cursor`. Rows for both capabilities ride in one result, keyed in payload."""
        with self._client(ctx) as http:
            locations = ctx.connection.settings.get("location_ids") if ctx.connection else None
            if not locations:
                response = http.get("/v2/locations")
                response.raise_for_status()
                locations = [loc["id"] for loc in response.json().get("locations", [])]
                if ctx.connection is not None:
                    ctx.connection.settings = {**ctx.connection.settings, "location_ids": locations}
            since = cursor or (until - timedelta(days=BACKFILL_DAYS)).isoformat()
            page_cursor = None
            if cursor and cursor.startswith("page:"):
                page_cursor, since = cursor[5:].split("|", 1)
            body = {"location_ids": locations[:10], "limit": 200,
                    "query": {"filter": {"state_filter": {"states": ["COMPLETED"]},
                                         "date_time_filter": {"closed_at": {"start_at": since, "end_at": until.isoformat()}}},
                              "sort": {"sort_field": "CLOSED_AT", "sort_order": "ASC"}}}
            if page_cursor:
                body["cursor"] = page_cursor
            response = http.post("/v2/orders/search", json=body)
            response.raise_for_status()
            data = response.json()
        orders = data.get("orders", [])
        mapped = map_orders(orders)
        rows = [{"_capability": "sales_days", **r} for r in mapped["sales_days"]] + [{"_capability": "sales_items", **r} for r in mapped["sales_items"]]
        if data.get("cursor"):
            return SyncResult(rows=rows, next_cursor=f"page:{data['cursor']}|{since}", done=False, capability="mixed")
        latest = max((o.get("closed_at") or "" for o in orders), default=None)
        return SyncResult(rows=rows, next_cursor=latest or until.isoformat(), done=True, capability="mixed")

    def healthcheck(self, ctx: TenantContext) -> str:
        try:
            with self._client(ctx) as http:
                return "ok" if http.get("/v2/locations").status_code == 200 else "reauth"
        except Exception:
            return "down"
