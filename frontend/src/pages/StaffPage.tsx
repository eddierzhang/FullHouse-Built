import { useCallback, useEffect, useMemo, useState } from 'react'
import { type Shift, type Staff, createShift, createStaff, deleteShift, deleteStaff, listShifts, listStaff, updateStaff } from '../api/client'
import type { AppContext } from '../app/context'
import { Icon } from '../components/Icon'
import { byName, initials, longDate, timeHm } from '../lib/format'

const BLANK_PERSON = { name: '', role: '', email: '' }
const todayIso = () => new Date().toISOString().slice(0, 10)

const hours = (s: Pick<Shift, 'start_time' | 'end_time'>) => {
  const [sh, sm] = s.start_time.split(':').map(Number)
  const [eh, em] = s.end_time.split(':').map(Number)
  return (eh * 60 + em - (sh * 60 + sm)) / 60
}

export function StaffPage({ app }: { app: AppContext }) {
  const [staff, setStaff] = useState<Staff[]>([])
  const [shifts, setShifts] = useState<Shift[]>([])
  const [busy, setBusy] = useState(false)

  const [addingPerson, setAddingPerson] = useState(false)
  const [person, setPerson] = useState(BLANK_PERSON)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [edit, setEdit] = useState(BLANK_PERSON)

  const [addingShift, setAddingShift] = useState(false)
  const [shift, setShift] = useState({ staff_id: '', date: todayIso(), start_time: '17:00', end_time: '23:00' })

  const load = useCallback(() => {
    listStaff().then(setStaff).catch(() => {})
    listShifts().then(setShifts).catch(() => {})
  }, [])
  useEffect(load, [load])

  const run = async (work: () => Promise<unknown>, message: string) => {
    setBusy(true)
    try {
      await work()
      app.toast(message)
      load()
      return true
    } catch (err) {
      app.toast('That didn’t work', err instanceof Error ? err.message : String(err))
      return false
    } finally {
      setBusy(false)
    }
  }

  const roster = useMemo(() => [...staff].sort(byName), [staff])
  const staffById = useMemo(() => new Map(staff.map((s) => [s.id, s])), [staff])

  // Upcoming only, soonest first; each day's shifts by start time.
  const schedule = useMemo(() => {
    const upcoming = shifts
      .filter((s) => s.date >= todayIso())
      .sort((a, b) => a.date.localeCompare(b.date) || a.start_time.localeCompare(b.start_time))
    const days = new Map<string, Shift[]>()
    for (const s of upcoming) days.set(s.date, [...(days.get(s.date) ?? []), s])
    return [...days.entries()]
  }, [shifts])

  const upcomingShifts = schedule.flatMap(([, day]) => day)
  const unscheduled = roster.filter((p) => !upcomingShifts.some((s) => s.staff_id === p.id))
  const proposals = app.pendingActions.filter((a) => a.action_type === 'shift_change').length

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Staff</h1>
          <p className="page-subtitle">The team and the upcoming schedule.</p>
        </div>
      </div>

      <div className="stack">
        {proposals > 0 && (
          <div className="callout callout-info">
            <Icon name="info" />
            <div className="callout-body">
              {proposals} scheduling {proposals === 1 ? 'proposal is' : 'proposals are'} waiting. <a href="#approvals">Review</a>
            </div>
          </div>
        )}

        <section className="grid grid-stats">
          <div className="card stat">
            <div className="stat-label">People</div>
            <div className="stat-value">{roster.length}</div>
          </div>
          <div className="card stat">
            <div className="stat-label">Upcoming shifts</div>
            <div className="stat-value">{upcomingShifts.length}</div>
          </div>
          <div className="card stat">
            <div className="stat-label">Hours scheduled</div>
            <div className="stat-value">{upcomingShifts.reduce((sum, s) => sum + hours(s), 0).toFixed(0)}</div>
          </div>
          <div className={`card stat${unscheduled.length ? ' attention' : ''}`}>
            <div className="stat-label">Without a shift</div>
            <div className="stat-value">{unscheduled.length}</div>
            <div className="stat-meta truncate">{unscheduled.map((p) => p.name).join(', ') || 'everyone is scheduled'}</div>
          </div>
        </section>

        <div className="grid grid-2" style={{ alignItems: 'start' }}>
          <section className="card">
            <div className="card-header">
              <div>
                <h2 className="card-title">Team</h2>
                <div className="card-subtitle">Alphabetical</div>
              </div>
              <button className="btn btn-secondary btn-sm" onClick={() => setAddingPerson((v) => !v)}>
                <Icon name={addingPerson ? 'close' : 'plus'} size={14} /> {addingPerson ? 'Cancel' : 'Add person'}
              </button>
            </div>

            {addingPerson && (
              <div className="inline-form stack" style={{ gap: 10 }}>
                <div className="form-row">
                  <label className="field">
                    <span className="field-label">Name</span>
                    <input className="input" autoFocus value={person.name} onChange={(e) => setPerson({ ...person, name: e.target.value })} />
                  </label>
                  <label className="field">
                    <span className="field-label">Role</span>
                    <input className="input" placeholder="e.g. Server" value={person.role} onChange={(e) => setPerson({ ...person, role: e.target.value })} />
                  </label>
                </div>
                <div className="form-row">
                  <label className="field">
                    <span className="field-label">Email (optional)</span>
                    <input className="input" type="email" value={person.email} onChange={(e) => setPerson({ ...person, email: e.target.value })} />
                  </label>
                  <button
                    className="btn btn-primary"
                    disabled={busy || !person.name.trim() || !person.role.trim()}
                    onClick={async () => {
                      const ok = await run(
                        () => createStaff({ name: person.name.trim(), role: person.role.trim(), email: person.email.trim() || null }),
                        'Added to the team',
                      )
                      if (ok) {
                        setPerson(BLANK_PERSON)
                        setAddingPerson(false)
                      }
                    }}
                  >
                    Save person
                  </button>
                </div>
              </div>
            )}

            <ul className="list">
              {roster.length === 0 && <li className="empty">Nobody on the team yet.</li>}
              {roster.map((p) => {
                const count = upcomingShifts.filter((s) => s.staff_id === p.id).length
                if (editingId === p.id) {
                  return (
                    <li key={p.id} className="list-item" style={{ flexWrap: 'wrap' }}>
                      <input className="input input-sm" aria-label="Name" style={{ flex: '1 1 140px' }} value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} />
                      <input className="input input-sm" aria-label="Role" style={{ flex: '1 1 100px' }} value={edit.role} onChange={(e) => setEdit({ ...edit, role: e.target.value })} />
                      <input className="input input-sm" aria-label="Email" style={{ flex: '1 1 160px' }} value={edit.email} onChange={(e) => setEdit({ ...edit, email: e.target.value })} />
                      <button className="btn btn-ghost btn-sm" onClick={() => setEditingId(null)}>
                        Cancel
                      </button>
                      <button
                        className="btn btn-primary btn-sm"
                        disabled={busy || !edit.name.trim() || !edit.role.trim()}
                        onClick={async () =>
                          (await run(() => updateStaff(p.id, { name: edit.name.trim(), role: edit.role.trim(), email: edit.email.trim() || null }), 'Details updated')) &&
                          setEditingId(null)
                        }
                      >
                        Save
                      </button>
                    </li>
                  )
                }
                return (
                  <li key={p.id} className="list-item">
                    <div className="avatar">{initials(p.name)}</div>
                    <div className="list-item-main">
                      <div className="row wrap">
                        <span className="strong">{p.name}</span>
                        <span className="badge">{p.role}</span>
                      </div>
                      <div className="muted small truncate">{p.email ?? 'No email on file'}</div>
                    </div>
                    <span className={`small ${count ? 'muted' : 'negative strong'}`}>{count ? `${count} upcoming` : 'No shifts'}</span>
                    <button
                      className="btn btn-ghost btn-sm btn-icon"
                      aria-label={`Edit ${p.name}`}
                      onClick={() => {
                        setEditingId(p.id)
                        setEdit({ name: p.name, role: p.role, email: p.email ?? '' })
                      }}
                    >
                      <Icon name="edit" size={14} />
                    </button>
                    <button
                      className="btn btn-ghost btn-sm btn-icon"
                      aria-label={`Remove ${p.name}`}
                      disabled={busy}
                      onClick={() => window.confirm(`Remove ${p.name} from the team?`) && run(() => deleteStaff(p.id), `${p.name} removed`)}
                    >
                      <Icon name="trash" size={14} />
                    </button>
                  </li>
                )
              })}
            </ul>
          </section>

          <section className="card">
            <div className="card-header">
              <div>
                <h2 className="card-title">Schedule</h2>
                <div className="card-subtitle">Upcoming, soonest first</div>
              </div>
              <button className="btn btn-secondary btn-sm" onClick={() => setAddingShift((v) => !v)}>
                <Icon name={addingShift ? 'close' : 'plus'} size={14} /> {addingShift ? 'Cancel' : 'Add shift'}
              </button>
            </div>

            {addingShift && (
              <div className="inline-form stack" style={{ gap: 10 }}>
                <label className="field">
                  <span className="field-label">Who</span>
                  <select className="select" value={shift.staff_id} onChange={(e) => setShift({ ...shift, staff_id: e.target.value })}>
                    <option value="">Choose a person…</option>
                    {roster.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name} — {p.role}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="form-row">
                  <label className="field">
                    <span className="field-label">Date</span>
                    <input className="input" type="date" value={shift.date} onChange={(e) => setShift({ ...shift, date: e.target.value })} />
                  </label>
                  <label className="field">
                    <span className="field-label">Start</span>
                    <input className="input" type="time" value={shift.start_time} onChange={(e) => setShift({ ...shift, start_time: e.target.value })} />
                  </label>
                  <label className="field">
                    <span className="field-label">End</span>
                    <input className="input" type="time" value={shift.end_time} onChange={(e) => setShift({ ...shift, end_time: e.target.value })} />
                  </label>
                </div>
                <button
                  className="btn btn-primary"
                  disabled={busy || !shift.staff_id || hours(shift) <= 0}
                  title={hours(shift) <= 0 ? 'A shift must end after it starts' : undefined}
                  onClick={async () => {
                    const ok = await run(
                      () => createShift({ ...shift, role: staffById.get(shift.staff_id)?.role ?? 'Staff' }),
                      'Shift scheduled',
                    )
                    if (ok) setAddingShift(false)
                  }}
                >
                  Schedule shift
                </button>
              </div>
            )}

            {schedule.length === 0 && <div className="empty">No upcoming shifts.</div>}
            {schedule.map(([date, day]) => (
              <div key={date} style={{ padding: '12px 20px', borderBottom: '1px solid var(--border)' }}>
                <div className="field-label" style={{ marginBottom: 8 }}>
                  {longDate(date)}
                </div>
                <div className="stack" style={{ gap: 6 }}>
                  {day.map((s) => (
                    <div key={s.id} className="row" style={{ padding: '8px 10px', borderRadius: 8, background: 'var(--surface-muted)' }}>
                      <span className="tabular strong small" style={{ width: 96 }}>
                        {timeHm(s.start_time)}–{timeHm(s.end_time)}
                      </span>
                      <span className="list-item-main truncate">{staffById.get(s.staff_id)?.name ?? 'Unknown'}</span>
                      <span className="badge">{s.role}</span>
                      <button
                        className="btn btn-ghost btn-sm btn-icon"
                        aria-label="Remove shift"
                        disabled={busy}
                        onClick={() => run(() => deleteShift(s.id), 'Shift removed')}
                      >
                        <Icon name="close" size={14} />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </section>
        </div>
      </div>
    </main>
  )
}
