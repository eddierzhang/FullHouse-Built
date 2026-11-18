import { useEffect, useState } from 'react'
import { approveAction, rejectAction } from '../api/client'
import type { AppContext } from '../app/context'
import { readHashParam } from '../lib/useHashRoute'
import { useLoad } from '../lib/useLoad'
import { correctLine, type DeliveryDay, draftOrder, fetchOrdering, fetchPrep, type OrderDraft, type Ordering, type OrderLine, proposePrep, removeDeliveryDay, saveDeliveryDay, setLineQty } from './api'
import { ForecastChart, StepLines } from './charts'
import { amount, dayLabel, dollars, percent, timeLabel } from './format'
import { ConfidenceLabel, HeaderSentence, LoadFailed, SetupChecklist, type SetupStep, SidePanel, Skeleton, StateBanner, StatTile, StatusWord, TileRow, useDeferred, useWhy, ViewAsTable, type Why } from './ui'

const TITLE = 'Ordering and prep'
const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

export function orderingSentence(data: Ordering): string {
  if (data.state === 'setup' || !data.header) {
    const done = data.setup.filter((s) => s.done).length
    return `${done} of ${data.setup.length} setup steps are done.`
  }
  const n = data.header.orders
  const next = data.header.next_cutoff
  const first = `${n} ${n === 1 ? 'order' : 'orders'} to review.`
  return next ? `${first} Next cutoff: ${next.supplier}, ${dayLabel(next.at)} ${timeLabel(next.at)}.` : first
}

