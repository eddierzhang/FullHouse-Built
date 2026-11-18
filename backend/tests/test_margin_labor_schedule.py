"""Module D against the database, the schedule (spec sections 8.4, 8.5, 11 and 12): a draft generated from the people
needed, edited, locked, regenerated around the lock and published, with wages kept to those with wage access."""

import re

import json
from datetime import timedelta
from decimal import Decimal as D

from app.agents.models import AgentAction
from app.auth.models import ADMIN, MEMBER
from app.audit.models import ChangeLog
from app.margin_labor import fixtures, labor, labor_read, schedule, scheduling
from app.margin_labor.models import EmployeeWage, LaborRules, Schedule, ScheduleShift
from app.platform import flags, jobs
from app.platform.models import Event
from test_margin_labor_labor import Floor
from test_restaurant_details import _account, _other_client, _second_restaurant

#: The rate 18.5 as a number of its own: not the seconds of a timestamp ("23:22:18.504720"), which follow a colon.
RATE = re.compile(r"(?<![\d:.])18\.5")

API = "/api/v1/margin-labor"
RULES = {"weekly_overtime_hours": 40, "daily_overtime_hours": 8, "min_rest_hours": 10, "min_shift_hours": 4, "max_shift_hours": 8, "max_hours_per_day": 10,
         "max_consecutive_days": 6, "break_after_hours": 6, "break_minutes": 30, "min_paid_hours": 3, "advance_notice_days": 14, "allow_on_call": False}


class Week(Floor):
    def __init__(self, client, db, rules=True):
        super().__init__(client, db)
        self.team = fixtures.seed_team(db, self.rid)
        db.commit()
        if rules:
            assert client.put(f"{API}/labor/rules", json=RULES).status_code == 200
        self.monday = labor_read.default_week(self.today)

    def generate(self):
        made = schedule.generate(self.db, self.rid, self.monday, seconds=6)
        self.db.commit()
        self.db.expire_all()
        return made

    def page(self, client=None):
        return (client or self.client).get(f"{API}/labor/weeks/{self.monday.isoformat()}").json()


def test_a_generated_draft_covers_the_people_needed_breaks_no_rule_and_is_one_proposal(client, db):
    w = Week(client, db)
    made = w.generate()
    assert (made.status, made.version, made.week_of) == ("draft", 1, w.monday) and made.notes["unset_rules"] == []
    body = w.page()["schedule"]
    assert body["violations"] == [] and body["status"] == "draft" and len(body["shifts"]) > 20
    assert all(s["start"] % 30 == 0 and 240 <= s["end"] - s["start"] <= 480 for s in body["shifts"])  # on the half hour, four to eight hours
    assert {s["role_key"] for s in body["shifts"]} == {"server", "line_cook"}
    assigned = [s for s in body["shifts"] if s["staff_id"]]
    names = {p["id"]: p["name"] for p in body["people"]}
    servers = {v.id for k, v in w.team.items() if fixtures.TEAM[k][0] == "Server"}
    assert all((s["staff_id"] in servers) == (s["role_key"] == "server") for s in assigned)  # everyone works a role they hold
    assert len(assigned) + body["open_shifts"] == len(body["shifts"]) and len(names) == 9
    # The day view now shows who is scheduled against who is needed.
    friday = (w.monday + timedelta(days=4)).isoformat()
    six = next(s for s in w.page()["intervals"][friday]["server"] if s["label"] == "6:00" and s["need"] == 4)
    assert six["scheduled"] >= 3
    proposals = db.query(AgentAction).filter_by(action_type="schedule_draft").all()
    assert len(proposals) == 1 and proposals[0].id == made.proposal_id and proposals[0].payload["week_of"] == w.monday.isoformat()
    assert db.query(Event).filter_by(type="schedule.generated").count() == 1


