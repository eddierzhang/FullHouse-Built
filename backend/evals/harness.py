"""Runs one scenario against one model and scores what the agent did.

Uses the real pipeline -- `execute_run`, the real tools, the real
appliers -- on a throwaway SQLite database per run, so a result reflects
what would happen in the app rather than in a mock of it.
"""

import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents import crud
from app.agents.appliers import ApplyError, apply_action, missing_inputs
from app.agents.models import AgentAction
from app.config import settings
from app.db.session import Base, engine_options
from evals.scenarios import Scenario, build_world


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Result:
    scenario: str
    provider: str
    model: str
    status: str
    checks: list[Check] = field(default_factory=list)
    tools_called: list[str] = field(default_factory=list)
    proposals: list[dict] = field(default_factory=list)
    seconds: float = 0.0
    tokens: int | None = None
    error: str | None = None

    @property
    def score(self) -> float:
        return sum(c.passed for c in self.checks) / len(self.checks) if self.checks else 0.0

    def to_dict(self) -> dict:
        data = asdict(self)
        data["score"] = round(self.score, 3)
        return data


def _dry_run(db, action: AgentAction) -> tuple[bool, str]:
    """Would approving this proposal actually apply? Applied, then rolled back."""
    if missing_inputs(action):
        return False, f"needs operator input: {', '.join(missing_inputs(action))}"
    try:
        result = apply_action(db, action)
        return True, result
    except ApplyError as exc:
        return False, str(exc)
    finally:
        db.rollback()


def configure_provider(provider: str, model: str | None) -> None:
    """Point the app's provider selection at this model for the next run."""
    settings.llm_provider = provider
    if provider == "ollama" and model:
        settings.ollama_model = model


def run_scenario(scenario: Scenario, provider: str, model: str | None = None) -> Result:
    configure_provider(provider, model)

    workdir = Path(tempfile.mkdtemp(prefix=f"eval-{scenario.id}-"))
    url = f"sqlite:///{workdir / 'eval.db'}"
    engine = create_engine(url, **engine_options(url))
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()

    result = Result(scenario=scenario.id, provider=provider, model=model or provider, status="not started")
    try:
        facts = scenario.setup(db, build_world(db))
        defn = crud.get_definition_by_key(db, scenario.agent_key)
        run = crud.create_run(db, defn.id, trigger_type="manual", input=scenario.prompt)

        from app.agents.runner import execute_run

        started = time.monotonic()
        try:
            execute_run(run.id, db=db)
        except Exception as exc:  # recorded on the run; scored below
            result.error = str(exc)
        result.seconds = round(time.monotonic() - started, 1)

        db.refresh(run)
        result.status = run.status
        result.tokens = run.tokens_used
        result.tools_called = [
            e.payload.get("tool") for e in crud.list_events(db, run.id) if e.type == "tool_call"
        ]
        actions = db.query(AgentAction).filter(AgentAction.run_id == run.id).all()
        result.proposals = [{"action_type": a.action_type, "payload": a.payload} for a in actions]

        result.checks = score(scenario, facts, result, db, actions)
    finally:
        db.close()
        engine.dispose()
    return result


def score(scenario: Scenario, facts: dict, result: Result, db, actions: list[AgentAction]) -> list[Check]:
    checks = [Check("completed", result.status == "succeeded", result.error or result.status)]

    missing = scenario.expected_tools - set(result.tools_called)
    checks.append(
        Check(
            "used expected tools",
            not missing,
            f"missing {sorted(missing)}" if missing else f"called {sorted(set(result.tools_called))}",
        )
    )

    if scenario.forbid_proposals:
        checks.append(
            Check("proposed nothing", not actions,
                  "correctly left things alone" if not actions else f"{len(actions)} unneeded proposal(s)")
        )

    if scenario.expected_proposal:
        expected = scenario.expected_proposal
        right = [
            a for a in actions
            if a.action_type == expected.action_type and expected.matches(a.payload or {}, facts)
        ]
        checks.append(
            Check("made the right proposal", bool(right),
                  f"found {expected.describe}" if right else f"expected {expected.describe}")
        )
        if right:
            ok, detail = _dry_run(db, right[0])
            checks.append(Check("proposal applies cleanly", ok, detail))
        else:
            checks.append(Check("proposal applies cleanly", False, "no matching proposal to apply"))

        wrong = [a for a in actions if a not in right]
        checks.append(
            Check("no stray proposals", not wrong,
                  "none" if not wrong else f"{len(wrong)} proposal(s) for the wrong thing")
        )

    return checks