export function OrderingPage({ app }: { app: AppContext }) {
  const [param, setParam] = useState(readHashParam)
  useEffect(() => {
    const onChange = () => setParam(readHashParam())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return param.startsWith('prep/') ? <PrepPage app={app} day={param.slice(5)} /> : <OrderingList app={app} />
}

function OrderingList({ app }: { app: AppContext }) {
  const load = useLoad(fetchOrdering, [])
  const why = useWhy()
  const deferred = useDeferred()
  const [tab, setTab] = useState<'demand' | 'accuracy'>('demand')
  const [days, setDays] = useState(false)
  const [decided, setDecided] = useState<Record<string, 'approved' | 'rejected'>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const data = load.data

  if (load.status === 'loading') return <main className="page ml-page"><Skeleton title={TITLE} /></main>
  if (!data) return <main className="page ml-page"><LoadFailed title={TITLE} what="Ordering and prep" onRetry={() => void load.reload()} /></main>

  const updated = data.computed_at ? `${dayLabel(data.computed_at)} ${timeLabel(data.computed_at)}` : undefined
  const editor = days && <DeliveryDays app={app} data={data} onClose={() => setDays(false)} onSaved={() => void load.reload()} />
  if (data.state === 'setup') {
    const steps: SetupStep[] = data.setup.map((s) => {
      if (s.key === 'costs') return { label: 'Cost a dish', detail: 'Orders are worked out from what each dish uses.', done: s.done, action: { label: 'Open menu costs', href: '#menu-costs' } }
      if (s.key === 'schedule') return { label: 'Add delivery days', detail: 'Each supplier needs an order cutoff and a delivery day.', done: s.done, action: app.canManage ? { label: 'Add delivery days', onClick: () => setDays(true) } : undefined }
      return { label: 'Import 4 weeks of sales', detail: `${s.count} of ${s.of ?? 4} weeks are in.`, done: s.done, action: { label: 'Open connections', href: '#connect' } }
    })
    return (
      <main className="page ml-page">
        <HeaderSentence title={TITLE} sentence={orderingSentence(data)} state="setup" />
        <SetupChecklist title="Before orders can be drafted" steps={steps} />
        {editor}
      </main>
    )
  }

  const tiles = data.tiles!
  const chart = data.chart ?? { points: [], today_index: 0, coverage: null }
  const ahead = chart.points.slice(chart.today_index)
  const mean = ahead.length ? ahead.reduce((sum, p) => sum + p.expected, 0) / ahead.length : 0
  const next = tiles.next_cutoff
  const miss = tiles.average_miss
  const weekly = data.accuracy.filter((w) => w.wape != null)

  const act = async (key: string, run: () => Promise<unknown>, failed: string) => {
    setBusy(key)
    try {
      await run()
      await load.reload()
    } catch (err) {
      app.toast(failed, err instanceof Error ? err.message : 'Try again.')
    } finally {
      setBusy(null)
    }
  }
  const decide = (draft: OrderDraft, decision: 'approved' | 'rejected') => {
    setDecided((d) => ({ ...d, [draft.proposal_id]: decision }))
    const revert = () =>
      setDecided((d) => {
        const rest = { ...d }
        delete rest[draft.proposal_id]
        return rest
      })
    deferred.start(draft.proposal_id, `${draft.supplier} order ${decision === 'approved' ? 'approved' : 'dismissed'}.`, () => {
      const call = decision === 'approved' ? approveAction(draft.proposal_id) : rejectAction(draft.proposal_id)
      call
        .then(() => app.refresh())
        .catch((err: unknown) => {
          revert()
          app.toast('The decision was not saved', err instanceof Error ? err.message : 'Try again.')
        })
    }, revert)
  }
  const missWhy = (): Why => ({
    result: `${percent(miss.value ?? 0)} average miss`,
    formula: 'Average miss = sum of the gaps between items sold and items expected / items sold',
    inputs: weekly.slice(-4).map((w) => ({ label: `Week of ${dayLabel(w.week)}`, value: percent(w.wape ?? 0) })),
    dataBasis: `${data.weeks_of_data} weeks of item sales, each week forecast from what was known before it`,
    freshness: updated ? `Computed ${updated}.` : 'Not computed yet.',
    fix: { label: 'Open connections', href: '#connect' },
  })

  return (
    <main className={`page ml-page${data.stale ? ' ml-stale' : ''}`}>
      <HeaderSentence title={TITLE} sentence={orderingSentence(data)} updated={updated} state="live">
        <ConfidenceLabel weeks={data.weeks_of_data} needed={8} />
        <a className="btn btn-secondary btn-sm" href={`#ordering-prep/prep/${data.today}`}>
          Prep sheet
        </a>
        {app.canManage && (
          <button className="btn btn-secondary btn-sm" onClick={() => setDays(true)}>
            Delivery days
          </button>
        )}
      </HeaderSentence>
      {data.stale && data.last_sales_date && <StateBanner kind="stale" text={`Sales were last imported ${dayLabel(data.last_sales_date)}. Import a newer export.`} action={{ label: 'Open connections', href: '#connect' }} />}
      <TileRow>
        <StatTile value={String(tiles.waiting.value)} label="Orders waiting" compare={`${tiles.waiting.lines} lines to order`} />
        {next && <StatTile value={`${dayLabel(next.at)} ${timeLabel(next.at)}`} label="Next cutoff" compare={`${next.supplier}, delivery ${dayLabel(next.delivery_date)}`} />}
        {miss.value != null && <StatTile value={percent(miss.value)} label="Average miss" compare={`last 28 days; ${percent(miss.then ?? miss.value)} the 28 days before`} onWhy={() => why.show(missWhy())} />}
      </TileRow>

      <div className="ml-tabs" role="tablist" aria-label="Chart">
        <button role="tab" aria-selected={tab === 'demand'} className={`ml-tab${tab === 'demand' ? ' active' : ''}`} onClick={() => setTab('demand')}>
          Demand
        </button>
        <button role="tab" aria-selected={tab === 'accuracy'} className={`ml-tab${tab === 'accuracy' ? ' active' : ''}`} onClick={() => setTab('accuracy')}>
          Accuracy
        </button>
      </div>
      {tab === 'demand' ? (
        <ViewAsTable
          title={`About ${Math.round(mean).toLocaleString('en-US')} items a day are expected over the next ${ahead.length} days`}
          summary={`Items sold each day for 14 days back, and expected with a likely range for ${ahead.length} days ahead.`}
          chart={<ForecastChart points={chart.points} unit="items sold" now={chart.today_index} />}
          columns={['Day', 'Expected', 'Likely range', 'Actual']}
          rows={chart.points.map((p) => [dayLabel(p.date), Math.round(p.expected), `${Math.round(p.low)} to ${Math.round(p.high)}`, p.actual == null ? 'Not yet' : Math.round(p.actual)])}
        />
      ) : (
        <ViewAsTable
          title={`Average miss was ${percent(miss.value ?? 0)} over the last 28 days`}
          summary={`Average miss of the item forecast by week for ${weekly.length} weeks.`}
          chart={<StepLines points={weekly.map((w) => ({ date: w.week, value: (w.wape ?? 0) * 100 }))} label="Average miss" unit="% of items sold" format={(v) => `${v.toFixed(1)}%`} />}
          columns={['Week of', 'Average miss']}
          rows={weekly.map((w) => [dayLabel(w.week), percent(w.wape ?? 0)])}
        />
      )}

      {data.drafts.map((draft) => {
        const status = decided[draft.proposal_id]
        const pending = deferred.pendingId === draft.proposal_id
        return (
          <section key={draft.proposal_id} className="card" aria-label={`Order for ${draft.supplier}`} data-testid="order-draft">
            <div className="card-header">
              <div>
                <h2 className="card-title">
                  {draft.supplier}, delivery {dayLabel(draft.delivery_date)}
                </h2>
                <div className="card-subtitle">
                  Covers {dayLabel(draft.period_start)} to the next delivery. Order by {dayLabel(draft.cutoff_at)} {timeLabel(draft.cutoff_at)}.
                </div>
              </div>
              <div className="ml-header-meta">
                {draft.check_quantities && <StatusWord tone="neutral">Check quantities</StatusWord>}
                {draft.under_minimum && draft.minimum_order_amount && <StatusWord tone="bad">Under the {dollars(draft.minimum_order_amount)} minimum</StatusWord>}
              </div>
            </div>
            <div className="table-wrap">
              <table className="table ml-table">
                <thead>
                  <tr>
                    <th>Item</th>
                    <th className="num">Suggested</th>
                    <th className="ml-hide-phone">Pack</th>
                    <th className="num ml-hide-phone">Expected use</th>
                    <th className="num ml-hide-phone">Estimated on hand</th>
                    <th className="num ml-hide-phone">Line total</th>
                    <th>Corrections</th>
                  </tr>
                </thead>
                <tbody>
                  {draft.lines.map((line) => (
                    <LineRow key={line.line_ref} line={line} locked={!app.canManage || !!status} busy={busy === line.line_ref} onWhy={() => why.open('order_lines', line.line_ref)}
                      onCorrect={(kind) => void act(line.line_ref, () => correctLine(line.line_ref, kind), 'The correction was not saved')}
                      onQty={(qty) => void act(line.line_ref, () => setLineQty(line.line_ref, qty), 'The quantity was not saved')} />
                  ))}
                </tbody>
              </table>
            </div>
            <div className="card-footer ml-order-foot">
              <span className="strong">Total {draft.total ? dollars(draft.total) : 'not known'}</span>
              {pending ? (
                <button className="btn btn-secondary" onClick={deferred.undo}>
                  Undo
                </button>
              ) : status === 'approved' ? (
                <StatusWord tone="good">Approved. The order is a draft; you send it.</StatusWord>
              ) : status === 'rejected' ? (
                <StatusWord tone="neutral">Dismissed</StatusWord>
              ) : app.canManage ? (
                <span className="row">
                  <button className="btn btn-primary" onClick={() => decide(draft, 'approved')}>
                    Approve
                  </button>
                  <button className="btn btn-secondary" onClick={() => decide(draft, 'rejected')}>
                    Dismiss
                  </button>
                </span>
              ) : null}
            </div>
          </section>
        )
      })}
      {app.canManage && (data.can_draft ?? []).length > 0 && (
        <section className="card ml-pad" aria-label="Draft an order">
          {(data.can_draft ?? []).map((c) => (
            <div key={c.supplier_id} className="ml-draft-row">
              <span>
                {c.supplier}: delivery {dayLabel(c.delivery_date)}, order by {dayLabel(c.cutoff_at)} {timeLabel(c.cutoff_at)}.
              </span>
              <button className="btn btn-secondary" disabled={busy === c.supplier_id} onClick={() => void act(c.supplier_id, () => draftOrder(c.supplier_id), 'No order was drafted')}>
                Draft order
              </button>
            </div>
          ))}
        </section>
      )}
      {editor}
      {deferred.bar}
      {why.panel}
    </main>
  )
}

function LineRow({ line, locked, busy, onWhy, onCorrect, onQty }: { line: OrderLine; locked: boolean; busy: boolean; onWhy: () => void; onCorrect: (kind: 'out' | 'plenty') => void; onQty: (qty: number) => void }) {
  const [text, setText] = useState(String(line.qty))
  useEffect(() => setText(String(line.qty)), [line.qty])
  const commit = () => {
    const qty = Number(text)
    if (!Number.isInteger(qty) || qty < 0) setText(String(line.qty))
    else if (qty !== line.qty) onQty(qty)
  }
  const note = line.correction === 'out' ? 'Marked out' : line.correction === 'plenty' ? 'Marked plenty' : line.correction === 'edit' ? `Suggested ${line.suggested}` : null
  return (
    <tr>
      <td>
        <span className="strong">{line.name}</span>
        {note && <div className="muted small">{note}</div>}
      </td>
      <td className="num">
        <input className="input ml-qty" type="number" min={0} step={1} inputMode="numeric" aria-label={`Packs of ${line.name}`} value={text} disabled={locked || busy} onChange={(e) => setText(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === 'Enter' && commit()} />
      </td>
      <td className="ml-hide-phone">
        {line.pack_unit} of {amount(line.pack_base_qty, line.base_unit)}
      </td>
      <td className="num ml-hide-phone">
        <button className="ml-why-link ml-cell-link" onClick={onWhy}>
          {amount(line.expected_use, line.base_unit)}
        </button>
      </td>
      <td className="num ml-hide-phone">{amount(line.est_on_hand, line.base_unit)}</td>
      <td className="num ml-hide-phone">{line.line_total != null ? dollars(line.line_total) : <span className="muted">No price</span>}</td>
      <td>
        {!locked && (
          <span className="row">
            <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => onCorrect('out')}>
              We're out
            </button>
            <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => onCorrect('plenty')}>
              We have plenty
            </button>
          </span>
        )}
      </td>
    </tr>
  )
}

/** When each supplier delivers and when its order must be in. */
function DeliveryDays({ app, data, onClose, onSaved }: { app: AppContext; data: Ordering; onClose: () => void; onSaved: () => void }) {
  const [supplier, setSupplier] = useState(data.suppliers[0]?.id ?? '')
  const [cutoffDay, setCutoffDay] = useState(2)
  const [cutoffTime, setCutoffTime] = useState('14:00')
  const [delivery, setDelivery] = useState(3)
  const [minimum, setMinimum] = useState('')
  const [rows, setRows] = useState<DeliveryDay[]>(data.schedules)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const save = async () => {
    if (!supplier || !/^([01]\d|2[0-3]):[0-5]\d$/.test(cutoffTime) || (minimum !== '' && !(Number(minimum) >= 0))) {
      setError('Pick a supplier, and give the cutoff as a 24-hour time such as 14:00.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      const out = await saveDeliveryDay({ supplier_id: supplier, order_cutoff_weekday: cutoffDay, order_cutoff_time: cutoffTime, delivery_weekday: delivery, minimum_order_amount: minimum === '' ? null : Number(minimum).toFixed(2) })
      setRows(out.schedules)
      onSaved()
    } catch (err) {
      setError(err instanceof Error ? `The delivery day was not saved. ${err.message}` : 'The delivery day was not saved. Try again.')
    } finally {
      setSaving(false)
    }
  }
  const remove = async (id: string) => {
    try {
      setRows((await removeDeliveryDay(id)).schedules)
      onSaved()
    } catch (err) {
      app.toast('The delivery day was not removed', err instanceof Error ? err.message : 'Try again.')
    }
  }
  return (
    <SidePanel title="Delivery days" onClose={onClose}>
      <div className="ml-stack">
        {rows.length > 0 && (
          <ul className="list">
            {rows.map((r) => (
              <li key={r.id} className="list-item">
                <div className="list-item-main">
                  <div className="strong">
                    {r.supplier}: {WEEKDAYS[r.delivery_weekday]}
                  </div>
                  <div className="muted small">
                    Order by {WEEKDAYS[r.order_cutoff_weekday]} {r.order_cutoff_time}
                    {r.minimum_order_amount ? `, minimum ${dollars(r.minimum_order_amount)}` : ''}
                  </div>
                </div>
                <button className="btn btn-ghost btn-sm" onClick={() => void remove(r.id)} aria-label={`Remove ${r.supplier} ${WEEKDAYS[r.delivery_weekday]}`}>
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
        {data.suppliers.length === 0 ? (
          <p>Add a supplier first. Suppliers come from your invoices.</p>
        ) : (
          <>
            <label className="field">
              <span className="field-label">Supplier</span>
              <select className="input" value={supplier} onChange={(e) => setSupplier(e.target.value)}>
                {data.suppliers.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="field-label">Delivery day</span>
              <select className="input" value={delivery} onChange={(e) => setDelivery(Number(e.target.value))}>
                {WEEKDAYS.map((d, i) => (
                  <option key={d} value={i}>
                    {d}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="field-label">Order cutoff day</span>
              <select className="input" value={cutoffDay} onChange={(e) => setCutoffDay(Number(e.target.value))}>
                {WEEKDAYS.map((d, i) => (
                  <option key={d} value={i}>
                    {d}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="field-label">Cutoff time</span>
              <input className="input" value={cutoffTime} onChange={(e) => setCutoffTime(e.target.value)} />
            </label>
            <label className="field">
              <span className="field-label">Minimum order, $</span>
              <input className="input" inputMode="decimal" value={minimum} onChange={(e) => setMinimum(e.target.value)} />
            </label>
            {error && <StateBanner kind="error" text={error} />}
            <div className="row">
              <button className="btn btn-primary" disabled={saving} onClick={() => void save()}>
                Save
              </button>
              <button className="btn btn-secondary" onClick={onClose}>
                Close
              </button>
            </div>
          </>
        )}
      </div>
    </SidePanel>
  )
}

/** The day's prep sheet, laid out to print: large type, a page per station, and two blank columns to fill on paper. */
function PrepPage({ app, day }: { app: AppContext; day: string }) {
  const load = useLoad(() => fetchPrep(day), [day])
  const [busy, setBusy] = useState(false)
  const data = load.data
  if (load.status === 'loading') return <main className="page ml-page"><Skeleton title="Prep sheet" /></main>
  if (!data) return <main className="page ml-page"><LoadFailed title="Prep sheet" what="The prep sheet" onRetry={() => void load.reload()} /></main>
  const stations = [...new Set(data.lines.map((l) => l.station))]
  const n = data.lines.length
  const propose = async () => {
    setBusy(true)
    try {
      await proposePrep(day)
      await load.reload()
      app.refresh()
    } catch (err) {
      app.toast('The sheet was not proposed', err instanceof Error ? err.message : 'Try again.')
    } finally {
      setBusy(false)
    }
  }
  return (
    <main className="page ml-page ml-print">
      <a className="ml-back" href="#ordering-prep">
        Ordering and prep
      </a>
      <HeaderSentence title="Prep sheet" sentence={n ? `Prep for ${dayLabel(data.date)}: ${n} ${n === 1 ? 'item' : 'items'} to par.` : `Prep for ${dayLabel(data.date)}: 0 items. No dish uses a prepped item.`} state={data.state}>
        {n > 0 && (
          <button className="btn btn-secondary btn-sm" onClick={() => window.print()}>
            Print
          </button>
        )}
        {n > 0 && app.canManage && !data.proposal && (
          <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => void propose()}>
            Propose sheet
          </button>
        )}
        {data.proposal && <StatusWord tone={data.proposal.status === 'approved' ? 'good' : 'neutral'}>{data.proposal.status === 'approved' ? 'Confirmed' : data.proposal.status === 'pending' ? 'Waiting for approval' : 'Dismissed'}</StatusWord>}
      </HeaderSentence>
      {stations.map((station) => (
        <section key={station} className="card ml-prep-station" aria-label={`${station} prep`}>
          <div className="card-header">
            <h2 className="card-title">
              {station}, {dayLabel(data.date)}
            </h2>
          </div>
          <table className="table ml-prep-table">
            <thead>
              <tr>
                <th>Item</th>
                <th className="num">Par</th>
                <th className="num">Batch size</th>
                <th>Shelf life</th>
                <th>On hand</th>
                <th>Prep</th>
              </tr>
            </thead>
            <tbody>
              {data.lines.filter((l) => l.station === station).map((line) => (
                <tr key={line.item_id}>
                  <td>
                    <span className="strong">{line.item_name}</span>
                    <div className="muted small">For {line.used_by.join(', ')}</div>
                  </td>
                  <td className="num">{amount(line.par, line.unit)}</td>
                  <td className="num">{amount(line.batch_size, line.unit)}</td>
                  <td>{line.shelf_life_days != null ? `${line.shelf_life_days} days` : 'Not set'}</td>
                  <td className="ml-blank" aria-label="On hand, filled in on paper" />
                  <td className="ml-blank" aria-label="Prep, filled in on paper" />
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
      {n > 0 && <p className="muted small">Prep up to par. Par is the amount the day needs three days in four. FullHouse does not record what was on hand.</p>}
    </main>
  )
}
