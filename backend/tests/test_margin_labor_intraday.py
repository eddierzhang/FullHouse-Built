"""Module D against the database, intraday (spec sections 8.6, 11 and 12): the reforecast during service, the
suggestion rules, and the Today page. A slow night in the fixture restaurant suggests cutting one server."""

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal as D

from app.auth.models import MEMBER
from app.margin_labor import clock, fixtures, intraday, labor
from app.margin_labor.models import ForecastInterval, LaborAdjustment, LaborRequirement, ScheduleShift
from app.notify.models import Notification
from app.platform import flags
from app.platform.models import Event, Job
from test_margin_labor_labor import Floor
from test_restaurant_details import _account, _other_client, _second_restaurant

API = "/api/v1/margin-labor"
RULES = {"weekly_overtime_hours": 40, "daily_overtime_hours": 8, "min_rest_hours": 10, "min_shift_hours": 4, "max_shift_hours": 8, "max_hours_per_day": 10,
         "max_consecutive_days": 7, "break_after_hours": 6, "break_minutes": 30, "min_paid_hours": 1, "advance_notice_days": 0, "allow_on_call": True}


class Night(Floor):
    """The fixture restaurant with this week's schedule published and today's sales in up to a given time."""

    def __init__(self, client, db, until=time(19, 0), factor=0.5):
        super().__init__(client, db)
        self.team = fixtures.seed_team(db, self.rid)
        db.commit()
        assert client.put(f"{API}/labor/rules", json=RULES).status_code == 200
        self.run()
        self.zone = labor.Service(db, self.rid).tz
        self.schedule = fixtures.seed_week_schedule(db, self.rid, self.team, self.today - timedelta(days=self.today.weekday()), self.tz)
        fixtures.seed_today(db, self.rid, self.today, self.tz, until, factor)
        db.commit()

    def at(self, hour, minute):
        return datetime.combine(self.today, time(hour, minute), tzinfo=self.zone).astimezone(timezone.utc)

    def run_at(self, hour, minute):
        day = intraday.reforecast(self.db, self.rid, self.at(hour, minute))
        self.db.commit()
        return day

    def open(self):
        self.db.expire_all()
        return self.db.query(LaborAdjustment).filter_by(restaurant_id=self.rid, status="proposed").all()

    def local(self, moment):
        return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).astimezone(self.zone).strftime("%H:%M")


def test_a_slow_night_revises_the_rest_of_the_day_and_suggests_cutting_one_server(client, db):
    n = Night(client, db)
    day = n.run_at(19, 15)
    # Half the usual covers all day: with most of the day gone, the pace is the multiplier.
    assert day.pace.adjusted and day.pace.weight == 1.0 and day.pace.pace == 0.5 and day.pace.multiplier == 0.5
    weekly = {r.interval_start: r.p50 for r in db.query(ForecastInterval).filter_by(restaurant_id=n.rid, business_date=n.today, kind="weekly", grain="interval", metric="covers")}
    revised = db.query(ForecastInterval).filter_by(restaurant_id=n.rid, business_date=n.today, kind="intraday").all()
    assert revised and all(n.local(r.interval_start) >= "19:15" for r in revised)  # only what is left of the day
    assert all(abs(r.p50 - weekly[r.interval_start] * 0.5) < 1e-6 and r.p10 <= r.p50 <= r.p90 for r in revised)
    needs = db.query(LaborRequirement).filter_by(restaurant_id=n.rid, business_date=n.today, kind="intraday", role_key="server").all()
    assert needs and all(n.local(r.interval_start) >= "19:00" for r in needs)

    [cut] = n.open()
    busy = n.today.weekday() in (4, 5)
    cal = n.team["Cal Ortiz"]
    assert (cut.kind, cut.role_key, n.local(cut.suggested_time), n.local(cut.expires_at)) == ("cut", "server", "19:45", "19:35")  # half an hour's notice; gone in 20 minutes
    assert db.get(ScheduleShift, cut.shift_id).staff_id == cal.id and cut.reason["person"] == "Cal O."
    assert cut.reason["why"] == "longest_on_shift" and cut.reason["heads"] == [4, 4, 4]  # the four dinner servers have the same week: the tie goes by name
    del busy
    assert all(h > need for h, need in zip(cut.reason["heads"], cut.reason["need_if_busy"]))  # more on than needed even if it gets busy
    assert cut.savings_estimate == D("28.0000")  # 7:45 to 9:30 is 1.75 hours, at Cal's $16.00
    told = db.query(Notification).filter_by(restaurant_id=n.rid, kind="labor_suggestion").one()
    assert told.title == "Cut one server at 7:45 pm." and told.link == "#labor/today"
    assert db.query(Event).filter_by(type="adjustment.proposed").count() == 1
    # Run again for the same moment: the same rows, and still one suggestion for the role.
    counts = (len(revised), len(needs))
    n.run_at(19, 15)
    assert (db.query(ForecastInterval).filter_by(restaurant_id=n.rid, business_date=n.today, kind="intraday").count(),
            db.query(LaborRequirement).filter_by(restaurant_id=n.rid, business_date=n.today, kind="intraday", role_key="server").count()) == counts
    assert len(n.open()) == 1 and db.query(Notification).filter_by(kind="labor_suggestion").count() == 1