def test_flow_edit_a_shift_lock_it_regenerate_around_it_and_publish(client, db):
    w = Week(client, db)
    made = w.generate()
    before = w.page()["schedule"]
    target = next(s for s in before["shifts"] if s["role_key"] == "server" and s["staff_id"] and s["end"] - s["start"] >= 300)
    # Edit: end it an hour earlier, and lock it.
    edited = client.patch(f"{API}/labor/shifts/{target['id']}", json={"schedule_id": made.id, "end": target["end"] - 60, "locked": True})
    assert edited.status_code == 200
    kept = next(s for s in edited.json()["shifts"] if s["id"] == target["id"])
    assert (kept["end"], kept["locked"], kept["staff_id"]) == (target["end"] - 60, True, target["staff_id"])
    assert D(edited.json()["hours_total"]) < D(before["hours_total"])  # the totals follow each edit

    # Regenerate: the locked shift is exactly as it was; everything else is scheduled around it, as version 2.
    again = w.generate()
    after = w.page()["schedule"]
    assert again.id == made.id and after["version"] == 2
    assert next(s for s in after["shifts"] if s["id"] == target["id"]) == kept
    assert target["id"] in {s["id"] for s in after["shifts"]} and len({s["id"] for s in after["shifts"]} & {s["id"] for s in before["shifts"]}) == 1
    assert after["violations"] == [] and db.query(AgentAction).filter_by(action_type="schedule_draft").count() == 1  # still one proposal for the week
    assert db.get(AgentAction, again.proposal_id).payload["version"] == 2

    # Publish: the week starts inside the 14 days' notice the owner set, so it warns first and waits for a yes.
    asked = client.post(f"{API}/labor/schedules/{made.id}/publish", json={}).json()
    assert asked["published"] is False and {"kind": "notice", "days": (w.monday - w.today).days, "rule": 14} in asked["warnings"]
    assert db.get(Schedule, made.id).status == "draft"
    done = client.post(f"{API}/labor/schedules/{made.id}/publish", json={"confirm": True}).json()
    assert done["published"] is True and done["schedule"]["status"] == "published" and done["schedule"]["published_at"]
    db.expire_all()
    proposal = db.get(AgentAction, made.proposal_id)
    assert proposal.status == "approved" and "Nobody was notified" in proposal.applied_result
    assert db.query(Event).filter_by(type="schedule.published").count() == 1
    from app.ordering.models import Outbox

    assert db.query(Outbox).count() == 0  # nothing is sent to anyone
    # The export says who works when, and holds no wage.
    csv_text = client.get(f"{API}/labor/schedules/{made.id}/export.csv")
    assert csv_text.status_code == 200 and csv_text.text.splitlines()[0] == "Date,Day,Person,Role,Start,End,Break minutes,Kind,Note"
    assert "Ana Rivera" in csv_text.text or "Ben Tran" in csv_text.text
    assert "15.00" not in csv_text.text and "rate" not in csv_text.text.lower()
    # A later edit puts the week back in draft: what was published is no longer what is here.
    assert client.delete(f"{API}/labor/shifts/{target['id']}?schedule_id={made.id}").json()["status"] == "draft"


def test_cost_is_paid_hours_times_rate_with_the_roles_median_for_an_unknown_rate(client, db):
    w = Week(client, db)
    made = schedule.generate(db, w.rid, w.monday, seconds=4)
    db.query(ScheduleShift).filter_by(schedule_id=made.id).delete()
    db.commit()
    ana, ivy = w.team["Ana Rivera"], w.team["Ivy Chen"]
    day = w.monday.isoformat()
    client.post(f"{API}/labor/shifts", json={"schedule_id": made.id, "date": day, "start": 600, "end": 1080, "role_key": "server", "staff_id": ana.id})   # 8 hours less a 30-minute break
    body = client.post(f"{API}/labor/shifts", json={"schedule_id": made.id, "date": day, "start": 960, "end": 1260, "role_key": "line_cook", "staff_id": ivy.id}).json()  # 5 hours, no break
    # Ana: 7.5 paid hours at $15.00. Ivy has no rate on file: the cooks' median, $21.00, for 5 hours.
    assert body["hours_total"] == "12.50" and body["cost_total"] == "217.50" and body["uncosted_shifts"] == 0
    net = D(body["net_expected"])
    assert net == D("20320.00") and body["labor_pct"] == str((D("217.50") / net).quantize(D("0.000001")))  # 1,016 covers at $20
    assert [s["paid_minutes"] for s in body["shifts"]] == [450, 300]
    for bad in ({"schedule_id": made.id, "date": day, "start": 615, "end": 900, "role_key": "server"}, {"schedule_id": made.id, "date": day, "start": 900, "end": 600, "role_key": "server"},
                {"schedule_id": made.id, "date": (w.monday + timedelta(days=9)).isoformat(), "start": 600, "end": 900, "role_key": "server"},
                {"schedule_id": made.id, "date": day, "start": 600, "end": 900, "role_key": "server", "staff_id": "nobody"}):
        assert client.post(f"{API}/labor/shifts", json=bad).status_code == 422
    # A hand edit that breaks a rule is allowed, and said: Ana again nine hours after she finishes.
    warned = client.post(f"{API}/labor/shifts", json={"schedule_id": made.id, "date": (w.monday + timedelta(days=1)).isoformat(), "start": 180, "end": 480, "role_key": "server", "staff_id": ana.id}).json()
    assert [(v["person"], v["rule"]) for v in warned["violations"]] == [("Ana Rivera", "leaves too little rest between shifts")]
    assert len(warned["shifts"]) == 3  # the four refused edits left nothing behind
    asked = client.post(f"{API}/labor/schedules/{made.id}/publish", json={}).json()
    assert any(wn["kind"] == "rule" for wn in asked["warnings"]) and asked["published"] is False


