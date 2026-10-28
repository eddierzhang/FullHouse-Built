"""Bill.com behind the connector interface: the client in app.prices.billcom, unchanged."""

from __future__ import annotations

from datetime import date, datetime

from app.connectors.base import SyncResult, TenantContext


class BillcomConnector:
    kind = "ap"
    provider = "billcom"
    auth = "api_key"
    capabilities = ("invoices",)

    def sync(self, ctx: TenantContext, cursor: str | None, until: datetime) -> SyncResult:
        """Pull bills and save the new ones as invoices (app.prices.billcom.sync is idempotent per bill)."""
        from app.prices import billcom

        out = billcom.sync(ctx.db, ctx.restaurant_id, today=until.date() if isinstance(until, datetime) else until)
        return SyncResult(rows=[{"pulled": out.pulled, "added": out.added, "skipped": out.skipped}],
                          next_cursor=date.today().isoformat(), done=True, capability="invoices")

    def healthcheck(self, ctx: TenantContext) -> str:
        from app.config import settings

        return "ok" if settings.billcom_configured else "reauth"
