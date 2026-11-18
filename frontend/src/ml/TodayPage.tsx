import { useState } from 'react'
import type { AppContext } from '../app/context'
import { useLoad } from '../lib/useLoad'
import { decideAdjustment, fetchLaborToday, type LaborSuggestion, type LaborToday } from './api'
import { CoverageChart, PaceChart } from './charts'
import { dayLabel, dollars, percent, timeLabel } from './format'
import { HeaderSentence, LoadFailed, SetupChecklist, type SetupStep, Skeleton, StateBanner, StatTile, SuggestionCard, TileRow, useDeferred, useWhy, ViewAsTable, type Why } from './ui'

const whole = (n: number) => Math.round(n).toLocaleString('en-US')

/** The first sentence of Today: how the day is running against the forecast. */
export function paceSentence(data: LaborToday): string {
  if (!data.pace || !data.so_far) {
    const done = data.setup.filter((s) => s.done).length
    return `${done} of ${data.setup.length} setup steps are done.`
  }
  const counts = `${whole(data.so_far.actual)} ${data.primary} so far, ${whole(data.so_far.expected)} expected.`
  if (!data.pace.adjusted || data.pace.pace == null) return `Too early to say. ${counts}`
  const gap = data.pace.pace - 1
  if (Math.abs(gap) < 0.02) return `On forecast. ${counts}`
  return `Running ${percent(Math.abs(gap))} ${gap > 0 ? 'above' : 'below'} forecast. ${counts}`
}

export function suggestionWords(s: LaborSuggestion): { sentence: string; reason: string } {
  const role = s.role.toLowerCase()
  const at = timeLabel(s.suggested_time)
  const sentence = s.kind === 'cut' ? `Cut one ${role} at ${at}.` : s.kind === 'extend' ? `Keep one ${role} until ${at}.` : `Call in one ${role} for ${at}.`
  const who = s.reason.person
  const reason = { closest_to_overtime: `${who} is closest to overtime.`, longest_on_shift: `${who} has been on longest.`, ends_soonest: `${who} finishes soonest.`, flex: `${who} is on call.` }[s.reason.why]
  return { sentence, reason }
}

const WHY_OFF: Record<string, string> = {
  learning: 'Suggestions start with 8 weeks of data.',
  early: 'Suggestions start once 15% of the day has passed.',
  accuracy: 'Suggestions are off while the average miss for this part of the day is over 25%.',
}