def test_acceptance_no_suggestion_while_under_15_percent_of_the_day_has_passed(client, db):
    n = Night(client, db, until=time(11, 45), factor=0.25)
    day = n.run_at(12, 0)
    assert day.pace.elapsed_share < 0.15 and day.pace.adjusted is False and day.pace.multiplier == 1.0 and day.pace.pace < 0.5
    assert day.suggestions_on is False and day.suggestions_why == "early" and n.open() == []
    revised = db.query(ForecastInterval).filter_by(restaurant_id=n.rid, business_date=n.today, kind="intraday").all()
    weekly = {r.interval_start: r.p50 for r in db.query(ForecastInterval).filter_by(restaurant_id=n.rid, business_date=n.today, kind="weekly", grain="interval", metric="covers")}
    assert revised and all(r.p50 == weekly[r.interval_start] for r in revised)  # nothing is adjusted yet


def test_accepting_records_the_decision_and_moves_the_shift_and_a_dismissal_is_respected_for_an_hour(client, db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "allow_time_travel", True)
    n = Night(client, db)
    n.run_at(18, 15)
    [cut] = n.open()
    assert n.local(cut.suggested_time) == "18:45"
    at = n.at(18, 20).isoformat().replace("+00:00", "Z")
    dismissed = client.post(f"{API}/labor/adjustments/{cut.id}/dismiss?at={at}")
    assert dismissed.json() == {"id": cut.id, "status": "dismissed"}
    n.run_at(18, 45)
    assert n.open() == []  # 25 minutes after the dismissal: nothing new for servers
    n.run_at(19, 21)
    [again] = n.open()     # an hour and a minute after: the night is still slow
    assert again.id != cut.id and n.local(again.suggested_time) == "20:00"
    accepted = client.post(f"{API}/labor/adjustments/{again.id}/accept?at={n.at(19, 25).isoformat().replace('+00:00', 'Z')}")
    assert accepted.json() == {"id": again.id, "status": "accepted"}
    db.expire_all()
    shift = db.get(ScheduleShift, again.shift_id)
    assert n.local(shift.end_at) == "20:00" and db.get(LaborAdjustment, again.id).decided_by  # the schedule follows; who decided is on record
    assert [e.payload["decision"] for e in db.query(Event).filter_by(type="adjustment.decided").order_by(Event.seq)] == ["dismissed", "accepted"]
    assert client.post(f"{API}/labor/adjustments/{again.id}/accept?at={at}").status_code == 409  # already decided
    assert client.post(f"{API}/labor/adjustments/nope/accept").status_code == 404
    from app.ordering.models import Outbox

    assert db.query(Outbox).count() == 0  # nobody is messaged: the manager tells the employee


def test_a_suggestion_expires_after_twenty_minutes(client, db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "allow_time_travel", True)
    n = Night(client, db)
    n.run_at(18, 15)
    [cut] = n.open()
    late = client.post(f"{API}/labor/adjustments/{cut.id}/accept?at={n.at(18, 36).isoformat().replace('+00:00', 'Z')}")
    assert late.status_code == 409 and "expired" in late.json()["detail"]
    db.expire_all()
    assert db.get(LaborAdjustment, cut.id).status == "expired"
    n.run_at(18, 45)
    [fresh] = n.open()  # an expiry is not a dismissal: the next run may suggest again
    assert fresh.id != cut.id


