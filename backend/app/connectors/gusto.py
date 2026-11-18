"""Gusto: processed payrolls into labor by day. Read-only.

A payroll covers a pay period; its gross pay and hours are spread evenly over the days of
the period (the API gives totals per period, not per day). `map_payrolls` has a golden-file
test; `sync` pulls payrolls processed since the cursor.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import httpx

from app.connectors.base import SyncResult, TenantContext

BASES = {"production": "https://api.gusto.com", "sandbox": "https://api.gusto-demo.com"}


def map_payrolls(payrolls: list[dict]) -> list[dict]:
    """Gusto payrolls -> labor_days rows, totals spread evenly over each pay period."""
    days: dict[str, dict] = {}
    for payroll in payrolls:
        if not payroll.get("processed", True):
            continue
        period = payroll.get("pay_period") or {}
        try:
            start = date.fromisoformat(str(period.get("start_date"))[:10])
            end = date.fromisoformat(str(period.get("end_date"))[:10])
        except ValueError:
            continue
        totals = payroll.get("totals") or {}
        dollars = float(totals.get("gross_pay") or 0)
        hours = 0.0
        for comp in payroll.get("employee_compensations") or []:
            for hc in comp.get("hourly_compensations") or []:
                hours += float(hc.get("hours") or 0)
            for fc in comp.get("fixed_compensations") or []:
                hours += float(fc.get("hours") or 0) if "hours" in fc else 0.0
        if hours == 0:
            hours = float(totals.get("hours") or 0)
        span = max((end - start).days + 1, 1)
        for i in range(span):
            day = (start + timedelta(days=i)).isoformat()
            row = days.setdefault(day, {"date": day, "hours": 0.0, "dollars": 0.0, "source": "payroll"})
            row["hours"] = round(row["hours"] + hours / span, 2)
            row["dollars"] = round(row["dollars"] + dollars / span, 2)
    return sorted(days.values(), key=lambda r: r["date"])


class GustoConnector:
    kind = "payroll"
    provider = "gusto"
    auth = "oauth"
    capabilities = ("labor_days",)

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.transport = transport

    def _client(self, ctx: TenantContext) -> httpx.Client:
        from app.connectors.oauth import environment

        return httpx.Client(base_url=BASES[environment("gusto")], timeout=30, transport=self.transport,
                            headers={"Authorization": f"Bearer {ctx.credentials.get('access_token', '')}", "X-Gusto-API-Version": "2024-04-01"})

    def sync(self, ctx: TenantContext, cursor: str | None, until: datetime) -> SyncResult:
        with self._client(ctx) as http:
            company = (ctx.connection.settings.get("company_id") if ctx.connection else None) or ctx.credentials.get("company_id")
            if not company:
                response = http.get("/v1/token_info")
                response.raise_for_status()
                company = (response.json().get("resource") or {}).get("uuid")
                if ctx.connection is not None and company:
                    ctx.connection.settings = {**ctx.connection.settings, "company_id": company}
            start = (cursor or (until - timedelta(days=90)).date().isoformat())[:10]
            response = http.get(f"/v1/companies/{company}/payrolls",
                                params={"processed": "true", "start_date": start, "end_date": until.date().isoformat(), "include": "totals"})
            response.raise_for_status()
            payrolls = response.json()
        rows = map_payrolls(payrolls)
        return SyncResult(rows=rows, next_cursor=until.date().isoformat(), done=True, capability="labor_days")

    def healthcheck(self, ctx: TenantContext) -> str:
        try:
            with self._client(ctx) as http:
                return "ok" if http.get("/v1/token_info").status_code == 200 else "reauth"
        except Exception:
            return "down"
