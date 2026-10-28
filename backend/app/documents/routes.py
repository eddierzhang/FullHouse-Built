"""Documents: upload anything, see what it was read as and what came of it, pick the type when the reader wasn't sure."""

from __future__ import annotations

import base64
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import storage
from app.auth.deps import current_user, manager
from app.auth.models import User
from app.db.session import get_db
from app.documents.models import DOC_TYPES, Document
from app.platform import flags, jobs
from app.restaurant.inventory import scope_for

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

MAX_BYTES = 15 * 1024 * 1024
MEDIA = {"application/pdf", "image/jpeg", "image/png", "image/webp", "text/plain", "text/csv"}


def _scope(db: Session, user: User) -> str:
    restaurant_id = scope_for(db, user)
    if restaurant_id is None:
        raise HTTPException(409, "Finish setup first.")
    if not flags.enabled(db, restaurant_id, flags.V2_DOCUMENTS):
        raise HTTPException(404, "Documents aren't switched on for this restaurant.")
    return restaurant_id


class DocumentOut(BaseModel):
    id: str
    doc_type: str
    type_confidence: float | None
    filename: str | None
    media_type: str | None
    source: str
    status: str
    confidence: float | None
    result_table: str | None
    result_id: str | None
    note: str | None
    extracted_summary: dict
    file_url: str | None
    created_at: datetime
    updated_at: datetime


def _out(document: Document) -> DocumentOut:
    extracted = document.extracted or {}
    summary = {k: v for k, v in extracted.items() if k in ("supplier", "supplier_guess", "effective_date", "date_guess", "name", "title", "issuer",
                                                           "expires_on", "issued_on", "asset_name", "proposed", "unmatched", "low_confidence",
                                                           "classification", "error", "reader", "reference", "total")}
    summary["lines"] = len(extracted.get("lines") or [])
    return DocumentOut(
        id=document.id, doc_type=document.doc_type, type_confidence=document.type_confidence, filename=document.filename,
        media_type=document.media_type, source=document.source, status=document.status, confidence=document.confidence,
        result_table=document.result_table, result_id=document.result_id, note=document.note, extracted_summary=summary,
        file_url=storage.signed_url(document.file_key) if document.file_key else None, created_at=document.created_at, updated_at=document.updated_at,
    )


@router.get("", response_model=list[DocumentOut])
def list_documents(user: User = Depends(current_user), db: Session = Depends(get_db)):
    restaurant_id = _scope(db, user)
    rows = db.query(Document).filter(Document.restaurant_id == restaurant_id).order_by(Document.created_at.desc()).limit(200).all()
    return [_out(d) for d in rows]


class UploadIn(BaseModel):
    filename: str = Field(max_length=255)
    media_type: str = Field(max_length=80)
    #: The file, base64.
    content: str
    #: When the person already knows what it is; otherwise it is classified.
    doc_type: str | None = None


@router.post("", response_model=DocumentOut, status_code=201)
def upload(body: UploadIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    restaurant_id = _scope(db, user)
    media = body.media_type.split(";")[0].strip().lower()
    if media not in MEDIA:
        raise HTTPException(422, "Upload a PDF, a photo (JPEG or PNG), or a text file.")
    try:
        data = base64.b64decode(body.content, validate=False)
    except Exception as exc:
        raise HTTPException(422, "The file didn't decode.") from exc
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(422, "The file is empty or over 15 MB.")
    if body.doc_type and body.doc_type not in DOC_TYPES:
        raise HTTPException(422, f"Unknown document type. One of: {', '.join(DOC_TYPES)}.")
    key = storage.new_key(restaurant_id, body.doc_type or "upload", body.filename)
    storage.put(key, data, media)
    document = Document(restaurant_id=restaurant_id, doc_type=body.doc_type or "other", type_confidence=1.0 if body.doc_type else None,
                        file_key=key, filename=body.filename, media_type=media, source="upload", status="received")
    db.add(document)
    db.flush()
    jobs.enqueue(db, "extract", f"extract:{document.id}", {"document_id": document.id, "doc_type": body.doc_type}, restaurant_id=restaurant_id)
    db.commit()
    return _out(document)


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(document_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    restaurant_id = _scope(db, user)
    document = db.get(Document, document_id)
    if document is None or document.restaurant_id != restaurant_id:
        raise HTTPException(404, "No such document.")
    return _out(document)


class TypeIn(BaseModel):
    doc_type: str


@router.post("/{document_id}/type", response_model=DocumentOut)
def set_type(document_id: str, body: TypeIn, user: User = Depends(manager), db: Session = Depends(get_db)):
    """The reviewer says what it is; it is read again as that."""
    restaurant_id = _scope(db, user)
    document = db.get(Document, document_id)
    if document is None or document.restaurant_id != restaurant_id:
        raise HTTPException(404, "No such document.")
    if body.doc_type not in DOC_TYPES:
        raise HTTPException(422, f"Unknown document type. One of: {', '.join(DOC_TYPES)}.")
    document.doc_type, document.type_confidence, document.status = body.doc_type, 1.0, "received"
    jobs.enqueue(db, "extract", f"extract:{document.id}:{body.doc_type}", {"document_id": document.id, "doc_type": body.doc_type}, restaurant_id=restaurant_id)
    db.commit()
    return _out(document)
