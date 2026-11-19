"""Uploading a restaurant's own data as CSV, one kind a section: suppliers, dishes, the team, supplier invoices.
Stock has its own import (`/restaurant/inventory-items/import`), and sales orders, clock-ins and payouts theirs
(`/margin-labor/data/import`)."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import manager
from app.auth.models import User
from app.db.session import get_db
from app.restaurant import inventory, section_import

router = APIRouter(prefix="/api/v1/imports", tags=["imports"])


class ImportIn(BaseModel):
    """A spreadsheet as CSV text (the browser reads the file)."""

    csv: str = Field(max_length=1_000_000)
    #: Rows that match something already here: update it, rather than skip the row.
    update_existing: bool = False
    #: Check and preview only; nothing is saved.
    dry_run: bool = True


def _kind(kind: str) -> str:
    if kind not in section_import.SPECS:
        raise HTTPException(404, "There is no import of that kind.")
    return kind


@router.get("/{kind}/template", response_class=PlainTextResponse)
def template(kind: str, user: User = Depends(manager)) -> PlainTextResponse:
    return PlainTextResponse(section_import.template(_kind(kind)), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="fullhouse-{kind}-template.csv"'})


@router.post("/{kind}")
def run(kind: str, body: ImportIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """A dry run previews what each row would do. A real run saves every row or, if any has a problem, none."""
    kind = _kind(kind)
    restaurant_id = inventory.scope_for(db, user)  # the tenant comes from the signed-in account, never the file
    try:
        plan = section_import.plan(db, restaurant_id, kind, body.csv, body.update_existing)
    except section_import.ImportRejected as exc:
        raise HTTPException(422, str(exc)) from exc
    saved, result = False, {}
    if not body.dry_run and plan.count("error") == 0:
        try:
            result = section_import.carry_out(db, restaurant_id, plan)
            db.commit()
        except Exception:
            db.rollback()
            raise
        saved = True
    spec = section_import.SPECS[kind]
    return {
        "kind": kind, "label": spec["label"], "one": spec["one"], "saved": saved,
        "rows": [{"line": r.line, "name": r.name, "status": r.status, "messages": r.messages} for r in plan.rows],
        "new": plan.count("new"), "updated": plan.count("update"), "skipped": plan.count("skip"), "errors": plan.count("error"),
        "ignored_columns": plan.ignored_columns, "groups": plan.groups, **result,
    }


class AutoIn(BaseModel):
    csv: str = Field(max_length=40_000_000)
    #: What the file is, when the person has said; otherwise it is worked out from the column names.
    kind: str | None = None
    update_existing: bool = False
    #: Check and report only; nothing is saved.
    dry_run: bool = False


@router.post("")
def auto(body: AutoIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Take any CSV the app knows how to read: work out what it is from its column names, check every row, and
    enter the rows. Nothing is entered when the kind is not clear (under 0.8 sure, and not named by the person) or
    when a row has a problem: the reply then says what to choose or fix. The same file twice changes nothing."""
    restaurant_id = inventory.scope_for(db, user)
    kind, sure, went_by = (body.kind, 1.0, []) if body.kind else section_import.detect(body.csv)
    if kind is not None and kind not in section_import.KINDS:
        raise HTTPException(404, "There is no import of that kind.")
    choices = [{"kind": k, "label": label, "section": section} for k, (label, section) in section_import.KINDS.items()]
    base = {"kind": kind, "confidence": sure, "went_by": went_by, "choices": choices, "saved": False, "rows": [], "new": 0, "updated": 0, "skipped": 0, "errors": 0,
            "label": section_import.KINDS[kind][0] if kind else None, "section": section_import.KINDS[kind][1] if kind else None, "needs_kind": False}
    if kind is None or sure < 0.8:
        return {**base, "needs_kind": True}
    write = not body.dry_run
    try:
        if kind in section_import.SPECS:
            if len(body.csv) > 1_000_000:
                raise section_import.ImportRejected("That file is over 1 MB. Split it into smaller files and import each.")
            plan = section_import.plan(db, restaurant_id, kind, body.csv, body.update_existing)
            out = {**base, "rows": [{"line": r.line, "name": r.name, "status": r.status, "messages": r.messages} for r in plan.rows],
                   "new": plan.count("new"), "updated": plan.count("update"), "skipped": plan.count("skip"), "errors": plan.count("error"),
                   "ignored_columns": plan.ignored_columns, "groups": plan.groups}
            if write and plan.count("error") == 0:
                out.update(section_import.carry_out(db, restaurant_id, plan))
                db.commit()
                out["saved"] = True
            return out
        if kind == "inventory":
            from app.restaurant import importer

            if len(body.csv) > 1_000_000:
                raise section_import.ImportRejected("That file is over 1 MB. Split it into smaller files and import each.")
            stock = importer.plan(db, restaurant_id, body.csv, body.update_existing)
            out = {**base, "rows": [{"line": r.line, "name": r.name, "status": r.status, "messages": r.messages} for r in stock.rows],
                   "new": stock.count("new"), "updated": stock.count("update"), "skipped": stock.count("skip"), "errors": stock.count("error"),
                   "new_suppliers": stock.new_suppliers}
            if write and stock.count("error") == 0:
                importer.carry_out(db, restaurant_id, stock)
                db.commit()
                out["saved"] = True
            return out
        # Sales orders, clock-ins and payouts: each row is matched on its own id, rows that can't be read are left
        # out and listed, and the rest are entered.
        from app.margin_labor import csv_import
        from app.platform import jobs

        if not write:
            return {**base, "note": "Each row is matched on its own id when the file is imported."}
        result = csv_import.IMPORTERS[kind](db, restaurant_id, body.csv)
        db.commit()
        jobs.drain(db, queues=("derive",), limit=400)
        db.commit()
        done = result.as_dict()
        problems = done.get("problems") or []
        return {**base, "saved": True, "new": done.get("created", 0), "updated": done.get("updated", 0), "skipped": done.get("unchanged", 0), "errors": 0,
                "left_out": len(problems), "rows": [{"line": p.get("line"), "name": None, "status": "skip", "messages": [p.get("problem", "")]} for p in problems[:50]],
                "orders": done.get("orders")}
    except (section_import.ImportRejected, ValueError) as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