def test_a_busy_night_suggests_keeping_the_person_whose_shift_ends_soonest_or_calling_in_a_flex_shift(client, db):
    n = Night(client, db, until=time(18, 0), factor=2.0)
    cal = db.query(ScheduleShift).filter_by(schedule_id=n.schedule.id, business_date=n.today, staff_id=n.team["Cal Ortiz"].id).one()
    cal.end_at = n.at(19, 0)  # Cal is due off at 7
    db.commit()
    day = n.run_at(18, 15)
    assert day.pace.multiplier == 1.75  # twice the covers, held at the 1.75 ceiling
    [keep] = n.open()
    assert (keep.kind, keep.shift_id, n.local(keep.suggested_time), keep.reason["why"], keep.reason["person"]) == ("extend", cal.id, "19:30", "ends_soonest", "Cal O.")
    assert all(need > heads for need, heads in zip(keep.reason["need"], keep.reason["heads"])) and keep.savings_estimate is None
    assert db.query(Notification).filter_by(kind="labor_suggestion").one().title == "Keep one server until 7:30 pm."
    # Nobody is due off, and a flex shift is on the schedule for later: call it in.
    keep.status, keep.decided_at = "expired", n.at(18, 16)
    cal.end_at = n.at(21, 30)
    flex = ScheduleShift(restaurant_id=n.rid, schedule_id=n.schedule.id, staff_id=n.team["Ana Rivera"].id, role_key="server", business_date=n.today, start_at=n.at(20, 0),
                         end_at=n.at(22, 0), kind="flex")
    db.add(flex)
    db.commit()
    n.run_at(18, 20)
    [call] = n.open()
    assert (call.kind, call.shift_id, call.reason["why"]) == ("call_in", flex.id, "flex")


def test_the_today_page_reads_pace_people_and_the_suggestion_and_staff_see_no_suggestion_or_cost(client, db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "allow_time_travel", True)
    n = Night(client, db)
    at = n.at(19, 15).isoformat().replace("+00:00", "Z")
    body = client.get(f"{API}/labor/today?at={at}").json()
    busy = n.today.weekday() in (4, 5)
    assert (body["state"], body["primary"], body["open_now"], body["stale"], body["wage_access"], body["suggestions_on"]) == ("live", "covers", True, False, True, True)
    assert body["pace"]["multiplier"] == 0.5 and body["pace"]["adjusted"] is True and 0.6 < body["pace"]["elapsed_share"] < 0.75
    assert body["so_far"]["actual"] == (20 + 9 * (4 if busy else 3)) and body["so_far"]["actual"] == body["so_far"]["expected"] / 2
    assert body["day"]["revised"] < body["day"]["expected"] and body["day"]["expected"] == (168.0 if busy else 136.0)
    server = next(p for p in body["people"] if p["role_key"] == "server")
    assert (server["name"], server["on"]) == ("Server", 4) and server["needed"] < server["on"]  # from the schedule: no punches today
    chart = body["chart"]
    assert chart["points"][chart["now_index"] - 1]["actual"] == body["so_far"]["actual"] and chart["points"][chart["now_index"]]["actual"] is None
    assert chart["points"][-1]["revised"] == body["day"]["revised"] and chart["points"][-1]["expected"] == body["day"]["expected"]
    assert [s["scheduled"] for s in body["coverage"]["server"]][:3] == [4, 4, 4] and body["coverage"]["server"][0]["label"] == "7:00"
    s = body["suggestion"]
    assert (s["kind"], s["role"], s["savings_estimate"], s["reason"]["person"]) == ("cut", "Server", "28.0000", "Cal O.")
    assert D(body["labor_so_far"]["cost"]) > 0 and D(body["labor_so_far"]["pct"]) > 0

    _account(db, "host@casaluna.example.com", MEMBER, n.rid)
    staff = _other_client("host@casaluna.example.com")
    seen = staff.get(f"{API}/labor/today?at={at}")
    assert seen.status_code == 200 and seen.json()["suggestion"] is None and seen.json()["wage_access"] is False and seen.json()["pace"]["multiplier"] == 0.5
    assert "savings" not in seen.text.replace('"suggestion":null', "") and "labor_so_far" not in seen.text and "hourly_rate" not in seen.text
    assert staff.post(f"{API}/labor/adjustments/{s['id']}/accept").status_code == 403
    # The notification is for owners and managers: staff do not get it, and the owner does.
    assert "Cut one server" not in staff.get("/api/v1/notifications").text and "Cut one server at 7:45 pm." in client.get("/api/v1/notifications").text
    # Without the setting, a given moment is ignored and the page is read as of now.
    monkeypatch.setattr(settings, "allow_time_travel", False)
    real = client.get(f"{API}/labor/today?at={at}").json()
    assert abs((datetime.fromisoformat(real["now"].replace("Z", "+00:00")) - datetime.now(timezone.utc)).total_seconds()) < 60


