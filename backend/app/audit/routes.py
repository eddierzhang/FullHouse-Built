from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import crud, schemas
from app.audit.context import note_context
from app.audit.models import ChangeLog
from app.audit.revert import RevertConflict, RevertError, revert_change
from app.db.session import get_db

router = APIRouter(prefix="/api/v1/changes", tags=["changes"])


@router.get("", response_model=schemas.ChangePage)
def list_changes(
    db: Session = Depends(get_db),
    entity_type: str | None = None,
    entity_id: str | None = None,
    operation: str | None = Query(default=None, pattern="^(insert|update|delete)$"),
    actor_type: str | None = Query(default=None, pattern="^(human|agent|system)$"),
    actor_id: str | None = None,
    agent_run_id: str | None = None,
    agent_action_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Changes newest first.

    Filter by entity to audit one record, or by `agent_run_id` to see
    everything a single agent run caused.
    """
    total, rows = crud.query_changes(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        operation=operation,
        actor_type=actor_type,
        actor_id=actor_id,
        agent_run_id=agent_run_id,
        agent_action_id=agent_action_id,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return schemas.ChangePage(total=total, limit=limit, offset=offset, items=rows)


@router.get("/entity/{entity_type}/{entity_id}", response_model=list[schemas.ChangeOut])
def entity_history(entity_type: str, entity_id: str, db: Session = Depends(get_db)):
    """One entity's full timeline, oldest first."""
    return crud.entity_history(db, entity_type, entity_id)


@router.get("/{change_id}", response_model=schemas.ChangeOut)
def get_change(change_id: int, db: Session = Depends(get_db)):
    change = crud.get_change(db, change_id)
    if change is None:
        raise HTTPException(404, "Change not found")
    return change


@router.post("/{change_id}/revert", response_model=schemas.RevertOut)
def revert(change_id: int, payload: schemas.RevertRequest | None = None, db: Session = Depends(get_db)):
    """Undo a recorded change.

    The undo is itself a change: it replays through the same listener and
    lands in the log alongside the original. History is never rewritten.
    """
    payload = payload or schemas.RevertRequest()
    change = crud.get_change(db, change_id)
    if change is None:
        raise HTTPException(404, "Change not found")

    note = payload.note or f"Revert of change {change_id}"
    high_water = db.query(ChangeLog.id).order_by(ChangeLog.id.desc()).limit(1).scalar() or 0

    try:
        with note_context(note):
            description = revert_change(db, change, force=payload.force)
            db.commit()
    except RevertConflict as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except RevertError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    except IntegrityError as exc:
        # Typically reverting an insert for a row something else references.
        db.rollback()
        raise HTTPException(
            409, f"Revert would leave the database inconsistent: {exc.orig}"
        ) from exc
    except Exception:
        db.rollback()
        raise

    resulting = [
        row_id
        for (row_id,) in db.query(ChangeLog.id).filter(ChangeLog.id > high_water).order_by(ChangeLog.id)
    ]
    return schemas.RevertOut(
        reverted_change_id=change_id,
        operation=change.operation,
        description=description,
        resulting_change_ids=resulting,
    )
