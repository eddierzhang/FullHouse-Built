import { useEffect, useRef, useState } from 'react'
import { apiUrl } from '../api/client'
import type { AppContext } from '../app/context'
import { addShift, editShift, fetchJob, fetchLaborRules, generateSchedule, type LaborRulesBody, type LaborWeek, publishSchedule, type PublishWarning, removeShift, saveLaborRules, type Schedule, scheduleCsvUrl, type ScheduleShift } from './api'
import { dayLabel } from './format'
import { type GridRow, type GridShift, ScheduleGrid } from './ScheduleGrid'
import { SidePanel, StateBanner, StatusWord, useDeferred } from './ui'

/** "4:30 pm" from minutes after local midnight; past midnight wraps to the small hours. */
export function clock(minutes: number): string {
  const m = ((minutes % 1440) + 1440) % 1440
  const hour = Math.floor(m / 60)
  return `${hour % 12 || 12}:${String(m % 60).padStart(2, '0')} ${hour < 12 ? 'am' : 'pm'}`
}
const short = (minutes: number) => clock(minutes).replace(':00', '').replace(' ', '')
const roleName = (key: string) => key.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase())
const RULE_WORDS: Record<string, string> = {
  weekly_overtime_hours: 'Weekly overtime, hours', daily_overtime_hours: 'Daily overtime, hours', min_rest_hours: 'Rest between shifts, hours', min_shift_hours: 'Shortest shift, hours',
  max_shift_hours: 'Longest shift, hours', max_hours_per_day: 'Most hours a day', max_consecutive_days: 'Most days in a row', break_after_hours: 'Break after, hours',
  break_minutes: 'Break, minutes', min_paid_hours: 'Least paid hours', advance_notice_days: 'Notice, days', minor_max_hours_per_day: 'Minors: most hours a day',
}
const TIMES = Array.from({ length: 61 }, (_, i) => i * 30)

export function warningText(w: PublishWarning): string {
  if (w.kind === 'notice') return `The week starts in ${w.days} days. Your rule asks for ${w.rule} days of notice.`
  if (w.kind === 'open_shifts') return `${w.count} ${w.count === 1 ? 'shift is' : 'shifts are'} open, with nobody on ${w.count === 1 ? 'it' : 'them'}.`
  return `${w.person} ${w.rule}.`
}

/**
 * The week's schedule on the Labor page: generate or regenerate, the grid, a shift's dialog, and publish.
 * Publishing notifies nobody; the manager tells the team or exports the week.
 */
