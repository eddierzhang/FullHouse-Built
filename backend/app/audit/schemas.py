from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChangeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    ts: datetime
    entity_type: str
    entity_id: str
    operation: str
    before: dict | None
    after: dict | None
    changed_fields: list
    actor_type: str
    actor_id: str | None
    agent_run_id: str | None
    agent_action_id: str | None
    note: str | None


class ChangePage(BaseModel):
    """A page of changes plus the total, so a UI can paginate without guessing."""

    total: int
    limit: int
    offset: int
    items: list[ChangeOut]


class RevertRequest(BaseModel):
    force: bool = Field(
        default=False,
        description=(
            "Revert even though the entity has changed since. Off by default: "
            "a blind revert would silently discard the newer edits."
        ),
    )
    note: str | None = Field(default=None, description="Why this is being reverted.")


class RevertOut(BaseModel):
    reverted_change_id: int
    operation: str
    description: str
    resulting_change_ids: list[int]