def test_the_tick_queues_the_reforecast_every_15_minutes_in_opening_hours_only(client, db):
    n = Night(client, db)
    db.query(Job).delete()
    db.commit()
    assert clock.run_due(db, n.at(18, 15)) == 1 and clock.run_due(db, n.at(18, 15)) == 0  # once for the interval
    assert clock.run_due(db, n.at(18, 20)) == 0 and clock.run_due(db, n.at(18, 30)) == 1
    assert [j.payload["job"] for j in db.query(Job).filter(Job.queue == "derive")] == ["intraday_reforecast"] * 2
    assert clock.run_due(db, n.at(9, 0)) == 0 and clock.run_due(db, n.at(23, 30)) == 0  # closed


def test_another_restaurant_sees_none_of_it_and_setup_shows_what_is_missing(client, db):
    n = Night(client, db)
    n.run_at(19, 15)
    [cut] = n.open()
    other, b = _second_restaurant(db)
    assert b.get(f"{API}/labor/today").status_code == 404
    flags.set_flag(db, other.id, flags.ML_LABOR, True)
    db.commit()
    theirs = b.get(f"{API}/labor/today").json()
    assert theirs["state"] == "setup" and theirs["suggestion"] is None and theirs["chart"] is None and [s["done"] for s in theirs["setup"]] == [False, False, False]
    assert b.post(f"{API}/labor/adjustments/{cut.id}/accept").status_code == 404 and b.post(f"{API}/labor/adjustments/{cut.id}/dismiss").status_code == 404
    assert db.query(LaborAdjustment).filter_by(restaurant_id=other.id).count() == 0


def test_a_cut_that_would_pay_under_the_least_paid_hours_is_not_suggested(client, db):
    n = Night(client, db)
    assert client.put(f"{API}/labor/rules", json={**RULES, "min_paid_hours": 3}).status_code == 200
    n.run_at(18, 15)   # a cut at 6:45 would pay 2.25 hours of a shift that began at 4:30: it saves nothing
    assert n.open() == []
    n.run_at(19, 15)   # at 7:45 it is 3.25 hours
    assert [a.kind for a in n.open()] == ["cut"]


def test_the_person_closest_to_overtime_this_week_is_suggested_first(client, db):
    n = Night(client, db)
    dee = n.team["Dee Shah"]
    monday = n.today - timedelta(days=n.today.weekday())
    other = monday if n.today != monday else monday + timedelta(days=1)
    local = datetime.combine(other, time(9, 0), tzinfo=n.zone)
    db.add(ScheduleShift(restaurant_id=n.rid, schedule_id=n.schedule.id, staff_id=dee.id, role_key="server", business_date=other, start_at=local.astimezone(timezone.utc),
                         end_at=(local + timedelta(hours=2)).astimezone(timezone.utc)))  # two more hours this week than the others
    db.commit()
    n.run_at(19, 15)
    [cut] = n.open()
    assert db.get(ScheduleShift, cut.shift_id).staff_id == dee.id and (cut.reason["why"], cut.reason["person"]) == ("closest_to_overtime", "Dee S.")
    assert cut.savings_estimate == D("26.2500")  # 1.75 hours at Dee's $15.00