export function SchedulePanel({ app, week, onChanged }: { app: AppContext; week: LaborWeek; onChanged: () => void }) {
  const schedule = week.schedule
  const deferred = useDeferred()
  const [generating, setGenerating] = useState(false)
  const [role, setRole] = useState<string>('all')
  const [mode, setMode] = useState<'week' | 'day'>('week')
  const [day, setDay] = useState(week.days[0]?.date ?? week.week_of)
  const [editing, setEditing] = useState<ScheduleShift | 'new' | null>(null)
  const [warnings, setWarnings] = useState<PublishWarning[] | null>(null)
  const [rules, setRules] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const wide = useWide(1024)

  const generate = async () => {
    setGenerating(true)
    try {
      const { job_id } = await generateSchedule(week.week_of)
      for (let i = 0; i < 90; i++) {
        const job = await fetchJob(job_id)
        if (job.status === 'done') break
        if (job.status === 'failed') throw new Error(job.error ?? 'The schedule was not generated.')
        await new Promise((resolve) => window.setTimeout(resolve, 700))
      }
      onChanged()
    } catch (err) {
      app.toast('The schedule was not generated', err instanceof Error ? err.message : 'Try again.')
    } finally {
      setGenerating(false)
    }
  }
  const publish = async (confirm: boolean) => {
    if (!schedule) return
    try {
      const out = await publishSchedule(schedule.id, confirm)
      if (!out.published) setWarnings(out.warnings)
      else {
        setWarnings(null)
        app.refresh()
        onChanged()
      }
    } catch (err) {
      app.toast('The schedule was not published', err instanceof Error ? err.message : 'Try again.')
    } finally {
      setPublishing(false)
    }
  }
  // Publish waits out its ten seconds of Undo, like every approval.
  const startPublish = (confirm: boolean) => {
    if (!schedule) return
    setWarnings(null)
    setPublishing(true)
    deferred.start(schedule.id, 'Schedule published.', () => void publish(confirm), () => setPublishing(false))
  }
  const askPublish = async () => {
    if (!schedule) return
    try {
      const out = await publishSchedule(schedule.id, false)
      if (out.published) {
        app.refresh()
        onChanged()
      } else setWarnings(out.warnings)
    } catch (err) {
      app.toast('The schedule was not published', err instanceof Error ? err.message : 'Try again.')
    }
  }
  const download = async () => {
    if (!schedule) return
    try {
      const res = await fetch(apiUrl(scheduleCsvUrl(schedule.id)), { credentials: 'include' })
      if (!res.ok) throw new Error('The export failed.')
      const link = document.createElement('a')
      link.href = URL.createObjectURL(await res.blob())
      link.download = `schedule-${week.week_of}.csv`
      link.click()
      URL.revokeObjectURL(link.href)
    } catch (err) {
      app.toast('The schedule was not exported', err instanceof Error ? err.message : 'Try again.')
    }
  }

  const toolbar = (
    <div className="ml-header-meta">
      {schedule && <StatusWord tone={schedule.status === 'published' && !publishing ? 'good' : 'neutral'}>{schedule.status === 'published' ? `Published v${schedule.version}` : `Draft v${schedule.version}`}</StatusWord>}
      {app.canManage && (
        <button className="btn btn-secondary btn-sm" disabled={generating} onClick={() => void generate()}>
          {schedule ? 'Regenerate' : 'Generate'}
        </button>
      )}
      {schedule && app.canManage && schedule.status === 'draft' && !publishing && (
        <button className="btn btn-primary btn-sm" onClick={() => void askPublish()}>
          Publish
        </button>
      )}
      {schedule && (
        <button className="btn btn-secondary btn-sm" onClick={() => void download()}>
          Export CSV
        </button>
      )}
      {schedule && (
        <button className="btn btn-secondary btn-sm" onClick={() => window.print()}>
          Print
        </button>
      )}
      {app.canManage && (
        <button className="btn btn-secondary btn-sm" onClick={() => setRules(true)}>
          Rules
        </button>
      )}
    </div>
  )
  if (!schedule) {
    return (
      <section className="card ml-pad" aria-label="Schedule" data-testid="schedule">
        <div className="ml-order-foot">
          <span>{generating ? 'Generating the schedule. This takes up to 30 seconds.' : `No schedule yet for the week of ${dayLabel(week.week_of)}.`}</span>
          {toolbar}
        </div>
        {rules && <RulesEditor app={app} onClose={() => setRules(false)} onSaved={() => { setRules(false); onChanged() }} />}
      </section>
    )
  }

  const names = new Map(schedule.people.map((p) => [p.id, p.name]))
  const dayIndex = new Map(week.days.map((d, i) => [d.date, i]))
  const shown = schedule.shifts.filter((s) => role === 'all' || s.role_key === role)
  const toGrid = (s: ScheduleShift): GridShift => ({ id: s.id, day: dayIndex.get(s.date) ?? 0, start: short(s.start), end: short(s.end), role: roleName(s.role_key), locked: s.locked, note: s.note || undefined })
  const rows: GridRow[] = schedule.people
    .filter((p) => role === 'all' || p.roles.includes(role))
    .map((p) => ({ id: p.id, name: p.name, shifts: shown.filter((s) => s.staff_id === p.id).map(toGrid), hours: schedule.hours_by_person[p.id] ?? 0, overtime: schedule.overtime.includes(p.id) }))
  const open = shown.filter((s) => !s.staff_id).map(toGrid)
  const roles = [...new Set(schedule.shifts.map((s) => s.role_key))].sort()
  const pick = (g: GridShift) => app.canManage && setEditing(schedule.shifts.find((s) => s.id === g.id) ?? null)
  const dayShifts = shown.filter((s) => s.date === day)

  return (
    <section className="card" aria-label="Schedule" data-testid="schedule">
      <div className="card-header">
        <h2 className="card-title">Schedule, week of {dayLabel(week.week_of)}</h2>
        {toolbar}
      </div>
      <div className="card-body ml-stack">
        {generating && <StateBanner kind="learning" text="Generating the schedule. This takes up to 30 seconds." />}
        {schedule.unset_rules.length > 0 && (
          <StateBanner kind="learning" text={`${schedule.unset_rules.length} labor rules are not set and were not checked. FullHouse gives no legal advice.`} action={app.canManage ? { label: 'Set rules', onClick: () => setRules(true) } : undefined} />
        )}
        {schedule.violations.length > 0 && <StateBanner kind="stale" text={`${schedule.violations.length} ${schedule.violations.length === 1 ? 'shift breaks' : 'shifts break'} a rule: ${schedule.violations[0].person} ${schedule.violations[0].rule}.`} />}
        <div className="ml-grid-tools">
          <div className="ml-tabs" role="group" aria-label="Roles">
            {['all', ...roles].map((r) => (
              <button key={r} className={`ml-tab${role === r ? ' active' : ''}`} aria-pressed={role === r} onClick={() => setRole(r)}>
                {r === 'all' ? 'All' : roleName(r)}
              </button>
            ))}
          </div>
          <div className="ml-tabs" role="group" aria-label="View">
            <button className={`ml-tab${mode === 'week' ? ' active' : ''}`} aria-pressed={mode === 'week'} onClick={() => setMode('week')}>
              Week
            </button>
            <button className={`ml-tab${mode === 'day' ? ' active' : ''}`} aria-pressed={mode === 'day'} onClick={() => setMode('day')}>
              Day
            </button>
          </div>
          {app.canManage && (
            <button className="btn btn-secondary btn-sm" onClick={() => setEditing('new')}>
              Add shift
            </button>
          )}
        </div>
        {!wide ? (
          <ul className="ml-day-list" aria-label="Shifts by day">
            {week.days.map((d) => (
              <li key={d.date}>
                <div className="strong">{dayLabel(d.date)}</div>
                {shown.filter((s) => s.date === d.date).map((s) => (
                  <div key={s.id} className="small">
                    {clock(s.start)} to {clock(s.end)}, {roleName(s.role_key)}: {s.staff_id ? names.get(s.staff_id) : 'Open shift'}
                    {s.locked ? ', locked' : ''}
                  </div>
                ))}
                {shown.every((s) => s.date !== d.date) && <div className="muted small">Nobody scheduled</div>}
              </li>
            ))}
          </ul>
        ) : mode === 'week' ? (
          <ScheduleGrid days={week.days.map((d) => dayLabel(d.date).split(' ').slice(0, 1).concat(dayLabel(d.date).split(' ').slice(2)).join(' '))} open={open} rows={rows} onPick={pick} />
        ) : (
          <DayTimeline app={app} schedule={schedule} shifts={dayShifts} day={day} days={week.days.map((d) => d.date)} names={names} onDay={setDay} onPick={(s) => app.canManage && setEditing(s)} onChanged={onChanged} />
        )}
      </div>
      {editing && <ShiftDialog schedule={schedule} shift={editing === 'new' ? null : editing} days={week.days.map((d) => d.date)} roles={roles} defaultDay={day} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); onChanged() }} />}
      {warnings && (
        <SidePanel title="Before you publish" onClose={() => setWarnings(null)}>
          <div className="ml-stack">
            <ul className="ml-checks" data-testid="publish-warnings">
              {warnings.map((w, i) => (
                <li key={i}>{warningText(w)}</li>
              ))}
            </ul>
            <p className="muted small">Publishing notifies nobody. Tell the team, or export the week.</p>
            <div className="row">
              <button className="btn btn-primary" onClick={() => startPublish(true)}>
                Publish anyway
              </button>
              <button className="btn btn-secondary" onClick={() => setWarnings(null)}>
                Cancel
              </button>
            </div>
          </div>
        </SidePanel>
      )}
      {rules && <RulesEditor app={app} onClose={() => setRules(false)} onSaved={() => { setRules(false); onChanged() }} />}
      {deferred.bar}
    </section>
  )
}

