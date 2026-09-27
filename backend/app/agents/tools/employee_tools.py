import json
from datetime import date, timedelta

from anthropic import beta_tool
from sqlalchemy.orm import Session

from app.agents import crud as agent_crud
from app.agents.models import AgentRun
from app.restaurant import crud as restaurant_crud


def build_tools(db: Session, run: AgentRun, agent_definition_id: str) -> list:
    @beta_tool
    def list_staff() -> str:
        """List all staff members with their roles."""
        staff = restaurant_crud.list_staff(db)
        return json.dumps([{"id": s.id, "name": s.name, "role": s.role} for s in staff])

    @beta_tool
    def get_upcoming_shifts(days_ahead: int = 7) -> str:
        """List scheduled shifts for the next N days, to spot coverage gaps.

        Args:
            days_ahead: How many days ahead to look, starting today. Defaults to 7.
        """
        today = date.today()
        shifts = restaurant_crud.list_shifts_between(db, today, today + timedelta(days=days_ahead))
        return json.dumps(
            [
                {
                    "staff_id": s.staff_id,
                    "staff_name": s.staff.name if s.staff else None,
                    "date": s.date.isoformat(),
                    "start_time": s.start_time.isoformat(),
                    "end_time": s.end_time.isoformat(),
                    "role": s.role,
                }
                for s in shifts
            ]
        )

    @beta_tool
    def propose_shift_change(staff_id: str, date_iso: str, start_time_iso: str, end_time_iso: str, role: str, note: str = "") -> str:
        """Propose adding or changing a shift for a staff member. Queues the
        proposal for operator approval rather than writing it directly to
        the schedule.

        Args:
            staff_id: The id of the staff member.
            date_iso: The shift date, ISO format (YYYY-MM-DD).
            start_time_iso: Shift start time, ISO format (HH:MM).
            end_time_iso: Shift end time, ISO format (HH:MM).
            role: The role being covered during this shift.
            note: Optional note explaining why this change is recommended.
        """
        staff = next((s for s in restaurant_crud.list_staff(db) if s.id == staff_id), None)
        if not staff:
            return f"Error: no staff member with id {staff_id}"
        action = agent_crud.create_action(
            db,
            run_id=run.id,
            agent_definition_id=agent_definition_id,
            action_type="shift_change",
            payload={
                "staff_id": staff_id,
                "staff_name": staff.name,
                "date": date_iso,
                "start_time": start_time_iso,
                "end_time": end_time_iso,
                "role": role,
                "note": note,
            },
        )
        return f"Shift proposal {action.id} for {staff.name} on {date_iso} submitted for operator approval."

    return [list_staff, get_upcoming_shifts, propose_shift_change]
