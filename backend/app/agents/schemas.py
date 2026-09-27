from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AgentDefinitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    key: str
    name: str
    description: str
    role: str
    model: str
    tool_allowlist: list
    schedule_cron: str | None
    enabled: bool
    #: When the schedule next fires, in UTC. None when unscheduled or disabled.
    next_run_at: datetime | None = None


class AgentDefinitionUpdate(BaseModel):
    """Set `schedule_cron` to null to remove a schedule."""

    schedule_cron: str | None = None
    enabled: bool | None = None


class AgentEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    run_id: str
    seq: int
    ts: datetime
    type: str
    payload: dict


class AgentRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    agent_definition_id: str
    parent_run_id: str | None
    depth: int
    status: str
    trigger_type: str
    input: str | None
    output_summary: str | None
    error: str | None
    tokens_used: int | None = None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class AgentRunDetailOut(AgentRunOut):
    events: list[AgentEventOut]
    child_run_ids: list[str]


class TriggerRunRequest(BaseModel):
    agent_key: str
    input: str | None = None


class AgentActionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    run_id: str
    agent_definition_id: str
    action_type: str
    payload: dict
    status: str
    created_at: datetime
    decided_at: datetime | None
    applied_result: str | None = None
    # Whether any applier can carry this out, and anything the proposal left
    # for the operator to fill in. Lets the queue ask up front instead of
    # failing after Approve is pressed.
    appliable: bool = True
    needs_input: list[str] = []


class ApproveRequest(BaseModel):
    """Values the operator supplies at approval time.

    A proposal can describe an intent it has no number for -- "run a BOGO
    on Garlic Bread" carries no price -- so the operator names it here and
    it is merged into the payload before the applier runs.
    """

    overrides: dict = {}
