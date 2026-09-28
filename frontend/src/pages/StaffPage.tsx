import { useMemo, useState } from 'react'
import {
  type Shift,
  type Staff,
  type StaffInput,
  type User,
  canEditRestaurant,
  createShift,
  createStaff,
  deleteShift,
  deleteStaff,
  listShifts,
  listStaff,
  publishShifts,
  updateStaff,
} from '../api/client'
import type { AppContext } from '../app/context'
import { Icon } from '../components/Icon'
import { Freshness, Loaded } from '../components/PageState'
import { byName, initials, longDate, timeHm } from '../lib/format'
import { useLoad } from '../lib/useLoad'

const BLANK_PERSON = { name: '', role: '', email: '' }

type ProfileForm = { name: string; role: string; email: string; phone: string; emergency_contact: string; hire_date: string; notes: string }

const toForm = (p: Staff): ProfileForm => ({
  name: p.name,
  role: p.role,
  email: p.email ?? '',
  phone: p.phone ?? '',
  emergency_contact: p.emergency_contact ?? '',
  hire_date: p.hire_date ?? '',
  notes: p.notes ?? '',
})

const fromForm = (f: ProfileForm): StaffInput => ({
  name: f.name.trim(),
  role: f.role.trim(),
  email: f.email.trim() || null,
  phone: f.phone.trim() || null,
  emergency_contact: f.emergency_contact.trim() || null,
  hire_date: f.hire_date || null,
  notes: f.notes.trim() || null,
})

/**
 * The roles set in Restaurant Settings, as a dropdown; free text only when
 * none are set. A role no longer on the list stays selectable on the person
 * who already has it, so editing their name doesn't force a role change.
 */
function RoleInput({ roles, value, onChange, small }: { roles: string[] | null; value: string; onChange: (role: string) => void; small?: boolean }) {
  if (!roles?.length) {
    return (
      <input className={`input${small ? ' input-sm' : ''}`} aria-label="Role" placeholder="e.g. Server" value={value} onChange={(e) => onChange(e.target.value)} />
    )
  }
  const options = value && !roles.includes(value) ? [value, ...roles] : roles
  return (
    <select className={`select${small ? ' input-sm' : ''}`} aria-label="Role" value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="" disabled>
        Choose a role
      </option>
      {options.map((role) => (
        <option key={role} value={role}>
          {role}
        </option>
      ))}
    </select>
  )
}

/** Every profile field, for a manager editing someone. */
function ProfileEditor({
  roles,
  initial,
  busy,
  onCancel,
  onSave,
}: {
  roles: string[] | null
  initial: ProfileForm
  busy: boolean
  onCancel: () => void
  onSave: (form: ProfileForm) => void
}) {
  const [form, setForm] = useState(initial)
  const set = (field: keyof ProfileForm) => (e: { target: { value: string } }) => setForm({ ...form, [field]: e.target.value })
  return (
    <li className="list-item" style={{ display: 'block' }}>
      <div className="stack" style={{ gap: 10 }}>
        <div className="form-row">
          <label className="field">
            <span className="field-label">Name</span>
            <input className="input" autoFocus value={form.name} onChange={set('name')} />
          </label>
          <label className="field">
            <span className="field-label">Role</span>
            <RoleInput roles={roles} value={form.role} onChange={(role) => setForm({ ...form, role })} />
          </label>
        </div>
        <div className="form-row">
          <label className="field">
            <span className="field-label">Email</span>
            <input className="input" type="email" value={form.email} onChange={set('email')} />
            <span className="field-hint">They see their own shifts when they sign in with this address.</span>
          </label>
          <label className="field">
            <span className="field-label">Phone</span>
            <input className="input" type="tel" value={form.phone} onChange={set('phone')} />
          </label>
        </div>
        <div className="form-row">
          <label className="field">
            <span className="field-label">Emergency contact</span>
            <input className="input" placeholder="Name, relationship, phone" value={form.emergency_contact} onChange={set('emergency_contact')} />
          </label>
          <label className="field">
            <span className="field-label">Hire date</span>
            <input className="input" type="date" value={form.hire_date} onChange={set('hire_date')} />
          </label>
        </div>
        <label className="field">
          <span className="field-label">Manager notes</span>
          <textarea className="textarea" rows={2} value={form.notes} onChange={set('notes')} />
          <span className="field-hint">Only owners and admins see these.</span>
        </label>
        <div className="row" style={{ justifyContent: 'flex-end' }}>
          <button className="btn btn-ghost btn-sm" onClick={onCancel}>
            Cancel
          </button>
          <button className="btn btn-primary btn-sm" disabled={busy || !form.name.trim() || !form.role.trim()} onClick={() => onSave(form)}>
            Save
          </button>
        </div>
      </div>
    </li>
  )
}

