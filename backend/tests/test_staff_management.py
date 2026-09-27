"""Editing and removing staff and shifts.

These endpoints did not exist before the employee page: everything was
create-only, so a name could be added but never corrected.
"""

from app.restaurant.models import Shift, Staff


def _staff(db, name="Ana Diaz", role="server"):
    staff = Staff(name=name, role=role, email=f"{name.split()[0].lower()}@example.com")
    db.add(staff)
    db.commit()
    return staff


def _shift(db, staff, date="2026-10-01", start="17:00", end="23:00"):
    from datetime import date as d, time as t

    shift = Shift(
        staff_id=staff.id,
        date=d.fromisoformat(date),
        start_time=t.fromisoformat(start),
        end_time=t.fromisoformat(end),
        role=staff.role,
    )
    db.add(shift)
    db.commit()
    return shift


def test_partial_update_leaves_other_fields_alone(client, db):
    staff = _staff(db)

    response = client.patch(f"/api/v1/restaurant/staff/{staff.id}", json={"role": "shift lead"})

    assert response.status_code == 200
    assert response.json()["role"] == "shift lead"
    assert response.json()["name"] == "Ana Diaz"  # untouched
    db.refresh(staff)
    assert staff.role == "shift lead"


def test_update_is_recorded_in_the_change_log(client, db, changes):
    staff = _staff(db)

    client.patch(
        f"/api/v1/restaurant/staff/{staff.id}",
        json={"name": "Ana Diaz-Moreno"},
        headers={"X-Actor-Id": "eddie"},
    )

    update = changes(entity_type="staff", operation="update")[-1]
    assert update.before == {"name": "Ana Diaz"}
    assert update.after == {"name": "Ana Diaz-Moreno"}
    assert update.actor_id == "eddie"


def test_updating_a_missing_staff_member_is_404(client, db):
    assert client.patch("/api/v1/restaurant/staff/nope", json={"role": "x"}).status_code == 404


def test_delete_removes_the_staff_member(client, db):
    staff = _staff(db)

    assert client.delete(f"/api/v1/restaurant/staff/{staff.id}").status_code == 204
    assert db.get(Staff, staff.id) is None


def test_delete_is_refused_while_shifts_reference_them(client, db):
    staff = _staff(db)
    _shift(db, staff)

    response = client.delete(f"/api/v1/restaurant/staff/{staff.id}")

    assert response.status_code == 409
    assert "1 scheduled shift" in response.json()["detail"]
    assert db.get(Staff, staff.id) is not None


def test_delete_succeeds_once_the_shifts_are_gone(client, db):
    staff = _staff(db)
    shift = _shift(db, staff)

    assert client.delete(f"/api/v1/restaurant/shifts/{shift.id}").status_code == 204
    assert client.delete(f"/api/v1/restaurant/staff/{staff.id}").status_code == 204
    assert db.get(Staff, staff.id) is None


def test_shift_can_be_rescheduled(client, db):
    staff = _staff(db)
    shift = _shift(db, staff)

    response = client.patch(
        f"/api/v1/restaurant/shifts/{shift.id}", json={"start_time": "16:00", "end_time": "22:00"}
    )

    assert response.status_code == 200
    assert response.json()["start_time"] == "16:00:00"
    db.refresh(shift)
    assert shift.start_time.hour == 16


def test_a_shift_cannot_end_before_it_starts(client, db):
    staff = _staff(db)
    shift = _shift(db, staff)

    response = client.patch(f"/api/v1/restaurant/shifts/{shift.id}", json={"end_time": "09:00"})

    assert response.status_code == 422
    db.refresh(shift)
    assert shift.end_time.hour == 23  # rolled back


def test_reassigning_a_shift_to_an_unknown_person_is_404(client, db):
    staff = _staff(db)
    shift = _shift(db, staff)

    response = client.patch(f"/api/v1/restaurant/shifts/{shift.id}", json={"staff_id": "ghost"})

    assert response.status_code == 404


def test_deleting_a_shift_is_recorded(client, db, changes):
    staff = _staff(db)
    shift = _shift(db, staff)

    client.delete(f"/api/v1/restaurant/shifts/{shift.id}")

    delete = changes(entity_type="shifts", operation="delete")[-1]
    assert delete.after is None
    assert delete.before["role"] == "server"


def test_a_deleted_shift_can_be_restored_by_revert(client, db):
    """Deletes are recoverable, which matters for an accidental removal."""
    staff = _staff(db)
    shift = _shift(db, staff)
    shift_id = shift.id
    client.delete(f"/api/v1/restaurant/shifts/{shift_id}")

    change = client.get("/api/v1/changes", params={"entity_type": "shifts", "operation": "delete"})
    change_id = change.json()["items"][0]["id"]
    assert client.post(f"/api/v1/changes/{change_id}/revert").status_code == 200

    assert db.get(Shift, shift_id) is not None
