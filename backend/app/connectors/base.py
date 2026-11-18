"""The connector interface. Every external system is an adapter implementing it."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from sqlalchemy.orm import Session

from app.connectors.models import Connection


@dataclass(frozen=True)
class TenantContext:
    """What a connector or a tool is allowed to touch: one restaurant, one session.

    Built by the registry or the tool layer, never by an adapter: an adapter cannot make
    its own context and so cannot reach another restaurant's rows.
    """

    restaurant_id: str
    db: Session
    connection: Connection | None = None
    #: Decrypted credentials, present only inside the worker.
    credentials: dict = field(default_factory=dict)


@dataclass
class SyncResult:
    rows: list[dict] = field(default_factory=list)
    next_cursor: str | None = None
    done: bool = True
    #: What the rows are: "invoices", "sales_days", "sales_items", "labor_days", "documents".
    capability: str = ""


@dataclass
class WebhookResult:
    external_id: str
    enqueue_sync: bool = True
    payload: dict = field(default_factory=dict)


class Connector(Protocol):
    kind: str
    provider: str
    auth: str
    capabilities: tuple[str, ...]

    def sync(self, ctx: TenantContext, cursor: str | None, until: datetime) -> SyncResult: ...

    def healthcheck(self, ctx: TenantContext) -> str: ...
