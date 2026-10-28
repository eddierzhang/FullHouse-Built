"""Invoices and documents by email.

Each restaurant gets its own address, `{slug}@in.<INBOUND_DOMAIN>`. The mail provider (Resend
inbound, or anything that can POST JSON) delivers to the webhook with a shared secret; the
webhook writes the raw delivery to the inbox once, saves each attachment as a document, and
queues an `ingest` job. Senders must be on the restaurant's allow-list, or the mail is kept
but nothing is read from it. Executables are refused.

`process` reads a document: a PDF or photo of an invoice becomes an invoice through the same
reader and checks as an upload; anything the reader can't make out goes to review.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
from datetime import date

from sqlalchemy.orm import Session

from app import storage
from app.config import settings
from app.connectors.models import Connection
from app.documents.models import COMPLETE, NEEDS_REVIEW, RECEIVED, Document
from app.restaurant.models import Restaurant

logger = logging.getLogger(__name__)

PROVIDER = "resend_inbound"
REFUSED_EXTENSIONS = (".exe", ".bat", ".cmd", ".sh", ".js", ".msi", ".scr", ".com", ".ps1", ".vbs", ".jar", ".apk", ".dmg")
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
ALLOWED_MEDIA = {"application/pdf", "image/jpeg", "image/png", "image/webp", "image/heic"}


def slugify(name: str | None) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (name or "restaurant").lower()).strip("-")[:30] or "restaurant"
    return base


def address_for(db: Session, restaurant: Restaurant) -> str | None:
    """The restaurant's inbound address, made once and kept on its connection row."""
    if not settings.inbound_domain:
        return None
    connection = connection_for(db, restaurant.id, create=True)
    return f"{connection.settings['slug']}@{settings.inbound_domain}"


def connection_for(db: Session, restaurant_id: str, create: bool = False) -> Connection | None:
    connection = db.query(Connection).filter(Connection.restaurant_id == restaurant_id, Connection.provider == PROVIDER).first()
    if connection is None and create:
        from app.connectors.registry import seed_providers

        seed_providers(db)
        restaurant = db.get(Restaurant, restaurant_id)
        slug = f"{slugify(restaurant.name if restaurant else None)}-{restaurant_id[:6]}"
        connection = Connection(restaurant_id=restaurant_id, kind="email", provider=PROVIDER,
                                settings={"slug": slug, "allowed_senders": []})
        db.add(connection)
        db.flush()
    return connection


def restaurant_for_address(db: Session, to_address: str) -> Connection | None:
    local = (to_address or "").split("@", 1)[0].strip().lower()
    if not local:
        return None
    for connection in db.query(Connection).filter(Connection.provider == PROVIDER):
        if connection.settings.get("slug", "").lower() == local:
            return connection
    return None


def sender_allowed(connection: Connection, sender: str) -> bool:
    sender = (sender or "").lower()
    address = re.search(r"<([^>]+)>", sender)
    sender = address.group(1) if address else sender
    allowed = [a.lower().strip() for a in connection.settings.get("allowed_senders", []) if a.strip()]
    if not allowed:
        return False
    domain = sender.rsplit("@", 1)[-1]
    return sender in allowed or f"@{domain}" in allowed or domain in allowed


def _parse(payload: dict) -> dict:
    """Resend's `email.received` shape or a plain one: from, to, subject, attachments[{filename, content_type, content}]."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    to = data.get("to")
    if isinstance(to, list):
        to = to[0] if to else ""
    attachments = []
    for att in data.get("attachments") or []:
        if not isinstance(att, dict):
            continue
        attachments.append({
            "filename": str(att.get("filename") or att.get("name") or "attachment")[:255],
            "content_type": str(att.get("content_type") or att.get("contentType") or "application/octet-stream").split(";")[0].lower(),
            "content": att.get("content") or att.get("content_base64") or "",
        })
    return {"from": str(data.get("from") or ""), "to": str(to or ""), "subject": str(data.get("subject") or "")[:200],
            "message_id": str(data.get("email_id") or data.get("message_id") or data.get("id") or ""), "attachments": attachments}


def receive(db: Session, payload: dict, external_id: str) -> dict:
    """One delivery: documents for each usable attachment, and an ingest job for each. Idempotent per delivery."""
    from app.platform import events, jobs

    mail = _parse(payload)
    connection = restaurant_for_address(db, mail["to"])
    if connection is None:
        logger.info("inbound mail for an unknown address %r dropped", mail["to"])
        return {"restaurant_id": None, "documents": 0, "reason": "unknown address"}
    restaurant_id = connection.restaurant_id
    if not sender_allowed(connection, mail["from"]):
        logger.info("inbound mail from %r not on the allow-list for %s", mail["from"], restaurant_id)
        connection.settings = {**connection.settings, "last_refused_sender": mail["from"][:200]}
        return {"restaurant_id": restaurant_id, "documents": 0, "reason": "sender not allowed"}
    made = 0
    for index, att in enumerate(mail["attachments"]):
        name = att["filename"]
        if name.lower().endswith(REFUSED_EXTENSIONS) or att["content_type"] not in ALLOWED_MEDIA:
            logger.info("inbound attachment %r refused (%s)", name, att["content_type"])
            continue
        try:
            data = base64.b64decode(att["content"], validate=False)
        except Exception:
            continue
        if not data or len(data) > MAX_ATTACHMENT_BYTES:
            continue
        doc_external = f"{external_id}:{index}"
        if db.query(Document).filter(Document.restaurant_id == restaurant_id, Document.external_id == doc_external).first():
            continue
        key = storage.new_key(restaurant_id, "inbox", name)
        storage.put(key, data, att["content_type"])
        document = Document(restaurant_id=restaurant_id, doc_type="other", file_key=key, filename=name, media_type=att["content_type"],
                            source="email", external_id=doc_external, status=RECEIVED,
                            note=f"From {mail['from'][:120]}: {mail['subject']}"[:500])
        db.add(document)
        db.flush()
        jobs.enqueue(db, "ingest", f"ingest:{document.id}", {"document_id": document.id}, restaurant_id=restaurant_id)
        made += 1
    connection.last_sync_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    events.emit(db, restaurant_id, "mail.received", "connections", connection.id, {"from": mail["from"][:120], "documents": made})
    db.flush()
    return {"restaurant_id": restaurant_id, "documents": made}


def process(db: Session, document_id: str, today: date | None = None) -> Document | None:
    """Read one document and make what it is: the document registry decides by kind."""
    from app.documents import registry

    return registry.process(db, document_id, today)
