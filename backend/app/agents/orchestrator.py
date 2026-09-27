import threading

from anthropic import beta_tool
from sqlalchemy.orm import Session, sessionmaker

from app.agents import crud as agent_crud
from app.agents.broker import broker
from app.agents.models import AgentDefinition, AgentRun

# Parallel delegations each append a "delegation" event to the same Boss
# run. Event sequence numbers are read-then-written, so two workers doing
# that at once would collide on the same seq.
_boss_events_lock = threading.Lock()


def build_delegate_tools(db: Session, boss_run: AgentRun, boss_defn: AgentDefinition) -> list:
    """Build one delegate_to_<key> tool per subagent key in the Boss's
    tool_allowlist. Calling one creates a child AgentRun (linked via
    parent_run_id), runs it to completion and returns its output_summary as
    the tool result.

    Delegations are parallel-safe: a provider may run several from one
    model turn at the same time. That is why each child works in its own
    session rather than the Boss's -- a Session is not thread-safe -- and
    why nothing here touches `db` or `boss_run` once the tools are built.
    """
    from app.agents.runner import execute_run  # local import: runner imports registry, which imports this module

    # The same database the Boss is using (which, in tests and evals, is
    # not the app's default engine).
    new_session = sessionmaker(bind=db.get_bind(), autoflush=False, autocommit=False)
    boss_run_id = boss_run.id
    child_depth = boss_run.depth + 1

    tools = []
    for subagent_key in boss_defn.tool_allowlist:
        subagent_defn = agent_crud.get_definition_by_key(db, subagent_key)
        if subagent_defn is None:
            continue

        def make_tool(defn_id: str = subagent_defn.id, key: str = subagent_defn.key,
                      name: str = subagent_defn.name, description: str = subagent_defn.description):
            @beta_tool(
                name=f"delegate_to_{key}",
                description=(
                    f"Delegate a task to the {name} subagent ({description}) and return its result. "
                    "Call several delegate tools in the same turn to run them in parallel."
                ),
            )
            def delegate(task: str) -> str:
                """
                Args:
                    task: A clear description of what to ask this subagent to do or report on.
                """
                with new_session() as child_db:
                    child_run = agent_crud.create_run(
                        child_db,
                        defn_id,
                        parent_run_id=boss_run_id,
                        depth=child_depth,
                        trigger_type="delegated",
                        input=task,
                    )
                    payload = {"child_run_id": child_run.id, "agent_key": key, "agent_name": name, "task": task}
                    with _boss_events_lock:
                        event = agent_crud.add_event(child_db, boss_run_id, "delegation", payload)
                    broker.publish(
                        boss_run_id,
                        {"type": "delegation", "seq": event.seq, "run_id": boss_run_id, "payload": payload},
                    )
                    try:
                        return execute_run(child_run.id, db=child_db)
                    except Exception as exc:  # noqa: BLE001 - report the failure back to the Boss's own loop
                        return f"Error: {name} run failed: {exc}"

            delegate.parallel_safe = True
            return delegate

        tools.append(make_tool())
    return tools
