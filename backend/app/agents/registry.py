from sqlalchemy.orm import Session

from app.agents.models import AgentDefinition, AgentRun
from app.agents.tools import employee_tools, inventory_tools, marketing_tools, profit_tools, supply_chain_tools

SUBAGENT_TOOL_BUILDERS = {
    "inventory": inventory_tools.build_tools,
    "supply_chain": supply_chain_tools.build_tools,
    "employee_management": employee_tools.build_tools,
    "profit": profit_tools.build_tools,
    "marketing": marketing_tools.build_tools,
}


def resolve_tools(db: Session, run: AgentRun, defn: AgentDefinition) -> list:
    if defn.role == "boss":
        from app.agents.orchestrator import build_delegate_tools

        return build_delegate_tools(db, run, defn)

    builder = SUBAGENT_TOOL_BUILDERS.get(defn.key)
    if builder is None:
        return []
    return builder(db, run, defn.id)
