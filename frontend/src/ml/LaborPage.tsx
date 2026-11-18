import { useEffect, useRef, useState } from 'react'
import type { AppContext } from '../app/context'
import { readHashParam } from '../lib/useHashRoute'
import { useLoad } from '../lib/useLoad'
import { fetchLaborWeek, fetchStandards, type LaborWeek, previewStandard, resetStandard, saveStandard, type StandardPreview, type StandardRow, type Standards } from './api'
import { CoverageChart, Histogram, RangeBars, SortedBars } from './charts'
import { dayLabel, dollars, percent, timeLabel } from './format'
import { SchedulePanel } from './SchedulePanel'
import { ConfidenceLabel, HeaderSentence, LoadFailed, SetupChecklist, type SetupStep, SidePanel, Skeleton, StateBanner, StatTile, StatusWord, TileRow, useWhy, ViewAsTable, type Why } from './ui'

const hr = (hours: number) => `${(Math.round(hours * 10) / 10).toLocaleString('en-US')} hr`
const whole = (n: number) => Math.round(n).toLocaleString('en-US')
const WEEKDAY = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString('en-US', { weekday: 'long' })
const DRIVERS: Record<string, string> = { covers: 'Covers', orders: 'Orders', items: 'Items', net: 'Sales', none: 'Fixed blocks' }

