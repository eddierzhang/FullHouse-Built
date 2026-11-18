"""Module D against the database, forecast and standards (spec sections 8.2, 8.3, 11 and 12): standards learned from
punches, forecasts by interval, people needed per role, and the acceptance: changing a standard updates the
requirements and the preview sentence. The fixture restaurant always has a server handling 8 covers an hour."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from app.margin_labor import clock, fixtures, labor, labor_read
from app.margin_labor import settings as ml_settings
from app.margin_labor.models import ForecastInterval, ForecastRun, LaborPunch, LaborRequirement, LaborRole, LaborStandard
from app.platform import flags, jobs
from app.platform.models import Event, Job
from test_restaurant_details import _second_restaurant
from test_restaurant_setup import _set_up

API = "/api/v1/margin-labor"


class Floor:
    def __init__(self, client, db, days=70):
        self.db, self.client = db, client
        self.rid = _set_up(client, db)["id"]
        flags.set_flag(db, self.rid, flags.ML_LABOR, True)
        db.commit()
        conf = ml_settings.for_restaurant(db, self.rid)
        self.tz = conf.timezone
        self.today = labor.Service(db, self.rid).today
        fixtures.seed_service(db, self.rid, self.today, self.tz, days=days)
        db.commit()

    def run(self):
        out = labor.forecast_labor(self.db, self.rid)
        self.db.commit()
        self.db.expire_all()
        return out

    def day(self, weekday):
        """The next date with this weekday, after today."""
        return self.today + timedelta(days=(weekday - self.today.weekday() - 1) % 7 + 1)

    def needs(self, role, day):
        local = labor.Service(self.db, self.rid).tz
        rows = self.db.query(LaborRequirement).filter_by(restaurant_id=self.rid, role_key=role, business_date=day, kind="weekly").order_by(LaborRequirement.interval_start).all()
        return {r.interval_start.astimezone(local).strftime("%H:%M"): (r.need_p50, r.need_p90) for r in rows}


def test_standards_are_learned_from_punches_and_people_needed_follow_the_forecast(client, db):
    f = Floor(client, db)
    made = f.run()
    assert made["state"] == "live" and made["stage"] == "baseline" and made["requirements"] > 0
    assert {r.key: (r.name, r.is_tipped) for r in db.query(LaborRole)}["server"] == ("Server", True)
    learned = {s.role_key: s for s in db.query(LaborStandard).filter_by(restaurant_id=f.rid)}
    # A server is clocked for 8 covers an hour in every interval; a cook for 12 to 16 items, and the busier half sets the standard.
    assert (learned["server"].units_per_labor_hour, learned["server"].driver_metric, learned["server"].driver_filter, learned["server"].source) == (D("8.0000"), "covers", {"channels": ["dine_in"]}, "learned")
    assert (learned["line_cook"].units_per_labor_hour, learned["line_cook"].driver_metric, learned["line_cook"].min_on_floor) == (D("16.0000"), "items", 1)
    assert learned["server"].sample_size >= 100

    tuesday, friday = f.day(1), f.day(4)
    # 12 covers each half hour of a weekday dinner, 4 per server: 3. On a Friday, 16 covers: 4. Lunch, 8 covers: 2.
    tue, fri = f.needs("server", tuesday), f.needs("server", friday)
    assert tue["18:00"][0] == 3 and tue["19:30"][0] == 3 and fri["18:00"][0] == 4 and tue["12:30"][0] == 2
    assert tue["15:00"] == (1, 1)  # between services: the floor of one, from open to close
    assert "09:00" not in tue and "23:00" not in tue  # nobody before opening or after close
    assert all(p90 >= p50 >= 1 for p50, p90 in tue.values())
    cooks = f.needs("line_cook", tuesday)
    assert cooks["18:00"][0] == 2 and cooks["12:30"][0] == 1  # 12 items at 16 an hour is 2; 8 items is 1

    day = db.query(ForecastInterval).filter_by(restaurant_id=f.rid, metric="covers", grain="day", business_date=tuesday).one()
    assert (day.p10, day.p50, day.p90) == (136.0, 136.0, 136.0)  # 10 lunch orders of 4 and 16 dinner orders of 6: the same every Tuesday
    slots = db.query(ForecastInterval).filter_by(restaurant_id=f.rid, metric="covers", grain="interval", business_date=tuesday).all()
    assert abs(sum(s.p50 for s in slots) - 136.0) < 0.001 and all(s.p10 <= s.p50 <= s.p90 for s in slots)
    assert db.query(ForecastInterval).filter_by(restaurant_id=f.rid, metric="covers", grain="day", business_date=friday).one().p50 == 168.0
    assert {e.type for e in db.query(Event).filter(Event.type.in_(("forecast.computed", "requirements.computed")))} == {"forecast.computed", "requirements.computed"}


def test_running_the_job_twice_leaves_the_same_rows(client, db):
    f = Floor(client, db)
    f.run()
    before = sorted((r.business_date, r.interval_start, r.role_key, r.need_p50, r.need_p90) for r in db.query(LaborRequirement))
    again = f.run()
    assert again["written"] == 0 and db.query(ForecastRun).filter_by(restaurant_id=f.rid).count() == 1
    assert sorted((r.business_date, r.interval_start, r.role_key, r.need_p50, r.need_p90) for r in db.query(LaborRequirement)) == before
    assert db.query(LaborStandard).filter_by(restaurant_id=f.rid).count() == 2  # learned once, not again


def test_acceptance_changing_a_standard_updates_requirements_and_the_preview_says_so_first(client, db):
    f = Floor(client, db)
    f.run()
    friday = f.day(4)
    before = sum(p50 for p50, _p90 in f.needs("server", friday).values()) * 0.5
    preview = client.post(f"{API}/labor/standards/preview", json={"role_key": "server", "units_per_labor_hour": "6", "min_on_floor": 1})
    assert preview.status_code == 200
    body = preview.json()
    shown = next(d for d in body["days"] if d["date"] == friday.isoformat())
    assert shown["before"] == before and shown["after"] > before and body["week_after"] > body["week_before"]
    assert body["top"]["date"] in [d["date"] for d in body["days"]] and body["top"]["after"] - body["top"]["before"] == max(d["after"] - d["before"] for d in body["days"])
    assert sum(p50 for p50, _p in f.needs("server", friday).values()) * 0.5 == before  # a preview writes nothing

    saved = client.put(f"{API}/labor/standards", json={"role_key": "server", "units_per_labor_hour": "6", "min_on_floor": 1})
    assert saved.status_code == 200
    db.expire_all()
    server = next(r for r in saved.json()["roles"] if r["role_key"] == "server")
    assert (server["units_per_labor_hour"], server["source"], server["learned"]["units_per_labor_hour"]) == ("6.0000", "manual", "8.0000")  # the owner's value wins; the learned one is kept beside it
    after = f.needs("server", friday)
    assert sum(p50 for p50, _p in after.values()) * 0.5 == shown["after"]  # what the preview said
    assert after["18:00"][0] == 6  # 16 covers a half hour at 3 per server
    # Another nightly run learns 8 again and leaves the owner's 6 in charge.
    f.run()
    assert f.needs("server", friday)["18:00"][0] == 6
    reset = client.delete(f"{API}/labor/standards/server")
    assert reset.status_code == 200 and next(r for r in reset.json()["roles"] if r["role_key"] == "server")["source"] == "learned"
    db.expire_all()
    assert f.needs("server", friday)["18:00"][0] == 4 and client.delete(f"{API}/labor/standards/server").status_code == 404
    for bad in ({"role_key": "server", "units_per_labor_hour": "0"}, {"role_key": "Server!", "units_per_labor_hour": "6"}, {"role_key": "server", "units_per_labor_hour": "6", "min_on_floor": -1}):
        assert client.put(f"{API}/labor/standards", json=bad).status_code == 422


def test_the_floor_runs_from_the_lead_before_opening_to_the_lag_after_closing(client, db):
    f = Floor(client, db)
    f.run()
    client.put(f"{API}/labor/standards", json={"role_key": "server", "units_per_labor_hour": "8", "min_on_floor": 2, "open_lead_minutes": 60, "close_lag_minutes": 30})
    db.expire_all()
    tue = f.needs("server", f.day(1))
    hours = labor.Service(db, f.rid).hours(f.day(1))
    opens, closes = hours[0] * 15, hours[1] * 15
    label = lambda minutes: f"{minutes // 60:02d}:{minutes % 60:02d}"  # noqa: E731
    assert tue[label(opens - 60)] == (2, 2) and label(opens - 90) not in tue  # an hour before opening, two on
    assert tue[label(closes)][0] == 2 and label(closes + 30) not in tue      # and half an hour after closing
    assert min(p50 for p50, _p in tue.values()) == 2


def test_under_four_weeks_is_setup_four_to_eight_is_learning_and_an_owner_can_enter_a_standard(client, db):
    short = Floor(client, db, days=20)
    assert short.run() == {"state": "setup", "weeks": 2, "written": 0}
    body = client.get(f"{API}/labor/weeks/next").json()
    assert body["state"] == "setup" and [(s["key"], s["done"]) for s in body["setup"]] == [("orders", False), ("standards", False)] and body["roles"] == [] and body["days"] == []
    page = client.get(f"{API}/labor/standards").json()
    server = next(r for r in page["roles"] if r["role_key"] == "server")
    assert page["state"] == "setup" and server["units_per_labor_hour"] is None and server["learned"] is None and server["driver_metric"] == "covers"
    assert server["observed_intervals"] > 0 and server["needed_intervals"] == 100


def test_five_weeks_of_orders_is_learning_with_a_learned_standard(client, db):
    f = Floor(client, db, days=40)
    assert f.run()["state"] == "learning"
    assert client.get(f"{API}/labor/weeks/next").json()["state"] == "learning"
    assert db.query(LaborStandard).filter_by(restaurant_id=f.rid, role_key="server").one().source == "learned"  # five weeks of punches is enough to learn


def test_the_week_page_reads_the_forecast_and_people_needed_and_the_standards_page_the_data_behind_them(client, db):
    f = Floor(client, db)
    body = client.get(f"{API}/labor/weeks/next").json()  # the first read computes the week
    monday = labor_read.default_week(f.today)
    assert (body["state"], body["primary"], body["week_of"], body["weeks_of_data"], body["schedule"]) == ("live", "covers", monday.isoformat(), 10, None)
    assert [d["date"] for d in body["days"]] == [(monday + timedelta(days=n)).isoformat() for n in range(7)]
    assert [d["expected"] for d in body["days"]] == [136.0] * 4 + [168.0] * 2 + [136.0]
    # Last week's actual is shown for the days of it that have closed, and nothing for the rest.
    assert [d["last_week"] for d in body["days"]] == [(136.0 if n < 4 or n == 6 else 168.0) if monday + timedelta(days=n - 7) < f.today else None for n in range(7)]
    assert body["totals"]["expected"] == 136.0 * 5 + 168.0 * 2 and body["totals"]["net_expected"] == body["totals"]["expected"] * 20 and body["totals"]["days_forecast"] == 7
    server = next(r for r in body["roles"] if r["role_key"] == "server")
    assert server["name"] == "Server" and server["peak"] == 4 and len(server["by_day"]) == 7 and server["hours_p90"] >= server["hours_p50"] > 0
    assert body["totals"]["hours_p50"] == sum(r["hours_p50"] for r in body["roles"])
    friday = (monday + timedelta(days=4)).isoformat()
    steps = body["intervals"][friday]["server"]
    six = next(s for s in steps if s["label"] == "6:00" and s["need"] == 4)
    assert six["need_if_busy"] >= 4 and six["scheduled"] == 0 and [s["start"] for s in steps] == sorted(s["start"] for s in steps)
    assert client.get(f"{API}/labor/weeks/{monday.isoformat()}").json()["week_of"] == monday.isoformat()
    assert client.get(f"{API}/labor/weeks/soon").status_code == 422

    page = client.get(f"{API}/labor/standards").json()
    server = next(r for r in page["roles"] if r["role_key"] == "server")
    assert (server["units_per_labor_hour"], server["source"], server["min_on_floor"], server["driver_metric"]) == ("8.0000", "learned", 1, "covers")
    assert server["histogram"] == [{"from": 8.0, "to": 8.0, "count": server["observed_intervals"]}] and server["punch_weeks"] >= 7  # every interval at 8 an hour
    assert page["accuracy"]["target"] == 0.8 and page["accuracy"]["coverage"] == 1.0 and page["accuracy"]["calibration_factor"] == 1.0
    # Lunch is forecast exactly. The 3-interval smoothing puts a third of dinner's first quarter hour (2 of 96 covers) at 4:45.
    assert {d["daypart"]: d["wape"] for d in page["accuracy"]["dayparts"]} == {"lunch": 0.0, "dinner": 0.020833}


def test_no_wage_is_read_or_returned_and_the_nightly_tick_queues_the_job_once(client, db):
    f = Floor(client, db)
    week = client.get(f"{API}/labor/weeks/next").text
    page = client.get(f"{API}/labor/standards").text
    assert "hourly_rate" not in week and "hourly_rate" not in page and "cost" not in week
    local = datetime.now(timezone.utc).astimezone(labor.Service(db, f.rid).tz)
    nightly = (local.replace(hour=4, minute=30, second=0, microsecond=0) + timedelta(days=1)).astimezone(timezone.utc)
    assert clock.run_due(db, nightly) == 1 and clock.run_due(db, nightly) == 0
    assert [j.payload["job"] for j in db.query(Job).filter(Job.queue == "derive", Job.status == "queued")] == ["forecast_labor"]
    jobs.drain(db, queues=("derive",))
    db.commit()
    assert db.query(LaborRequirement).filter_by(restaurant_id=f.rid).count() > 0


def test_another_restaurant_sees_none_of_it(client, db):
    f = Floor(client, db)
    f.run()
    other, b = _second_restaurant(db)
    assert b.get(f"{API}/labor/weeks/next").status_code == 404 and b.get(f"{API}/labor/standards").status_code == 404
    flags.set_flag(db, other.id, flags.ML_LABOR, True)
    db.commit()
    theirs = b.get(f"{API}/labor/weeks/next").json()
    assert theirs["state"] == "setup" and theirs["roles"] == [] and theirs["weeks_of_data"] == 0
    assert all(r["observed_intervals"] == 0 for r in b.get(f"{API}/labor/standards").json()["roles"])
    assert db.query(LaborRequirement).filter_by(restaurant_id=other.id).count() == 0 and db.query(LaborPunch).filter_by(restaurant_id=other.id).count() == 0
    assert b.delete(f"{API}/labor/standards/server").status_code == 404
