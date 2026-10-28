from sqlalchemy.orm import Session

from app.agents.models import AgentDefinition, AgentRun
from app.agents.tools import (
    customer_tools,
    employee_tools,
    inventory_tools,
    marketing_tools,
    price_tools,
    profit_tools,
    supply_chain_tools,
)

#: Every tool the agent has, by the part of the restaurant it covers. Order
#: is the order the agent is offered them in.
TOOL_AREAS = {
    "inventory": inventory_tools.build_tools,
    "suppliers": supply_chain_tools.build_tools,
    "staff": employee_tools.build_tools,
    "menu": marketing_tools.build_tools,
    "profit": profit_tools.build_tools,
    "customers": customer_tools.build_tools,
    "prices": price_tools.build_tools,
}


def resolve_tools(db: Session, run: AgentRun, defn: AgentDefinition) -> list:
    """All of the restaurant's tools, each tagged with its `area`.

    `tool_allowlist`, when set, narrows it to those areas -- empty means all.
    """
    wanted = set(defn.tool_allowlist or ()) or set(TOOL_AREAS)
    tools = []
    for area, build in TOOL_AREAS.items():
        if area not in wanted:
            continue
        for tool in build(db, run, defn.id):
            tool.area = area
            tools.append(tool)
    return tools