def test_a_wage_reaches_only_those_with_wage_access_and_never_a_log(client, db):
    w = Week(client, db)
    made = w.generate()
    _account(db, "cook@casaluna.example.com", MEMBER, w.rid)
    _account(db, "gm@casaluna.example.com", ADMIN, w.rid)
    staff, gm = _other_client("cook@casaluna.example.com"), _other_client("gm@casaluna.example.com")

    owner_view = w.page()
    assert owner_view["wage_access"] is True and owner_view["schedule"]["cost_total"] is not None and owner_view["schedule"]["labor_pct"] is not None
    assert gm.get(f"{API}/labor/weeks/{w.monday.isoformat()}").json()["schedule"]["cost_total"] == owner_view["schedule"]["cost_total"]
    seen = staff.get(f"{API}/labor/weeks/{w.monday.isoformat()}")
    text = seen.text
    assert seen.status_code == 200 and seen.json()["wage_access"] is False and seen.json()["schedule"]["hours_total"] == owner_view["schedule"]["hours_total"]
    assert "hourly_rate" not in text and "cost" not in text  # no rate, and no cost of any kind: hours only
    assert not {"cost_total", "labor_pct", "uncosted_shifts", "net_expected"} & set(seen.json()["schedule"])
    for path in ("/labor/standards", f"/labor/schedules/{made.id}/export.csv", "/labor/rules", "/digest-cards"):
        assert "hourly_rate" not in staff.get(f"{API}{path}").text
    assert staff.get(f"{API}/labor/team").status_code == 403
    ana = w.team["Ana Rivera"].id
    assert staff.put(f"{API}/labor/team/{ana}", json={"hourly_rate": "99", "set_rate": True}).status_code == 403
    for call in (lambda: staff.post(f"{API}/labor/weeks/{w.monday.isoformat()}/generate"), lambda: staff.post(f"{API}/labor/schedules/{made.id}/publish", json={"confirm": True}),
                 lambda: staff.patch(f"{API}/labor/shifts/{owner_view['schedule']['shifts'][0]['id']}", json={"schedule_id": made.id, "locked": True}),
                 lambda: staff.put(f"{API}/labor/rules", json=RULES)):
        assert call().status_code == 403
    assert gm.put(f"{API}/labor/rules", json=RULES).status_code == 403  # a manager edits a schedule; only the owner confirms the rules

    team = client.get(f"{API}/labor/team").json()
    assert next(p for p in team if p["name"] == "Ana Rivera")["hourly_rate"] == "15.0000" and next(p for p in team if p["name"] == "Ivy Chen")["hourly_rate"] is None
    saved = client.put(f"{API}/labor/team/{ana}", json={"role_keys": ["server", "host"], "max_weekly_hours": 32, "desired_weekly_hours": 28, "hourly_rate": "18.50", "set_rate": True,
                                                        "availability": [{"weekday": 4, "start_time": "16:00", "end_time": "23:00"}]})
    mine = next(p for p in saved.json() if p["staff_id"] == ana)
    assert (mine["hourly_rate"], mine["role_keys"], mine["max_weekly_hours"], mine["availability"]) == ("18.5000", ["server", "host"], 32, [{"weekday": 4, "start_time": "16:00", "end_time": "23:00"}])
    assert db.query(EmployeeWage).filter_by(staff_id=ana).one().hourly_rate == D("18.5000")
    # The change is on record, with who made it and not what it is; the rate is in no audit row and no event.
    changed = db.query(Event).filter_by(type="wage.changed").one()
    assert changed.aggregate_id == ana and changed.payload == {"changed_by": changed.payload["changed_by"]} and "@" in changed.payload["changed_by"]
    assert db.query(ChangeLog).filter(ChangeLog.entity_type == "employee_wages").count() == 0
    for row in db.query(ChangeLog):
        assert not RATE.search(json.dumps([row.before, row.after], default=str)) and "hourly_rate" not in json.dumps([row.before, row.after], default=str)
    for event in db.query(Event):
        assert not RATE.search(json.dumps(event.payload, default=str))
    # A rate left out of a save is left as it is.
    client.put(f"{API}/labor/team/{ana}", json={"role_keys": ["server"]})
    assert db.query(EmployeeWage).filter_by(staff_id=ana).one().hourly_rate == D("18.5000")
    # Availability and the weekly limit now bind the solver: Ana works Fridays only, from 4, and never over 32 hours.
    w.generate()
    hers = [s for s in w.page()["schedule"]["shifts"] if s["staff_id"] == ana]
    assert all(s["date"] == (w.monday + timedelta(days=4)).isoformat() and s["start"] >= 960 for s in hers)


