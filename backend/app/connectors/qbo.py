"""QuickBooks Online: complete invoices pushed as bills. Never pulled, never paid.

A bill per invoice, coded to an expense account by the item's category, the supplier as the
vendor (made if missing). The bill id is kept on `invoices.external_refs["qbo"]`, so a second
push of the same invoice does nothing.
"""

from __future__ import annotations

import httpx
from sqlalchemy.orm import Session

from app.prices.models import Invoice, InvoiceLine, PriceItem
from app.restaurant.models import Supplier

BASES = {"production": "https://quickbooks.api.intuit.com", "sandbox": "https://sandbox-quickbooks.api.intuit.com"}
#: Category -> the expense account's name in QuickBooks; made with these names if missing.
ACCOUNTS = {"food": "Cost of Goods Sold:Food", "dry": "Cost of Goods Sold:Food", "paper": "Cost of Goods Sold:Paper",
            "beer": "Cost of Goods Sold:Beverage", "wine": "Cost of Goods Sold:Beverage", "liquor": "Cost of Goods Sold:Beverage"}


class QboConnector:
    kind = "accounting"
    provider = "qbo"
    auth = "oauth"
    capabilities = ("push_invoices",)

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.transport = transport

    def _client(self, credentials: dict) -> httpx.Client:
        from app.connectors.oauth import environment

        return httpx.Client(base_url=BASES[environment("qbo")], timeout=30, transport=self.transport,
                            headers={"Authorization": f"Bearer {credentials.get('access_token', '')}", "Accept": "application/json"})

    def sync(self, ctx, cursor, until):
        from app.connectors.base import SyncResult

        return SyncResult(rows=[], next_cursor=cursor, done=True, capability="push_invoices")  # nothing is pulled

    def healthcheck(self, ctx) -> str:
        return "ok" if ctx.credentials.get("access_token") else "reauth"

    # --- pushing ------------------------------------------------------------------------------

    def _query_one(self, http: httpx.Client, realm: str, query: str) -> dict | None:
        response = http.get(f"/v3/company/{realm}/query", params={"query": query})
        response.raise_for_status()
        body = response.json().get("QueryResponse", {})
        for key, value in body.items():
            if isinstance(value, list) and value:
                return value[0]
        return None

    def _vendor(self, http: httpx.Client, realm: str, name: str) -> str:
        safe = name.replace("'", "\\'")
        found = self._query_one(http, realm, f"select * from Vendor where DisplayName = '{safe}'")
        if found:
            return found["Id"]
        response = http.post(f"/v3/company/{realm}/vendor", json={"DisplayName": name})
        response.raise_for_status()
        return response.json()["Vendor"]["Id"]

    def _account(self, http: httpx.Client, realm: str, name: str) -> str:
        safe = name.replace("'", "\\'")
        found = self._query_one(http, realm, f"select * from Account where FullyQualifiedName = '{safe}'")
        if found:
            return found["Id"]
        response = http.post(f"/v3/company/{realm}/account", json={"Name": name.split(":")[-1], "AccountType": "Cost of Goods Sold"})
        response.raise_for_status()
        return response.json()["Account"]["Id"]

    def push_invoice(self, db: Session, credentials: dict, invoice: Invoice) -> str:
        """Create the bill for one complete invoice. Returns the QuickBooks bill id (the existing one if pushed before)."""
        if invoice.external_refs.get("qbo"):
            return invoice.external_refs["qbo"]
        if invoice.status != "complete":
            raise ValueError("Only an invoice that has been checked is pushed.")
        realm = credentials.get("realmId") or credentials.get("realm_id")
        if not realm:
            raise ValueError("QuickBooks didn't say which company; reconnect it.")
        supplier = db.get(Supplier, invoice.supplier_id)
        with self._client(credentials) as http:
            vendor_id = self._vendor(http, realm, supplier.name if supplier else "Supplier")
            lines = []
            for line in db.query(InvoiceLine).filter(InvoiceLine.invoice_id == invoice.id).order_by(InvoiceLine.line_no):
                item = db.get(PriceItem, line.item_id) if line.item_id else None
                account = ACCOUNTS.get(item.category if item else "food", ACCOUNTS["food"])
                lines.append({
                    "DetailType": "AccountBasedExpenseLineDetail", "Amount": round(line.extended, 2), "Description": line.description,
                    "AccountBasedExpenseLineDetail": {"AccountRef": {"value": self._account(http, realm, account)}},
                })
            bill = {"VendorRef": {"value": vendor_id}, "TxnDate": invoice.invoice_date.isoformat(), "DocNumber": (invoice.reference or "")[:21],
                    "Line": lines, "PrivateNote": "Pushed by FullHouse"}
            response = http.post(f"/v3/company/{realm}/bill", json=bill)
            response.raise_for_status()
            bill_id = response.json()["Bill"]["Id"]
        invoice.external_refs = {**(invoice.external_refs or {}), "qbo": bill_id}
        db.flush()
        return bill_id
