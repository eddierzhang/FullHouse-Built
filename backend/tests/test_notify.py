"""Alerts reach people: a bell in the app for everyone, and mail to owners and managers for the ones that need
someone to act."""

from app.auth.models import User
from app.notify import service as notify
from app.notify.models import Notification
from app.ordering.models import Outbox
from app.platform import jobs
from test_restaurant_details import _second_restaurant
from test_restaurant_setup import _set_up

API = "/api/v1/notifications"


def test_the_bell_lists_what_was_said_and_marks_it_read_and_staff_do_not_see_spending(client, db):
    rid = _set_up(client, db)["id"]
    notify.tell(db, rid, kind="stock_eighty_six", title="86 Margherita Pizza", body="Not enough mozzarella.", link="#variance", dedupe_key="a")
    notify.tell(db, rid, kind="autopilot_send", title="Sysco order goes out at 2:30 pm", body="$248.10", link="#autopilot", dedupe_key="b")
    assert notify.tell(db, rid, kind="autopilot_send", title="again", dedupe_key="b") is None  # said once
    db.commit()

    bell = client.get(API).json()
    assert bell["unread"] == 2 and {i["title"] for i in bell["items"]} == {"86 Margherita Pizza", "Sysco order goes out at 2:30 pm"}
    first = next(i for i in bell["items"] if i["kind"] == "stock_eighty_six")
    assert client.post(f"{API}/read", json={"ids": [first["id"]]}).json()["unread"] == 1
    assert client.post(f"{API}/read").json()["unread"] == 0

    db.query(User).filter(User.restaurant_id == rid).update({"role": "member"})
    db.commit()
    assert [i["kind"] for i in client.get(API).json()["items"]] == ["stock_eighty_six"]

    _, b = _second_restaurant(db)
    assert b.get(API).json() == {"unread": 0, "items": []}
    assert b.post(f"{API}/read", json={"ids": [first["id"]]}).json()["unread"] == 0
    assert db.query(Notification).filter_by(restaurant_id=rid).count() == 2


def test_the_ones_that_need_someone_are_mailed_to_managers_once(client, db, monkeypatch):
    from app.prices import mail

    rid = _set_up(client, db)["id"]
    sent = []
    monkeypatch.setattr(mail, "can_send", lambda: True)
    monkeypatch.setattr(mail, "send", lambda to, subject, html, text, **kw: sent.append((to, subject, text)) or True)
    notify.tell(db, rid, kind="autopilot_send", title="Sysco order goes out at 2:30 pm", body="$248.10, 6 lines.", link="#autopilot", dedupe_key="send-1")
    notify.tell(db, rid, kind="stock_runs_out", title="Basil runs out", dedupe_key="low-1")  # in the app only
    db.commit()

    assert notify.deliver_notices(db, rid) == 1 and notify.deliver_notices(db, rid) == 0
    db.commit()
    assert len(sent) == 1 and sent[0][1] == "Sysco order goes out at 2:30 pm" and "$248.10, 6 lines." in sent[0][2]
    assert sent[0][0] == [u.email for u in db.query(User).filter_by(restaurant_id=rid)]
    assert [o.status for o in db.query(Outbox).filter_by(kind="notice")] == ["sent"]


def test_with_no_mailbox_a_notice_stays_in_the_app_and_is_not_retried_forever(client, db):
    rid = _set_up(client, db)["id"]
    notify.tell(db, rid, kind="delivery_short", title="Green Valley delivery came in short", dedupe_key="short-1")
    db.commit()

    assert notify.deliver_notices(db, rid) == 0
    db.commit()
    assert [o.status for o in db.query(Outbox).filter_by(kind="notice")] == ["logged"]
    assert client.get(API).json()["unread"] == 1