function useWide(px: number): boolean {
  const [wide, setWide] = useState(() => window.innerWidth >= px)
  useEffect(() => {
    const on = () => setWide(window.innerWidth >= px)
    window.addEventListener('resize', on)
    return () => window.removeEventListener('resize', on)
  }, [px])
  return wide
}

/** One day by the hour: a bar per shift. Drag a bar's right edge to change when it ends; the dialog and the arrow keys do the same. */
function DayTimeline({ app, schedule, shifts, day, days, names, onDay, onPick, onChanged }: { app: AppContext; schedule: Schedule; shifts: ScheduleShift[]; day: string; days: string[]; names: Map<string, string>; onDay: (d: string) => void; onPick: (s: ScheduleShift) => void; onChanged: () => void }) {
  const first = Math.min(600, ...shifts.map((s) => s.start))
  const last = Math.max(1320, ...shifts.map((s) => s.end))
  const from = Math.floor(first / 60) * 60
  const span = Math.ceil(last / 60) * 60 - from
  const track = useRef<HTMLDivElement>(null)
  const [drag, setDrag] = useState<{ id: string; end: number } | null>(null)
  const pct = (m: number) => `${((m - from) / span) * 100}%`
  const setEnd = async (shift: ScheduleShift, end: number) => {
    if (end === shift.end || end <= shift.start) return
    try {
      await editShift(shift.id, { schedule_id: schedule.id, end })
      onChanged()
    } catch (err) {
      app.toast('The shift was not changed', err instanceof Error ? err.message : 'Try again.')
    }
  }
  const dragTo = (clientX: number, shift: ScheduleShift) => {
    const box = track.current?.getBoundingClientRect()
    if (!box) return shift.end
    const minutes = from + ((clientX - box.left) / box.width) * span
    return Math.max(shift.start + 30, Math.min(from + span, Math.round(minutes / 30) * 30))
  }
  const hours = Array.from({ length: span / 60 + 1 }, (_, i) => from + i * 60)
  return (
    <div className="ml-timeline" data-testid="day-timeline">
      <label className="field ml-inline">
        <span className="field-label">Day shown</span>
        <select className="input" value={day} onChange={(e) => onDay(e.target.value)}>
          {days.map((d) => (
            <option key={d} value={d}>
              {dayLabel(d)}
            </option>
          ))}
        </select>
      </label>
      <div className="ml-timeline-axis" aria-hidden="true">
        {hours.map((h) => (
          <span key={h} style={{ left: pct(h) }}>
            {short(h)}
          </span>
        ))}
      </div>
      <div ref={track} className="ml-timeline-rows">
        {shifts.length === 0 && <p className="muted">Nobody is scheduled that day.</p>}
        {shifts.map((s) => {
          const end = drag?.id === s.id ? drag.end : s.end
          const who = s.staff_id ? names.get(s.staff_id) : 'Open shift'
          return (
            <div key={s.id} className="ml-timeline-row">
              <button
                className={`ml-shift ml-timeline-bar${s.locked ? ' locked' : ''}`}
                style={{ left: pct(s.start), width: `${((end - s.start) / span) * 100}%` }}
                onClick={() => onPick(s)}
                onKeyDown={(e) => {
                  if (!app.canManage || (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft')) return
                  e.preventDefault()
                  void setEnd(s, s.end + (e.key === 'ArrowRight' ? 30 : -30))
                }}
              >
                <span className="ml-shift-time">
                  {short(s.start)}–{short(end)}
                </span>
                <span className="ml-shift-role">
                  {who}, {roleName(s.role_key)}
                  {s.locked ? ', locked' : ''}
                </span>
                <span className="visually-hidden">Arrow keys change the end.</span>
              </button>
              {app.canManage && (
                <span
                  className="ml-timeline-handle"
                  style={{ left: pct(end) }}
                  aria-hidden="true"
                  onPointerDown={(e) => {
                    e.currentTarget.setPointerCapture(e.pointerId)
                    setDrag({ id: s.id, end: s.end })
                  }}
                  onPointerMove={(e) => drag?.id === s.id && setDrag({ id: s.id, end: dragTo(e.clientX, s) })}
                  onPointerUp={(e) => {
                    const to = dragTo(e.clientX, s)
                    setDrag(null)
                    void setEnd(s, to)
                  }}
                />
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function ShiftDialog({ schedule, shift, days, roles, defaultDay, onClose, onSaved }: { schedule: Schedule; shift: ScheduleShift | null; days: string[]; roles: string[]; defaultDay: string; onClose: () => void; onSaved: () => void }) {
  const [day, setDay] = useState(shift?.date ?? defaultDay)
  const [role, setRole] = useState(shift?.role_key ?? roles[0] ?? 'server')
  const [start, setStart] = useState(shift?.start ?? 17 * 60)
  const [end, setEnd] = useState(shift?.end ?? 22 * 60)
  const [staff, setStaff] = useState(shift?.staff_id ?? '')
  const [locked, setLocked] = useState(shift?.locked ?? false)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const people = schedule.people.filter((p) => p.roles.includes(role) || p.id === staff)
  const run = async (call: () => Promise<unknown>, failed: string) => {
    setSaving(true)
    setError(null)
    try {
      await call()
      onSaved()
    } catch (err) {
      setError(err instanceof Error ? `${failed} ${err.message}` : `${failed} Try again.`)
    } finally {
      setSaving(false)
    }
  }
  const save = () => {
    if (end <= start) {
      setError('The shift must end after it starts.')
      return
    }
    const body = { schedule_id: schedule.id, start, end, role_key: role, locked, ...(staff ? { staff_id: staff } : { unassign: true }) }
    void run(() => (shift ? editShift(shift.id, body) : addShift({ ...body, date: day })), 'The shift was not saved.')
  }
  return (
    <SidePanel title={shift ? 'Edit shift' : 'Add shift'} onClose={onClose}>
      <div className="ml-stack">
        {!shift && (
          <label className="field">
            <span className="field-label">Day</span>
            <select className="input" value={day} onChange={(e) => setDay(e.target.value)}>
              {days.map((d) => (
                <option key={d} value={d}>
                  {dayLabel(d)}
                </option>
              ))}
            </select>
          </label>
        )}
        {shift && <p className="strong">{dayLabel(shift.date)}</p>}
        <label className="field">
          <span className="field-label">Role</span>
          <select className="input" value={role} onChange={(e) => setRole(e.target.value)}>
            {[...new Set([...roles, role])].map((r) => (
              <option key={r} value={r}>
                {roleName(r)}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Person</span>
          <select className="input" value={staff} onChange={(e) => setStaff(e.target.value)}>
            <option value="">Open shift</option>
            {people.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Starts</span>
          <select className="input" value={start} onChange={(e) => setStart(Number(e.target.value))}>
            {TIMES.slice(0, 60).map((t) => (
              <option key={t} value={t}>
                {clock(t)}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Ends</span>
          <select className="input" value={end} onChange={(e) => setEnd(Number(e.target.value))}>
            {TIMES.slice(1).map((t) => (
              <option key={t} value={t}>
                {clock(t)}
                {t >= 1440 ? ', next day' : ''}
              </option>
            ))}
          </select>
        </label>
        <label className="ml-check">
          <input type="checkbox" checked={locked} onChange={(e) => setLocked(e.target.checked)} />
          <span>Lock: keep this shift when the week is regenerated</span>
        </label>
        {error && <StateBanner kind="error" text={error} />}
        <div className="row">
          <button className="btn btn-primary" disabled={saving} onClick={save}>
            Save
          </button>
          {shift && (
            <button className="btn btn-secondary" disabled={saving} onClick={() => void run(() => removeShift(shift.id, schedule.id), 'The shift was not removed.')}>
              Remove
            </button>
          )}
          <button className="btn btn-secondary" onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    </SidePanel>
  )
}

/** The owner's labor rules. Empty until confirmed; an empty rule is not checked. */
function RulesEditor({ app, onClose, onSaved }: { app: AppContext; onClose: () => void; onSaved: () => void }) {
  const [values, setValues] = useState<Record<string, string> | null>(null)
  const [onCall, setOnCall] = useState(false)
  const [latest, setLatest] = useState('')
  const [confirmed, setConfirmed] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    fetchLaborRules()
      .then((r) => {
        setValues(Object.fromEntries(Object.keys(RULE_WORDS).map((k) => [k, r[k] == null ? '' : String(r[k])])))
        setOnCall(r.allow_on_call === true)
        setLatest(typeof r.minor_latest_end === 'string' ? r.minor_latest_end : '')
        setConfirmed(typeof r.confirmed_by === 'string' ? r.confirmed_by : null)
      })
      .catch(() => setError('The rules did not load. Close this panel and try again.'))
  }, [])
  const save = async () => {
    if (!values) return
    const body: LaborRulesBody = { allow_on_call: onCall, minor_latest_end: latest || null }
    for (const [key, text] of Object.entries(values)) {
      if (text !== '' && !(Number(text) >= 0)) {
        setError(`${RULE_WORDS[key]} must be a number, or left empty.`)
        return
      }
      body[key] = text === '' ? null : Number(text)
    }
    try {
      await saveLaborRules(body)
      app.toast('Rules confirmed', 'The next draft is checked against them.')
      onSaved()
    } catch (err) {
      setError(err instanceof Error ? `The rules were not saved. ${err.message}` : 'The rules were not saved. Try again.')
    }
  }
  return (
    <SidePanel title="Labor rules" onClose={onClose}>
      <div className="ml-stack">
        <p className="muted small">These are your rules for your location. A rule left empty is not checked. FullHouse gives no legal advice.{confirmed ? ` Last confirmed by ${confirmed}.` : ''}</p>
        {values &&
          Object.keys(RULE_WORDS).map((key) => (
            <label key={key} className="field">
              <span className="field-label">{RULE_WORDS[key]}</span>
              <input className="input" inputMode="decimal" value={values[key]} onChange={(e) => setValues({ ...values, [key]: e.target.value })} />
            </label>
          ))}
        <label className="field">
          <span className="field-label">Minors: latest end, 24-hour</span>
          <input className="input" value={latest} onChange={(e) => setLatest(e.target.value)} />
        </label>
        <label className="ml-check">
          <input type="checkbox" checked={onCall} onChange={(e) => setOnCall(e.target.checked)} />
          <span>Allow on-call shifts for a busy service</span>
        </label>
        {error && <StateBanner kind="error" text={error} />}
        <div className="row">
          <button className="btn btn-primary" disabled={!values} onClick={() => void save()}>
            Confirm rules
          </button>
          <button className="btn btn-secondary" onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    </SidePanel>
  )
}