export function LaborPage({ app }: { app: AppContext }) {
  const [param, setParam] = useState(readHashParam)
  useEffect(() => {
    const onChange = () => setParam(readHashParam())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  if (param === 'standards') return <StandardsPage app={app} />
  return <WeekPage app={app} weekOf={param.startsWith('week/') ? param.slice(5) : 'next'} />
}

export function weekSentence(data: LaborWeek): string {
  if (data.state === 'setup' || !data.totals) {
    const done = data.setup.filter((s) => s.done).length
    return `${done} of ${data.setup.length} setup steps are done.`
  }
  const s = data.schedule
  const week = dayLabel(data.week_of).split(' ').slice(1).join(' ')
  if (s) {
    const kind = s.status === 'published' ? 'Published' : 'Draft'
    const hours = `${whole(Number(s.hours_total))} hours`
    if (s.cost_total != null && s.labor_pct != null) {
      const target = data.target_labor_pct ? ` Target ${percent(data.target_labor_pct)}.` : ''
      return `${kind} for ${week}: ${hours}, ${dollars(s.cost_total)}, ${percent(s.labor_pct)} of expected sales.${target}`
    }
    return `${kind} for ${week}: ${hours} scheduled, ${whole(data.totals.hours_p50)} needed.`
  }
  return `Week of ${week} needs about ${whole(data.totals.hours_p50)} hours at expected sales, ${whole(data.totals.hours_p90)} if busy.`
}

function WeekPage({ app, weekOf }: { app: AppContext; weekOf: string }) {
  const load = useLoad(() => fetchLaborWeek(weekOf), [weekOf])
  const why = useWhy()
  const [role, setRole] = useState<string | null>(null)
  const [day, setDay] = useState<string | null>(null)
  const [tab, setTab] = useState<'demand' | 'cost'>('demand')
  const data = load.data
  const title = 'Labor'
  if (load.status === 'loading') return <main className="page ml-page"><Skeleton title={title} /></main>
  if (!data) return <main className="page ml-page"><LoadFailed title={title} what="The labor week" onRetry={() => void load.reload()} /></main>

  const updated = data.computed_at ? `${dayLabel(data.computed_at)} ${timeLabel(data.computed_at)}` : undefined
  if (data.state === 'setup') {
    const steps: SetupStep[] = data.setup.map((s) =>
      s.key === 'orders'
        ? { label: 'Import 4 weeks of orders', detail: `${s.count} of ${s.of ?? 4} weeks of orders with times are in.`, done: s.done, action: { label: 'Open connections', href: '#connect' } }
        : { label: 'Set a standard', detail: 'A standard says how much one person handles an hour.', done: s.done, action: { label: 'Open standards', href: '#labor/standards' } },
    )
    return (
      <main className="page ml-page">
        <HeaderSentence title={title} sentence={weekSentence(data)} state="setup" />
        <SetupChecklist title="Before people needed can show" steps={steps} />
      </main>
    )
  }
  const totals = data.totals!
  const unit = data.primary
  const known = data.days.filter((d) => d.expected != null)
  const busiest = [...known].sort((a, b) => (b.expected ?? 0) - (a.expected ?? 0))[0]
  const peakRole = [...data.roles].sort((a, b) => b.peak - a.peak)[0]
  const shownRole = role ?? data.roles[0]?.role_key ?? null
  const shownDay = day ?? busiest?.date ?? data.days[0]?.date
  const steps = (shownRole && shownDay && data.intervals[shownDay]?.[shownRole]) || []
  const roleName = data.roles.find((r) => r.role_key === shownRole)?.name ?? ''
  const sched = data.schedule
  const costDays = (sched?.cost_by_day ?? [])
    .filter((d) => d.net_expected && d.net_low && d.net_high && Number(d.cost) > 0)
    .map((d) => ({ date: d.date, expected: (Number(d.cost) / (d.net_expected as number)) * 100, low: (Number(d.cost) / (d.net_high as number)) * 100, high: (Number(d.cost) / (d.net_low as number)) * 100 }))
  const costliest = [...costDays].sort((a, b) => b.expected - a.expected)[0]
  const costWhy = (): Why => ({
    result: `${percent(sched?.labor_pct ?? 0)} of expected sales`,
    formula: 'Labor % = total shift cost / expected sales. Shift cost = paid hours x hourly rate, with the role median for an unknown rate',
    inputs: [
      { label: 'Total shift cost', value: dollars(sched?.cost_total ?? 0) },
      { label: 'Expected sales', value: dollars(sched?.net_expected ?? 0) },
      { label: 'Paid hours', value: hr(Number(sched?.hours_total ?? 0)) },
    ],
    dataBasis: `${data.weeks_of_data} weeks of orders, and the rates on the team list`,
    freshness: updated ? `Computed ${updated}.` : 'Not computed yet.',
    fix: { label: 'Open standards', href: '#labor/standards' },
  })
  const hoursWhy = (): Why => ({
    result: `${hr(totals.hours_p50)} needed`,
    formula: 'People needed each half hour = expected units / (standard x 0.5), rounded up, and never under the minimum on floor',
    inputs: data.roles.map((r) => ({ label: r.name, value: `${hr(r.hours_p50)}, ${hr(r.hours_p90)} if busy`, source: { label: 'Standards', href: '#labor/standards' } })),
    dataBasis: `${data.weeks_of_data} weeks of orders with times, and each role's standard`,
    freshness: updated ? `Computed ${updated}.` : 'Not computed yet.',
    fix: { label: 'Open standards', href: '#labor/standards' },
  })
  return (
    <main className={`page ml-page${data.stale ? ' ml-stale' : ''}`}>
      <HeaderSentence title={title} sentence={weekSentence(data)} updated={updated} state={data.state}>
        <ConfidenceLabel weeks={data.weeks_of_data} needed={8} />
        <a className="btn btn-secondary btn-sm" href="#labor/standards">
          Standards
        </a>
      </HeaderSentence>
      {data.stale && data.last_order_date && <StateBanner kind="stale" text={`Orders were last imported ${dayLabel(data.last_order_date)}. Import a newer export.`} action={{ label: 'Open connections', href: '#connect' }} />}
      {data.state === 'learning' && <StateBanner kind="learning" text={`Estimate. ${data.weeks_of_data} weeks of data; needs 8.`} />}
      {totals.days_forecast < 7 && <StateBanner kind="learning" text={`${totals.days_forecast} of 7 days are forecast. The forecast reaches ${dayLabel(data.horizon_end)}.`} />}
      {sched ? (
        <TileRow>
          <StatTile value={hr(Number(sched.hours_total))} label="Scheduled hours" compare={`${hr(totals.hours_p50)} needed, ${hr(totals.hours_p90)} if busy`} onWhy={() => why.show(hoursWhy())} />
          {sched.labor_pct != null && sched.cost_total != null ? (
            <StatTile value={percent(sched.labor_pct)} label="Labor %" compare={data.target_labor_pct ? `target ${percent(data.target_labor_pct)}` : `${dollars(sched.cost_total)} of ${dollars(sched.net_expected ?? 0)} expected sales`} onWhy={() => why.show(costWhy())} />
          ) : (
            <StatTile value={whole(totals.expected)} label={`Expected ${unit}`} compare={`likely ${whole(totals.low)} to ${whole(totals.high)} for the week`} />
          )}
          <StatTile value={String(sched.open_shifts)} label="Open shifts" compare={`${sched.understaffed_intervals} half hours understaffed`} tone={sched.open_shifts || sched.understaffed_intervals ? 'bad' : 'good'} />
        </TileRow>
      ) : (
        <TileRow>
          <StatTile value={hr(totals.hours_p50)} label="Hours needed" compare={`${hr(totals.hours_p90)} if busy`} onWhy={() => why.show(hoursWhy())} />
          <StatTile value={whole(totals.expected)} label={`Expected ${unit}`} compare={`likely ${whole(totals.low)} to ${whole(totals.high)} for the week`} />
          {peakRole && peakRole.peak_at && <StatTile value={String(peakRole.peak)} label="Peak people" compare={`${peakRole.name}, ${dayLabel(peakRole.peak_at)} ${timeLabel(peakRole.peak_at)}`} />}
        </TileRow>
      )}
      {costDays.length > 0 && (
        <div className="ml-tabs" role="tablist" aria-label="Chart">
          <button role="tab" aria-selected={tab === 'demand'} className={`ml-tab${tab === 'demand' ? ' active' : ''}`} onClick={() => setTab('demand')}>
            Demand
          </button>
          <button role="tab" aria-selected={tab === 'cost'} className={`ml-tab${tab === 'cost' ? ' active' : ''}`} onClick={() => setTab('cost')}>
            Cost
          </button>
        </div>
      )}

      {tab === 'cost' && costDays.length > 0 ? (
        <ViewAsTable
          title={`${WEEKDAY(costliest.date)} has the highest labor share, ${costliest.expected.toFixed(1)}% of expected sales`}
          summary="Labor cost as a share of expected sales for each day, with the range the likely sales give."
          chart={<RangeBars bars={costDays} unit="labor, % of expected sales" markerLabel="Not shown" target={data.target_labor_pct ? { value: Number(data.target_labor_pct) * 100, label: 'Target' } : undefined} hideMarker />}
          columns={['Day', 'Labor %', 'Range']}
          rows={costDays.map((d) => [dayLabel(d.date), `${d.expected.toFixed(1)}%`, `${d.low.toFixed(1)}% to ${d.high.toFixed(1)}%`])}
        />
      ) : known.length > 0 && busiest ? (
        <ViewAsTable
          title={`${WEEKDAY(busiest.date)} is the busiest day, about ${whole(busiest.expected ?? 0)} ${unit}`}
          summary={`Expected ${unit} for each day of the week with the likely range, and last week's actual where the day has closed.`}
          chart={<RangeBars bars={known.map((d) => ({ date: d.date, low: d.low ?? 0, expected: d.expected ?? 0, high: d.high ?? 0, marker: d.last_week, chips: d.drivers }))} unit={unit} markerLabel="Last week" />}
          columns={['Day', 'Expected', 'Likely range', 'Last week', 'Known']}
          rows={known.map((d) => [dayLabel(d.date), whole(d.expected ?? 0), `${whole(d.low ?? 0)} to ${whole(d.high ?? 0)}`, d.last_week == null ? 'Not yet' : whole(d.last_week), d.drivers.join(', ') || 'Nothing'])}
        />
      ) : null}

      <SchedulePanel app={app} week={data} onChanged={() => void load.reload()} />

      <section className="card" aria-label="People needed by role">
        <div className="table-wrap">
          <table className="table ml-table">
            <thead>
              <tr>
                <th>Role</th>
                <th className="num">Hours needed</th>
                <th className="num ml-hide-phone">Hours if busy</th>
                <th className="num ml-hide-phone">Peak people</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {data.roles.map((r) => (
                <tr key={r.role_key}>
                  <td className="strong">{r.name}</td>
                  <td className="num">
                    <button className="ml-why-link ml-cell-link" onClick={() => why.show(hoursWhy())}>
                      {hr(r.hours_p50)}
                    </button>
                  </td>
                  <td className="num ml-hide-phone">{hr(r.hours_p90)}</td>
                  <td className="num ml-hide-phone">{r.peak}</td>
                  <td>
                    <button className="btn btn-secondary btn-sm" onClick={() => setRole(r.role_key)}>
                      See day
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {shownRole && shownDay && (
        <section className="card" aria-label="Day view" data-testid="day-view">
          <div className="card-header">
            <h2 className="card-title">
              {roleName}, {dayLabel(shownDay)}: people needed by half hour
            </h2>
            <div className="ml-header-meta">
              <label className="field ml-inline">
                <span className="field-label">Role</span>
                <select className="input" value={shownRole} onChange={(e) => setRole(e.target.value)}>
                  {data.roles.map((r) => (
                    <option key={r.role_key} value={r.role_key}>
                      {r.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field ml-inline">
                <span className="field-label">Day</span>
                <select className="input" value={shownDay} onChange={(e) => setDay(e.target.value)}>
                  {data.days.map((d) => (
                    <option key={d.date} value={d.date}>
                      {dayLabel(d.date)}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </div>
          <div className="card-body">
            {steps.length ? <CoverageChart steps={steps.map((s) => ({ label: s.label, need: s.need, needIfBusy: s.need_if_busy, scheduled: s.scheduled }))} role={roleName} /> : <p className="muted">Nobody is needed in this role that day.</p>}
          </div>
        </section>
      )}
      {why.panel}
    </main>
  )
}

export function standardsSentence(data: Standards): string {
  const set = data.roles.filter((r) => r.units_per_labor_hour != null)
  const learned = set.filter((r) => r.source === 'learned').length
  return `${set.length} of ${data.roles.length} roles have a standard. ${learned} ${learned === 1 ? 'is' : 'are'} learned from punches.`
}

export function previewSentence(preview: StandardPreview, role: string): string {
  const { before, after, date } = preview.top
  const name = role.toLowerCase()
  if (before === after) return `Leaves ${name} hours at ${whole(preview.week_before)} for the next 7 days.`
  return `${after > before ? 'Raises' : 'Lowers'} ${WEEKDAY(date)} ${name} hours from ${(Math.round(before * 10) / 10).toLocaleString('en-US')} to ${(Math.round(after * 10) / 10).toLocaleString('en-US')}.`
}

function StandardsPage({ app }: { app: AppContext }) {
  const load = useLoad(fetchStandards, [])
  const [tab, setTab] = useState<'standards' | 'accuracy'>('standards')
  const [picked, setPicked] = useState<string | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const data = load.data
  const title = 'Labor standards'
  if (load.status === 'loading') return <main className="page ml-page"><Skeleton title={title} /></main>
  if (!data) return <main className="page ml-page"><LoadFailed title={title} what="Labor standards" onRetry={() => void load.reload()} /></main>
  const updated = data.computed_at ? `${dayLabel(data.computed_at)} ${timeLabel(data.computed_at)}` : undefined
  const shown = data.roles.find((r) => r.role_key === picked) ?? data.roles.find((r) => r.histogram.length > 0) ?? data.roles[0]
  const acc = data.accuracy
  const worst = [...acc.dayparts].sort((a, b) => b.wape - a.wape)[0]
  const worstName = worst ? worst.daypart.charAt(0).toUpperCase() + worst.daypart.slice(1) : ''
  const worstMiss = worst ? percent(worst.wape) : ''
  const edit = editing ? data.roles.find((r) => r.role_key === editing) : undefined
  return (
    <main className="page ml-page">
      <a className="ml-back" href="#labor">
        Labor
      </a>
      <HeaderSentence title={title} sentence={standardsSentence(data)} updated={updated} state={data.state} />
      {data.state === 'learning' && <StateBanner kind="learning" text={`Estimate. ${data.weeks_of_data} weeks of data; needs 8.`} />}
      <div className="ml-tabs" role="tablist" aria-label="Chart">
        <button role="tab" aria-selected={tab === 'standards'} className={`ml-tab${tab === 'standards' ? ' active' : ''}`} onClick={() => setTab('standards')}>
          Standards
        </button>
        <button role="tab" aria-selected={tab === 'accuracy'} className={`ml-tab${tab === 'accuracy' ? ' active' : ''}`} onClick={() => setTab('accuracy')}>
          Accuracy
        </button>
      </div>
      {tab === 'standards' && shown ? (
        <ViewAsTable
          title={shown.units_per_labor_hour ? `${shown.name}: the standard is ${Number(shown.units_per_labor_hour)} ${DRIVERS[shown.driver_metric].toLowerCase()} a labor hour` : `${shown.name} has no standard yet, from ${shown.observed_intervals} half hours`}
          summary={`How often each rate of ${DRIVERS[shown.driver_metric].toLowerCase()} a labor hour was observed for ${shown.name} over 8 weeks, with the standard in use marked.`}
          chart={<Histogram bins={shown.histogram} unit={`${DRIVERS[shown.driver_metric].toLowerCase()} a labor hour`} line={shown.units_per_labor_hour ? { value: Number(shown.units_per_labor_hour), label: `Standard ${Number(shown.units_per_labor_hour)}` } : undefined} />}
          columns={['From', 'To', 'Half hours']}
          rows={shown.histogram.map((b) => [b.from, b.to, b.count])}
        />
      ) : (
        <ViewAsTable
          title={worst ? `${worstName} has the largest average miss, ${worstMiss}` : 'No average miss to show yet, from 0 weeks'}
          summary={`Average miss by daypart over 12 weeks. ${acc.coverage != null ? percent(acc.coverage) : 'No'} of days fell inside the likely range; the target is ${percent(acc.target)}.`}
          chart={
            <>
              <SortedBars bars={acc.dayparts.map((d) => ({ key: d.daypart, label: d.daypart.charAt(0).toUpperCase() + d.daypart.slice(1), value: d.wape * 100, source: 'Source: 12 weeks of orders, forecast week by week' }))} unit="average miss, % of volume, last 12 weeks" format={(v) => `${v.toFixed(1)}%`} />
              {acc.coverage != null && (
                <p className="ml-cover-words" data-testid="range-coverage">
                  {percent(acc.coverage)} of days fell inside the likely range, last 8 weeks. The target is {percent(acc.target)}.
                </p>
              )}
            </>
          }
          columns={['Daypart', 'Average miss']}
          rows={acc.dayparts.map((d) => [d.daypart, percent(d.wape)])}
        />
      )}
      <section className="card" aria-label="Standards by role">
        <div className="table-wrap">
          <table className="table ml-table">
            <thead>
              <tr>
                <th>Role</th>
                <th className="ml-hide-phone">Driver</th>
                <th className="num">Standard</th>
                <th className="num ml-hide-phone">Minimum on floor</th>
                <th className="ml-hide-phone">Source</th>
                <th className="ml-hide-phone">Data</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {data.roles.map((r) => (
                <tr key={r.role_key}>
                  <td>
                    <button className="ml-why-link ml-cell-link" onClick={() => { setPicked(r.role_key); setTab('standards') }}>
                      {r.name}
                    </button>
                  </td>
                  <td className="ml-hide-phone">{DRIVERS[r.driver_metric]}</td>
                  <td className="num">{r.units_per_labor_hour ? `${Number(r.units_per_labor_hour)} an hour` : <span className="muted">Not set</span>}</td>
                  <td className="num ml-hide-phone">{r.min_on_floor}</td>
                  <td className="ml-hide-phone">{r.source === 'manual' ? <StatusWord tone="neutral">Set by you</StatusWord> : r.source === 'learned' ? <StatusWord tone="good">Learned</StatusWord> : <span className="muted">None</span>}</td>
                  <td className="ml-hide-phone">
                    {r.observed_intervals} of {r.needed_intervals} half hours, {r.punch_weeks} weeks of punches
                  </td>
                  <td>
                    {app.canManage && (
                      <button className="btn btn-secondary btn-sm" onClick={() => setEditing(r.role_key)}>
                        Edit
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      {edit && <EditStandard app={app} row={edit} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); void load.reload() }} />}
    </main>
  )
}

/** A standard, with what the change would do shown before it is saved. */
function EditStandard({ app, row, onClose, onSaved }: { app: AppContext; row: StandardRow; onClose: () => void; onSaved: () => void }) {
  const [rate, setRate] = useState(row.units_per_labor_hour ? String(Number(row.units_per_labor_hour)) : row.learned ? String(Number(row.learned.units_per_labor_hour)) : '')
  const [floor, setFloor] = useState(String(row.min_on_floor))
  const [lead, setLead] = useState(String(row.open_lead_minutes))
  const [lag, setLag] = useState(String(row.close_lag_minutes))
  const [preview, setPreview] = useState<StandardPreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const ticket = useRef(0)
  const valid = Number(rate) > 0 && Number.isInteger(Number(floor)) && Number(floor) >= 0 && Number.isInteger(Number(lead)) && Number(lead) >= 0 && Number.isInteger(Number(lag)) && Number(lag) >= 0
  const body = { role_key: row.role_key, units_per_labor_hour: Number(rate).toFixed(4), min_on_floor: Number(floor), open_lead_minutes: Number(lead), close_lag_minutes: Number(lag) }
  useEffect(() => {
    if (!valid) {
      setPreview(null)
      return
    }
    const mine = ++ticket.current
    const timer = window.setTimeout(() => {
      previewStandard(body)
        .then((out) => mine === ticket.current && setPreview(out))
        .catch(() => mine === ticket.current && setPreview(null))
    }, 250)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rate, floor, lead, lag])
  const save = async () => {
    if (!valid) {
      setError('The standard must be more than 0. The other three are whole numbers, 0 or more.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      await saveStandard(body)
      app.toast('Standard saved', 'People needed were worked out again.')
      onSaved()
    } catch (err) {
      setError(err instanceof Error ? `The standard was not saved. ${err.message}` : 'The standard was not saved. Try again.')
    } finally {
      setSaving(false)
    }
  }
  const reset = async () => {
    setSaving(true)
    try {
      await resetStandard(row.role_key)
      onSaved()
    } catch (err) {
      setError(err instanceof Error ? `The learned value was not restored. ${err.message}` : 'The learned value was not restored. Try again.')
    } finally {
      setSaving(false)
    }
  }
  return (
    <SidePanel title={`${row.name} standard`} onClose={onClose}>
      <div className="ml-stack">
        <label className="field">
          <span className="field-label">{DRIVERS[row.driver_metric]} a labor hour</span>
          <input className="input" inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Minimum on floor</span>
          <input className="input" inputMode="numeric" value={floor} onChange={(e) => setFloor(e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Minutes before opening</span>
          <input className="input" inputMode="numeric" value={lead} onChange={(e) => setLead(e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Minutes after closing</span>
          <input className="input" inputMode="numeric" value={lag} onChange={(e) => setLag(e.target.value)} />
        </label>
        {row.learned && (
          <p className="muted small">
            Learned from punches: {Number(row.learned.units_per_labor_hour)} an hour, from {row.learned.sample_size} half hours.
          </p>
        )}
        {preview && (
          <p className="strong" role="status" data-testid="standard-preview">
            {previewSentence(preview, row.name)}
          </p>
        )}
        {error && <StateBanner kind="error" text={error} />}
        <div className="row">
          <button className="btn btn-primary" disabled={saving} onClick={() => void save()}>
            Save
          </button>
          {row.source === 'manual' && row.learned && (
            <button className="btn btn-secondary" disabled={saving} onClick={() => void reset()}>
              Use learned
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