def test_rules_are_empty_until_the_owner_confirms_them_and_an_unset_rule_is_not_enforced(client, db):
    w = Week(client, db, rules=False)
    assert client.get(f"{API}/labor/rules").json()["confirmed_at"] is None and all(client.get(f"{API}/labor/rules").json()[n] is None for n in scheduling.RULE_NAMES)
    made = w.generate()
    assert made.notes["unset_rules"] == list(scheduling.RULE_NAMES)  # the draft lists what was not checked
    assert w.page()["schedule"]["unset_rules"] == list(scheduling.RULE_NAMES)
    assert client.post(f"{API}/labor/schedules/{made.id}/publish", json={}).json()["warnings"] in ([], [{"kind": "open_shifts", "count": w.page()["schedule"]["open_shifts"]}])  # no notice rule: no notice warning
    saved = client.put(f"{API}/labor/rules", json={**RULES, "advance_notice_days": None, "minor_max_hours_per_day": 6, "minor_latest_end": "22:00"})
    assert saved.status_code == 200 and saved.json()["confirmed_by"] and saved.json()["minor_latest_end"] == "22:00" and saved.json()["advance_notice_days"] is None
    assert schedule.rules_for(db, w.rid).unset() == ["advance_notice_days"] and schedule.rules_for(db, w.rid).minor_rules == {"max_hours_per_day": 6.0, "latest_end": "22:00"}
    assert client.put(f"{API}/labor/rules", json={"min_shift_hours": 9, "max_shift_hours": 6}).status_code == 422
    row = db.query(LaborRules).filter_by(restaurant_id=w.rid).one()
    row.confirmed_at = None  # saved, but not confirmed: nothing is enforced
    db.commit()
    assert schedule.rules_for(db, w.rid).unset() == list(scheduling.RULE_NAMES)


def test_generate_is_a_job_with_a_status_and_another_restaurant_sees_none_of_it(client, db):
    w = Week(client, db)
    queued = client.post(f"{API}/labor/weeks/{w.monday.isoformat()}/generate").json()
    assert queued["week_of"] == w.monday.isoformat()
    assert client.get(f"{API}/jobs/{queued['job_id']}").json() == {"job_id": queued["job_id"], "status": "queued", "error": None}
    assert client.post(f"{API}/labor/weeks/{w.monday.isoformat()}/generate").json()["job_id"] == queued["job_id"]  # asking twice queues it once
    jobs.drain(db, queues=("derive",))
    db.commit()
    assert client.get(f"{API}/jobs/{queued['job_id']}").json()["status"] == "done"
    made = db.query(Schedule).filter_by(restaurant_id=w.rid).one()
    # Approving the proposal in Recommendations publishes, as the Publish button does.
    approved = client.post(f"/api/v1/agents/actions/{made.proposal_id}/approve")
    assert approved.status_code == 200 and "Nobody was notified" in approved.json()["applied_result"]
    db.expire_all()
    assert db.get(Schedule, made.id).status == "published"

    other, b = _second_restaurant(db)
    flags.set_flag(db, other.id, flags.ML_LABOR, True)
    db.commit()
    shift = db.query(ScheduleShift).filter_by(schedule_id=made.id).first()
    assert b.get(f"{API}/jobs/{queued['job_id']}").status_code == 404 and b.get(f"{API}/labor/schedules/{made.id}/export.csv").status_code == 404
    assert b.post(f"{API}/labor/schedules/{made.id}/publish", json={"confirm": True}).status_code == 404
    assert b.patch(f"{API}/labor/shifts/{shift.id}", json={"schedule_id": made.id, "locked": True}).status_code == 404
    assert b.delete(f"{API}/labor/shifts/{shift.id}?schedule_id={made.id}").status_code == 404
    assert b.put(f"{API}/labor/team/{w.team['Ana Rivera'].id}", json={"role_keys": ["server"]}).status_code == 404
    assert b.get(f"{API}/labor/team").json() == [] and b.get(f"{API}/labor/weeks/next").json()["schedule"] is None