const todayIso = () => new Date().toISOString().slice(0, 10)

const hours = (s: Pick<Shift, 'start_time' | 'end_time'>) => {
  const [sh, sm] = s.start_time.split(':').map(Number)
  const [eh, em] = s.end_time.split(':').map(Number)
  return (eh * 60 + em - (sh * 60 + sm)) / 60
}

export function StaffPage({ app, user }: { app: AppContext; user: User }) {
  const isManager = canEditRestaurant(user)
  const staffLoad = useLoad(listStaff, [])
  const shiftsLoad = useLoad(listShifts, [])
  const [busy, setBusy] = useState(false)

  const [addingPerson, setAddingPerson] = useState(false)
  const [person, setPerson] = useState(BLANK_PERSON)
  const [editingId, setEditingId] = useState<string | null>(null)

  const [addingShift, setAddingShift] = useState(false)
  const [shift, setShift] = useState({ staff_id: '', date: todayIso(), start_time: '17:00', end_time: '23:00' })

  const [contact, setContact] = useState<{ phone: string; emergency_contact: string } | null>(null)

  // Until both have loaded (or if one fails) the lists below show that, not "nobody" or "no shifts".
  const staff = useMemo(() => staffLoad.data ?? [], [staffLoad.data])
  const shifts = useMemo(() => shiftsLoad.data ?? [], [shiftsLoad.data])
  const ready = staffLoad.data !== null && shiftsLoad.data !== null
  const load = () => {
    void staffLoad.reload()
    void shiftsLoad.reload()
  }

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
  const me = staff.find((s) => s.is_me) ?? null

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
  const myShifts = me ? upcomingShifts.filter((s) => s.staff_id === me.id) : []
  const drafts = shifts.filter((s) => !s.published_at).length
  const unscheduled = roster.filter((p) => !upcomingShifts.some((s) => s.staff_id === p.id))
  const proposals = app.pendingActions.filter((a) => a.action_type === 'shift_change').length

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Schedule & staff</h1>
          <p className="page-subtitle">{isManager ? 'The team and the schedule.' : 'Your shifts and the team schedule.'}</p>
        </div>
        <div className="row wrap">
          <Freshness loads={[staffLoad, shiftsLoad]} />
          {isManager && drafts > 0 && (
            <button className="btn btn-primary" disabled={busy} onClick={() => run(publishShifts, `Schedule published — staff can see ${drafts === 1 ? 'the new shift' : `all ${drafts} new shifts`}`)}>
              <Icon name="check" size={14} /> Publish {drafts} {drafts === 1 ? 'shift' : 'shifts'}
            </button>
          )}
        </div>
      </div>

      <div className="stack">
        {isManager && drafts > 0 && (
          <div className="callout callout-warning">
            <Icon name="info" />
            <div className="callout-body">
              {drafts} {drafts === 1 ? 'shift is a draft' : 'shifts are drafts'}. Staff see {drafts === 1 ? 'it' : 'them'} once you publish. Changes to shifts that are already published show straight away.
            </div>
          </div>
        )}
        {isManager && proposals > 0 && (
          <div className="callout callout-info">
            <Icon name="info" />
            <div className="callout-body">
              {proposals} scheduling {proposals === 1 ? 'proposal is' : 'proposals are'} waiting. <a href="#approvals">Review</a>
            </div>
          </div>
        )}
        {!isManager && staffLoad.data !== null && !me && (
          <div className="callout callout-info">
            <Icon name="info" />
            <div className="callout-body">
              Your account isn’t linked to a staff profile yet. Ask a manager to put <strong>{user.email}</strong> on your profile, and your shifts will show here.
            </div>
          </div>
        )}

        {!isManager && me && (
          <div className="grid grid-2" style={{ alignItems: 'start' }}>
            <section className="card">
              <div className="card-header">
                <div>
                  <h2 className="card-title">My shifts</h2>
                  <div className="card-subtitle">
                    {!shiftsLoad.data
                      ? ' '
                      : myShifts.length
                        ? `${myShifts.length} upcoming · ${myShifts.reduce((sum, s) => sum + hours(s), 0).toFixed(1)} hours`
                        : 'Nothing scheduled yet'}
                  </div>
                </div>
              </div>
              <Loaded load={shiftsLoad} what="your shifts">
                {() => (
                  <ul className="list">
                    {myShifts.length === 0 && <li className="empty">No upcoming shifts.</li>}
                    {myShifts.map((s) => (
                      <li key={s.id} className="list-item">
                        <div className="list-item-main">
                          <div className="strong">{longDate(s.date)}</div>
                          <div className="muted small tabular">
                            {timeHm(s.start_time)}–{timeHm(s.end_time)} · {hours(s).toFixed(1)} h
                          </div>
                        </div>
                        <span className="badge">{s.role}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </Loaded>
            </section>

            <section className="card">
              <div className="card-header">
                <div>
                  <h2 className="card-title">My details</h2>
                  <div className="card-subtitle">
                    {me.role}
                    {me.hire_date ? ` · since ${longDate(me.hire_date)}` : ''}
                  </div>
                </div>
                {!contact && (
                  <button className="btn btn-secondary btn-sm" onClick={() => setContact({ phone: me.phone ?? '', emergency_contact: me.emergency_contact ?? '' })}>
                    <Icon name="edit" size={14} /> Edit
                  </button>
                )}
              </div>
              {contact ? (
                <div className="inline-form stack" style={{ gap: 10 }}>
                  <label className="field">
                    <span className="field-label">Phone</span>
                    <input className="input" type="tel" value={contact.phone} onChange={(e) => setContact({ ...contact, phone: e.target.value })} />
                  </label>
                  <label className="field">
                    <span className="field-label">Emergency contact</span>
                    <input
                      className="input"
                      placeholder="Name, relationship, phone"
                      value={contact.emergency_contact}
                      onChange={(e) => setContact({ ...contact, emergency_contact: e.target.value })}
                    />
                  </label>
                  <div className="row" style={{ justifyContent: 'flex-end' }}>
                    <button className="btn btn-ghost btn-sm" onClick={() => setContact(null)}>
                      Cancel
                    </button>
                    <button
                      className="btn btn-primary btn-sm"
                      disabled={busy}
                      onClick={async () =>
                        (await run(() => updateStaff(me.id, { phone: contact.phone.trim() || null, emergency_contact: contact.emergency_contact.trim() || null }), 'Your details are saved')) &&
                        setContact(null)
                      }
                    >
                      Save
                    </button>
                  </div>
                </div>
              ) : (
                <dl className="stack" style={{ gap: 6, padding: '12px 20px', margin: 0 }}>
                  <div className="row">
                    <dt className="muted small" style={{ width: 140 }}>Email</dt>
                    <dd style={{ margin: 0 }}>{me.email}</dd>
                  </div>
                  <div className="row">
                    <dt className="muted small" style={{ width: 140 }}>Phone</dt>
                    <dd style={{ margin: 0 }}>{me.phone ?? <span className="faint">Not given</span>}</dd>
                  </div>
                  <div className="row">
                    <dt className="muted small" style={{ width: 140 }}>Emergency contact</dt>
                    <dd style={{ margin: 0 }}>{me.emergency_contact ?? <span className="faint">Not given</span>}</dd>
                  </div>
                </dl>
              )}
            </section>
          </div>
        )}

        {isManager && (
          <section className="grid grid-stats">
            <div className="card stat">
              <div className="stat-label">People</div>
              <div className="stat-value">{staffLoad.data ? roster.length : '—'}</div>
            </div>
            <div className="card stat">
              <div className="stat-label">Upcoming shifts</div>
              <div className="stat-value">{shiftsLoad.data ? upcomingShifts.length : '—'}</div>
            </div>
            <div className="card stat">
              <div className="stat-label">Hours scheduled</div>
              <div className="stat-value">{shiftsLoad.data ? upcomingShifts.reduce((sum, s) => sum + hours(s), 0).toFixed(0) : '—'}</div>
            </div>
            <div className={`card stat${ready && unscheduled.length ? ' attention' : ''}`}>
              <div className="stat-label">Without a shift</div>
              <div className="stat-value">{ready ? unscheduled.length : '—'}</div>
              <div className="stat-meta truncate">{ready ? unscheduled.map((p) => p.name).join(', ') || 'everyone is scheduled' : ' '}</div>
            </div>
          </section>
        )}

        <div className="grid grid-2" style={{ alignItems: 'start' }}>
          <section className="card">
            <div className="card-header">
              <div>
                <h2 className="card-title">Team</h2>
                <div className="card-subtitle">Alphabetical</div>
              </div>
              {isManager && (
                <button className="btn btn-secondary btn-sm" onClick={() => setAddingPerson((v) => !v)}>
                  <Icon name={addingPerson ? 'close' : 'plus'} size={14} /> {addingPerson ? 'Cancel' : 'Add person'}
                </button>
              )}
            </div>

            {isManager && addingPerson && (
              <div className="inline-form stack" style={{ gap: 10 }}>
                <div className="form-row">
                  <label className="field">
                    <span className="field-label">Name</span>
                    <input className="input" autoFocus value={person.name} onChange={(e) => setPerson({ ...person, name: e.target.value })} />
                  </label>
                  <label className="field">
                    <span className="field-label">Role</span>
                    <RoleInput roles={app.restaurant.staff_roles} value={person.role} onChange={(role) => setPerson({ ...person, role })} />
                  </label>
                </div>
                <div className="form-row">
                  <label className="field">
                    <span className="field-label">Email (optional)</span>
                    <input className="input" type="email" value={person.email} onChange={(e) => setPerson({ ...person, email: e.target.value })} />
                    <span className="field-hint">Lets them see their own shifts when they sign in with it.</span>
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

            <Loaded load={staffLoad} what="the team">
              {() => (
                <ul className="list">
                  {roster.length === 0 && <li className="empty">Nobody on the team yet.</li>}
                  {roster.map((p) => {
                    const count = upcomingShifts.filter((s) => s.staff_id === p.id).length
                    if (isManager && editingId === p.id) {
                      return (
                        <ProfileEditor
                          key={p.id}
                          roles={app.restaurant.staff_roles}
                          initial={toForm(p)}
                          busy={busy}
                          onCancel={() => setEditingId(null)}
                          onSave={async (form) => (await run(() => updateStaff(p.id, fromForm(form)), 'Details updated')) && setEditingId(null)}
                        />
                      )
                    }
                    return (
                      <li key={p.id} className="list-item">
                        <div className="avatar">{initials(p.name)}</div>
                        <div className="list-item-main">
                          <div className="row wrap">
                            <span className="strong">{p.name}</span>
                            <span className="badge">{p.role}</span>
                            {p.is_me && <span className="badge badge-brand">You</span>}
                            {isManager && p.email && p.has_account === false && (
                              <span className="badge badge-warning" title="Nobody has signed up with this email yet">
                                No login yet
                              </span>
                            )}
                          </div>
                          {isManager && (
                            <div className="muted small truncate">{[p.email ?? 'No email on file', p.phone].filter(Boolean).join(' · ')}</div>
                          )}
                        </div>
                        {isManager && (
                          <>
                            <span className={`small ${count ? 'muted' : 'negative strong'}`}>{count ? `${count} upcoming` : 'No shifts'}</span>
                            <button className="btn btn-ghost btn-sm btn-icon" aria-label={`Edit ${p.name}`} onClick={() => setEditingId(p.id)}>
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
                          </>
                        )}
                      </li>
                    )
                  })}
                </ul>
              )}
            </Loaded>
          </section>

          <section className="card">
            <div className="card-header">
              <div>
                <h2 className="card-title">Schedule</h2>
                <div className="card-subtitle">{isManager ? 'Upcoming, soonest first' : 'Published shifts, soonest first'}</div>
              </div>
              {isManager && (
                <button className="btn btn-secondary btn-sm" onClick={() => setAddingShift((v) => !v)}>
                  <Icon name={addingShift ? 'close' : 'plus'} size={14} /> {addingShift ? 'Cancel' : 'Add shift'}
                </button>
              )}
            </div>

            {isManager && addingShift && (
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
                      'Shift added as a draft',
                    )
                    if (ok) setAddingShift(false)
                  }}
                >
                  Add draft shift
                </button>
              </div>
            )}

            <Loaded load={shiftsLoad} what="the schedule">
              {() => (
                <>
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
                            {me?.id === s.staff_id && <span className="badge badge-brand">You</span>}
                            {!s.published_at && <span className="badge badge-warning">Draft</span>}
                            <span className="badge">{s.role}</span>
                            {isManager && (
                              <button
                                className="btn btn-ghost btn-sm btn-icon"
                                aria-label="Remove shift"
                                disabled={busy}
                                onClick={() => {
                                  const who = staffById.get(s.staff_id)?.name
                                  if (window.confirm(`Remove ${who ? `${who}’s` : 'this'} shift on ${longDate(s.date)}?`)) void run(() => deleteShift(s.id), 'Shift removed')
                                }}
                              >
                                <Icon name="close" size={14} />
                              </button>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </>
              )}
            </Loaded>
          </section>
        </div>
      </div>
    </main>
  )
}