/** Today, for a phone held in one hand during service: one column, no table. */
export function TodayPage({ app }: { app: AppContext }) {
  // "?at=2026-11-17T19:15:00" reads the page as of that moment on the restaurant's clock, where the site allows it.
  const [at] = useState(() => new URLSearchParams(window.location.hash.split('?')[1] ?? '').get('at'))
  const load = useLoad(() => fetchLaborToday(at), [at])
  const why = useWhy()
  const deferred = useDeferred()
  const [decided, setDecided] = useState<'accept' | 'dismiss' | null>(null)
  const [role, setRole] = useState<string | null>(null)
  const data = load.data
  const title = 'Today'
  if (load.status === 'loading') return <main className="page ml-page ml-today"><Skeleton title={title} /></main>
  if (!data) return <main className="page ml-page ml-today"><LoadFailed title={title} what="Today" onRetry={() => void load.reload()} /></main>
  const updated = timeLabel(data.now)
  if (!data.pace || !data.so_far || !data.day || !data.chart) {
    const steps: SetupStep[] = data.setup.map((s) =>
      s.key === 'orders'
        ? { label: 'Import 4 weeks of orders', detail: `${s.count} of ${s.of ?? 4} weeks of orders with times are in.`, done: s.done, action: { label: 'Open connections', href: '#connect' } }
        : s.key === 'standards'
          ? { label: 'Set a standard', detail: 'A standard says how much one person handles an hour.', done: s.done, action: { label: 'Open standards', href: '#labor/standards' } }
          : { label: 'Forecast the week', detail: 'Today is read against the forecast made overnight.', done: s.done, action: { label: 'Open labor', href: '#labor' } },
    )
    return (
      <main className="page ml-page ml-today">
        <HeaderSentence title={title} sentence={paceSentence(data)} state="setup" />
        <SetupChecklist title="Before today can show" steps={steps} />
      </main>
    )
  }
  const suggestion = decided ? null : data.suggestion
  const words = suggestion ? suggestionWords(suggestion) : null
  const decide = (s: LaborSuggestion, decision: 'accept' | 'dismiss') => {
    setDecided(decision)
    deferred.start(s.id, decision === 'accept' ? 'Suggestion accepted. Tell the person yourself.' : 'Suggestion dismissed.', () => {
      decideAdjustment(s.id, decision, at)
        .then(() => load.reload())
        .then(() => setDecided(null))
        .catch((err: unknown) => {
          setDecided(null)
          app.toast('The decision was not saved', err instanceof Error ? err.message : 'Try again.')
        })
    }, () => setDecided(null))
  }
  const gap = [...data.people].sort((a, b) => Math.abs(b.on - b.needed) - Math.abs(a.on - a.needed))[0]
  const shownRole = role ?? suggestion?.role_key ?? gap?.role_key ?? Object.keys(data.coverage)[0]
  const steps = (shownRole && data.coverage[shownRole]) || []
  const roleName = data.people.find((p) => p.role_key === shownRole)?.name ?? ''
  const pace = data.pace
  const paceWhy = (): Why => ({
    result: `${whole(data.day?.revised ?? 0)} ${data.primary} expected today`,
    formula: 'Revised forecast = forecast x multiplier. Multiplier = weight x pace + (1 - weight), where weight = share of the day passed / 0.40, at most 1',
    inputs: [
      { label: `${data.primary.charAt(0).toUpperCase()}${data.primary.slice(1)} so far`, value: whole(data.so_far?.actual ?? 0), source: { label: 'Point of sale' } },
      { label: 'Forecast so far', value: whole(data.so_far?.expected ?? 0), source: { label: 'Overnight forecast' } },
      { label: 'Share of the day passed', value: percent(pace.elapsed_share) },
      { label: 'Multiplier', value: pace.multiplier.toFixed(4) },
    ],
    dataBasis: `${data.weeks_of_data} weeks of orders. No change is made before 15% of the day has passed`,
    freshness: `Computed ${dayLabel(data.now)} at ${updated}.`,
    fix: { label: 'Open standards', href: '#labor/standards' },
  })
  return (
    <main className="page ml-page ml-today">
      <a className="ml-back" href="#labor">
        Labor
      </a>
      <HeaderSentence title={`Today, ${dayLabel(data.today)}`} sentence={paceSentence(data)} updated={updated} state={data.state} />
      {data.stale && <StateBanner kind="stale" text="No orders are in for today. Import today's orders, or reconnect the point of sale." action={{ label: 'Open connections', href: '#connect' }} />}
      {data.state === 'learning' && <StateBanner kind="learning" text={`Estimate. ${data.weeks_of_data} weeks of data; needs 8.`} />}
      {suggestion && words && app.canManage && (
        <SuggestionCard sentence={words.sentence} saving={suggestion.kind === 'cut' ? suggestion.savings_estimate : null} reason={words.reason} expires={timeLabel(suggestion.expires_at)} onAccept={() => decide(suggestion, 'accept')} onDismiss={() => decide(suggestion, 'dismiss')} onWhy={() => why.show(paceWhy())} />
      )}
      {!data.suggestion && !data.suggestions_on && app.canManage && data.suggestions_why !== 'on' && <p className="muted small" data-testid="suggestions-off">{WHY_OFF[data.suggestions_why]}</p>}
      <TileRow>
        <StatTile value={whole(data.so_far.actual)} label={`${data.primary.charAt(0).toUpperCase()}${data.primary.slice(1)} so far`} compare={`expected ${whole(data.so_far.expected)}`} onWhy={() => why.show(paceWhy())} />
        {gap && <StatTile value={`${gap.on} on`} label={gap.name} compare={`${gap.needed} needed now, ${gap.needed_if_busy} if busy`} tone={gap.on < gap.needed ? 'bad' : undefined} />}
        {data.labor_so_far?.pct != null && <StatTile value={percent(data.labor_so_far.pct)} label="Labor % today" compare={`${dollars(data.labor_so_far.cost)} of ${dollars(data.labor_so_far.net)} sales so far`} />}
      </TileRow>
      <ViewAsTable
        title={`${whole(data.day.revised)} ${data.primary} are now expected today, against ${whole(data.day.expected)} this morning`}
        summary={`${data.primary} so far today counted up against the forecast, with the revised forecast for the rest of the day.`}
        chart={<PaceChart points={data.chart.points.map((p) => ({ time: p.start, low: p.low, expected: p.expected, high: p.high, actual: p.actual, revised: p.revised }))} now={data.chart.now_index} unit={data.primary} labelOf={(iso) => timeLabel(iso).replace(':00', '')} />}
        columns={['Time', 'Actual', 'Forecast this morning', 'Revised']}
        rows={data.chart.points.map((p) => [timeLabel(p.start), p.actual == null ? 'Not yet' : whole(p.actual), whole(p.expected), p.revised == null ? 'Passed' : whole(p.revised)])}
      />
      {steps.length > 0 && (
        <section className="card" aria-label="People on against people needed" data-testid="today-coverage">
          <div className="card-header">
            <h2 className="card-title">{roleName}: people on against people needed, to close</h2>
            <label className="field ml-inline">
              <span className="field-label">Role</span>
              <select className="input" value={shownRole} onChange={(e) => setRole(e.target.value)}>
                {data.people.map((p) => (
                  <option key={p.role_key} value={p.role_key}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="card-body">
            <CoverageChart steps={steps.map((s) => ({ label: s.label, need: s.need, needIfBusy: s.need_if_busy, scheduled: s.scheduled }))} role={roleName} now={0} />
          </div>
        </section>
      )}
      {deferred.bar}
      {why.panel}
    </main>
  )
}
