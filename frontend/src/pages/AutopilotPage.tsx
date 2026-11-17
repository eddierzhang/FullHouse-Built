import { useEffect, useState } from 'react'
import {
  type AutopilotLimits,
  cancelSend,
  type Decision,
  fetchOrdering,
  fetchPars,
  type OrderingSupplier,
  pauseAutopilot,
  type Preview,
  previewPolicy,
  runAutopilot,
  saveCalendar,
  savePolicy,
  setPar,
  type SupplierCalendar,
} from '../api/ordering'
import type { AppContext } from '../app/context'
import { Icon } from '../components/Icon'
import { Empty, Freshness, Loaded } from '../components/PageState'
import { money, qty, relativeTime, shortDate } from '../lib/format'
import { useLoad } from '../lib/useLoad'

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const OUTCOME: Record<Decision['outcome'], { label: string; badge: string }> = {
  passed: { label: 'Waiting to send', badge: 'badge-warning' },
  sent: { label: 'Sent', badge: 'badge-success' },
  held: { label: 'Held for you', badge: 'badge-info' },
  cancelled: { label: 'Cancelled', badge: '' },
  failed: { label: 'Could not send', badge: 'badge-danger' },
}
const dayList = (days: number[]) => (days.length ? days.map((d) => DAYS[d]).join(', ') : 'no days set')

function DayPicker({ label, value, onChange }: { label: string; value: number[]; onChange: (days: number[]) => void }) {
  return (
    <fieldset className="field">
      <legend className="field-label">{label}</legend>
      <div className="chip-row">
        {DAYS.map((name, index) => (
          <button key={name} type="button" className="chip-button" aria-pressed={value.includes(index)} onClick={() => onChange(value.includes(index) ? value.filter((d) => d !== index) : [...value, index].sort())}>
            {name}
          </button>
        ))}
      </div>
    </fieldset>
  )
}

function NumberField({ label, value, onChange, prefix, suffix, step = 1, disabled }: { label: string; value: number; onChange: (v: number) => void; prefix?: string; suffix?: string; step?: number; disabled?: boolean }) {
  const id = `f-${label.replace(/\W+/g, '-').toLowerCase()}-${Math.random().toString(36).slice(2, 6)}`
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <div className="affix">
        {prefix && <span>{prefix}</span>}
        <input id={id} className="input" type="number" inputMode="decimal" min="0" step={step} value={Number.isFinite(value) ? value : ''} disabled={disabled} onChange={(e) => onChange(Number(e.target.value))} />
        {suffix && <span>{suffix}</span>}
      </div>
    </div>
  )
}

function Rules({ decision }: { decision: Decision }) {
  return (
    <ul className="rule-list">
      {decision.rules.map((r) => (
        <li key={r.rule} className={r.ok ? 'ok' : 'no'}>
          <Icon name={r.ok ? 'check' : 'close'} size={14} />
          <span>{r.detail}</span>
        </li>
      ))}
    </ul>
  )
}

/** One supplier: when they take orders, and the limits autopilot works inside. Folded to a summary until opened. */
function SupplierCard({ app, supplier, isOwner, onSaved }: { app: AppContext; supplier: OrderingSupplier; isOwner: boolean; onSaved: () => void }) {
  const [open, setOpen] = useState(false)
  const [calendar, setCalendar] = useState<SupplierCalendar>(supplier.calendar)
  const { consecutive_failures: _f, paused_reason: _r, ...initial } = supplier.policy
  const [limits, setLimits] = useState<AutopilotLimits>(initial)
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<Preview | null>(null)
  useEffect(() => {
    setCalendar(supplier.calendar)
    const { consecutive_failures: _a, paused_reason: _b, ...rest } = supplier.policy
    setLimits(rest)
  }, [supplier])

  const act = async (work: () => Promise<unknown>, done: string) => {
    setBusy(true)
    try {
      await work()
      app.toast(done)
      onSaved()
    } catch (err) {
      app.toast('Could not save that', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }
  const on = supplier.policy.enabled
  const byEmail = supplier.calendar.channel === 'email' && !!supplier.order_email

  return (
    <li className="autopilot-supplier">
      <div className="autopilot-supplier-head">
        <div className="list-item-main">
          <div className="row wrap">
            <span className="strong">{supplier.name}</span>
            <span className={`badge ${on ? 'badge-success' : ''}`}>{on ? 'Autopilot on' : 'You send'}</span>
          </div>
          <div className="muted small">
            Orders {dayList(supplier.calendar.order_days)} by {supplier.calendar.cutoff_time} · delivers {dayList(supplier.calendar.delivery_days)}
            {on ? ` · up to ${money(supplier.policy.per_order_cap)} an order, ${money(supplier.policy.weekly_cap)} a week · ${money(supplier.sent_this_week)} sent this week` : ''}
          </div>
          {supplier.policy.paused_reason && <div className="small count-short">{supplier.policy.paused_reason}</div>}
        </div>
        <button className="btn btn-secondary btn-sm" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? 'Close' : 'Edit'}
        </button>
      </div>
      {open && (
        <div className="autopilot-edit">
          <div className="autopilot-pane">
            <h3 className="pane-title">When they take orders</h3>
            <DayPicker label="Order days" value={calendar.order_days} onChange={(order_days) => setCalendar({ ...calendar, order_days })} />
            <DayPicker label="Delivery days" value={calendar.delivery_days} onChange={(delivery_days) => setCalendar({ ...calendar, delivery_days })} />
            <div className="form-row">
              <div className="field">
                <label className="field-label" htmlFor={`cutoff-${supplier.id}`}>
                  Order by
                </label>
                <input id={`cutoff-${supplier.id}`} className="input" type="time" value={calendar.cutoff_time} onChange={(e) => setCalendar({ ...calendar, cutoff_time: e.target.value })} />
              </div>
              <NumberField label="Smallest order" prefix="$" value={calendar.minimum_dollars ?? 0} onChange={(v) => setCalendar({ ...calendar, minimum_dollars: v || null })} />
            </div>
            <div className="form-row">
              <div className="field">
                <label className="field-label" htmlFor={`channel-${supplier.id}`}>
                  They take orders by
                </label>
                <select id={`channel-${supplier.id}`} className="select" value={calendar.channel} onChange={(e) => setCalendar({ ...calendar, channel: e.target.value as SupplierCalendar['channel'] })}>
                  <option value="email">Email</option>
                  <option value="portal">Their website</option>
                  <option value="phone">Phone</option>
                </select>
              </div>
              <div className="field">
                <label className="field-label" htmlFor={`email-${supplier.id}`}>
                  Order address
                </label>
                <input id={`email-${supplier.id}`} className="input" type="email" placeholder="orders@supplier.com" value={calendar.order_email ?? ''} onChange={(e) => setCalendar({ ...calendar, order_email: e.target.value })} />
              </div>
            </div>
            <button className="btn btn-secondary" disabled={busy} onClick={() => void act(() => saveCalendar(supplier.id, calendar), 'Order days saved')}>
              Save order days
            </button>
          </div>

          <div className="autopilot-pane">
            <h3 className="pane-title">Limits for autopilot</h3>
            {!isOwner && <p className="muted small">Only an owner can change these or switch autopilot on.</p>}
            {!byEmail && <p className="muted small">Autopilot only sends by email. Set an order address first; until then every order waits for you.</p>}
            <label className="checkbox-row">
              <input type="checkbox" checked={limits.enabled} disabled={!isOwner} onChange={(e) => setLimits({ ...limits, enabled: e.target.checked })} />
              <span>
                <span className="strong">Send {supplier.name} orders without me</span>
                <span className="muted small"> when every limit below is met. Anything else waits in Recommendations.</span>
              </span>
            </label>
            <div className="form-row">
              <NumberField label="Most for one order" prefix="$" value={limits.per_order_cap} disabled={!isOwner} onChange={(v) => setLimits({ ...limits, per_order_cap: v })} />
              <NumberField label="Most in a week" prefix="$" value={limits.weekly_cap} disabled={!isOwner} onChange={(v) => setLimits({ ...limits, weekly_cap: v })} />
            </div>
            <div className="form-row">
              <NumberField label="Hold if a price rose more than" suffix="%" value={limits.max_price_rise_pct} disabled={!isOwner} onChange={(v) => setLimits({ ...limits, max_price_rise_pct: v })} />
              <NumberField label="Hold if a line is over" suffix="× usual" step={0.1} value={limits.max_qty_multiple} disabled={!isOwner} onChange={(v) => setLimits({ ...limits, max_qty_multiple: v })} />
            </div>
            <div className="form-row">
              <NumberField label="Only items bought at least" suffix="times in 8 weeks" value={limits.min_history} disabled={!isOwner} onChange={(v) => setLimits({ ...limits, min_history: v })} />
              <NumberField label="Wait before sending" suffix="minutes" value={limits.cancel_window_minutes} disabled={!isOwner} onChange={(v) => setLimits({ ...limits, cancel_window_minutes: v })} />
            </div>
            <div className="row wrap">
              {isOwner && (
                <button className="btn btn-primary" disabled={busy} onClick={() => void act(() => savePolicy(supplier.id, limits), limits.enabled ? `Autopilot is on for ${supplier.name}` : 'Limits saved')}>
                  Save limits
                </button>
              )}
              <button
                className="btn btn-secondary"
                disabled={busy}
                onClick={() => {
                  setBusy(true)
                  previewPolicy(supplier.id, limits)
                    .then(setPreview)
                    .catch((err: unknown) => app.toast('Could not run the preview', err instanceof Error ? err.message : String(err)))
                    .finally(() => setBusy(false))
                }}
              >
                Try these on the last 8 weeks
              </button>
            </div>
            {preview && (
              <div className="autopilot-preview" role="status">
                <p className="strong" style={{ margin: 0 }}>
                  {preview.would_send} of {preview.orders.length} orders would have gone by themselves ({money(preview.sent_total)}); {preview.would_hold} would have waited for you.
                </p>
                <ul className="list">
                  {[...preview.orders].reverse().map((o) => (
                    <li key={`${o.date}-${o.reference}`} className="list-item">
                      <Icon name={o.would_send ? 'check' : 'alert'} className={o.would_send ? 'rule-ok' : 'alert-icon'} />
                      <div className="list-item-main">
                        <div>
                          {shortDate(o.date)} · {money(o.total, true)} · <span className="strong">{o.would_send ? 'sent' : 'held'}</span>
                        </div>
                        {o.held_for.length > 0 && <div className="muted small">{o.held_for.join(' ')}</div>}
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      )}
    </li>
  )
}

/**
 * Orders that go out by themselves, and only inside limits an owner set for
 * that supplier. Everything else still waits in Recommendations. The page
 * leads with what is about to go, because that is what can still be stopped.
 */
export function AutopilotPage({ app }: { app: AppContext }) {
  const load = useLoad(fetchOrdering, [])
  const pars = useLoad(fetchPars, [])
  const [busy, setBusy] = useState<string | null>(null)
  const [, tick] = useState(0)
  // The countdowns move, and a send that has gone shows as sent, without a reload.
  useEffect(() => {
    const timer = window.setInterval(() => {
      tick((n) => n + 1)
      if (load.data?.waiting.some((w) => w.send_after && new Date(w.send_after).getTime() < Date.now())) load.reload()
    }, 15000)
    return () => window.clearInterval(timer)
  }, [load])

  const act = async (key: string, work: () => Promise<unknown>, done: string, detail?: string) => {
    setBusy(key)
    try {
      await work()
      app.toast(done, detail)
      app.refresh()
      load.reload()
      pars.reload()
    } catch (err) {
      app.toast('That didn’t work', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }
  const until = (iso: string | null) => {
    if (!iso) return 'soon'
    const minutes = Math.round((new Date(iso).getTime() - Date.now()) / 60000)
    return minutes <= 0 ? 'any moment now' : minutes === 1 ? 'in 1 minute' : `in ${minutes} minutes`
  }

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Ordering autopilot</h1>
          <p className="page-subtitle">Routine orders go out by themselves, inside limits you set for each supplier. Everything else waits for you.</p>
        </div>
        <div className="row wrap">
          <Freshness loads={[load]} />
          {load.data && (
            <>
              <button className="btn btn-secondary" disabled={busy !== null} onClick={() => void act('run', runAutopilot, 'Checked', 'Pars, drafts due and anything ready to send.')}>
                Check now
              </button>
              <button className={`btn ${load.data.paused ? 'btn-primary' : 'btn-secondary'}`} disabled={busy !== null} onClick={() => void act('pause', () => pauseAutopilot(!load.data!.paused), load.data!.paused ? 'Autopilot resumed' : 'Autopilot paused', load.data!.paused ? undefined : 'Nothing goes out until you resume it.')}>
                {load.data.paused ? 'Resume autopilot' : 'Pause all'}
              </button>
            </>
          )}
        </div>
      </div>

      <Loaded load={load} what="autopilot" placeholderHeight={300}>
        {(data) => (
          <div className="stack">
            {data.paused && (
              <div className="callout callout-warning" role="status">
                <Icon name="alert" />
                <div className="callout-grow">Autopilot is paused for the whole restaurant. Orders are still drafted; none go out until you resume it.</div>
              </div>
            )}
            {!data.can_send && (
              <div className="callout callout-warning" role="status">
                <Icon name="alert" />
                <div className="callout-grow">This site has no mailbox to send from, so every order is held for a person. Whoever runs FullHouse for you can set one up.</div>
              </div>
            )}

            <section className="card" aria-labelledby="waiting-title">
              <div className="card-header">
                <div>
                  <h2 className="card-title" id="waiting-title">
                    About to go
                  </h2>
                  <div className="card-subtitle">Inside your limits and waiting out the cancel window. Stop one here if it looks wrong.</div>
                </div>
              </div>
              {data.waiting.length === 0 ? (
                <div className="card-body">
                  <Empty title="Nothing waiting to send">An order appears here on a supplier’s order day, a few hours before their cutoff.</Empty>
                </div>
              ) : (
                <ul className="list">
                  {data.waiting.map((w) => (
                    <li key={w.id} className="list-item autopilot-waiting">
                      <div className="list-item-main">
                        <div className="row wrap">
                          <span className="strong">
                            {w.supplier_name} · {money(w.total, true)}
                          </span>
                          <span className="badge badge-warning">Goes out {until(w.send_after)}</span>
                        </div>
                        <div className="muted small">
                          {w.lines ?? 0} lines{w.expected_on ? `, for delivery ${shortDate(w.expected_on)}` : ''}. Every limit met.
                        </div>
                      </div>
                      <div className="row">
                        {w.order_id && (
                          <a className="btn btn-ghost btn-sm" href={`#po/${w.order_id}`}>
                            See the order
                          </a>
                        )}
                        <button className="btn btn-secondary btn-sm" disabled={busy !== null} onClick={() => void act(w.id, () => cancelSend(w.id), 'Stopped', 'The order is a draft under On order. Nothing was sent.')}>
                          Don’t send
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="card" aria-labelledby="suppliers-title">
              <div className="card-header">
                <div>
                  <h2 className="card-title" id="suppliers-title">
                    Suppliers
                  </h2>
                  <div className="card-subtitle">Autopilot is off for a supplier until an owner switches it on.</div>
                </div>
              </div>
              {data.suppliers.length === 0 ? (
                <div className="card-body">
                  <Empty title="No suppliers yet" action={<a className="btn btn-primary" href="#suppliers">Add a supplier</a>} />
                </div>
              ) : (
                <ul className="autopilot-suppliers">
                  {data.suppliers.map((s) => (
                    <SupplierCard key={s.id} app={app} supplier={s} isOwner={data.is_owner} onSaved={load.reload} />
                  ))}
                </ul>
              )}
            </section>

            <section className="card" aria-labelledby="recent-title">
              <div className="card-header">
                <div>
                  <h2 className="card-title" id="recent-title">
                    What autopilot did
                  </h2>
                  <div className="card-subtitle">Every order it looked at, and each limit it checked.</div>
                </div>
              </div>
              {data.recent.length === 0 ? (
                <div className="card-body">
                  <Empty title="Nothing yet">Each order autopilot sends or holds is listed here with the rules behind the decision.</Empty>
                </div>
              ) : (
                <ul className="list">
                  {data.recent.map((d) => (
                    <li key={d.id} className="list-item">
                      <details className="autopilot-decision">
                        <summary>
                          <span className="strong">
                            {d.supplier_name} · {money(d.total, true)}
                          </span>
                          <span className={`badge ${OUTCOME[d.outcome].badge}`}>{OUTCOME[d.outcome].label}</span>
                          <span className="muted small">
                            {relativeTime(d.sent_at ?? d.decided_at)}
                            {d.cancelled_by ? ` · stopped by ${d.cancelled_by}` : ''}
                            {d.outcome === 'held' ? ` · ${d.rules.filter((r) => !r.ok).map((r) => r.detail).join(' ')}` : ''}
                          </span>
                        </summary>
                        <Rules decision={d} />
                        {d.outcome === 'held' && (
                          <a className="btn btn-secondary btn-sm" href="#approvals">
                            Open it in Recommendations
                          </a>
                        )}
                      </details>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="card" aria-labelledby="pars-title">
              <div className="card-header">
                <div>
                  <h2 className="card-title" id="pars-title">
                    Pars
                  </h2>
                  <div className="card-subtitle">The level each item is ordered up to: a day’s use × (lead time + days between orders + safety days). Type a figure to pin your own.</div>
                </div>
              </div>
              <Loaded load={pars} what="pars" placeholderHeight={160}>
                {(rows) => (
                  <div className="table-wrap">
                    <table className="table table-cards">
                      <thead>
                        <tr>
                          <th>Item</th>
                          <th className="num">On hand</th>
                          <th className="num">Used a day</th>
                          <th className="num">Worked out</th>
                          <th className="num">Par</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((r) => (
                          <tr key={r.inventory_item_id}>
                            <td data-label="Item">
                              {r.name}
                              <div className="muted small">{r.supplier_name ?? 'No supplier'}</div>
                            </td>
                            <td className="num" data-label="On hand">
                              {qty(r.on_hand)} {r.unit}
                            </td>
                            <td className="num" data-label="Used a day">{r.daily_use > 0 ? `${qty(r.daily_use)} ${r.unit}` : '—'}</td>
                            <td className="num" data-label="Worked out">{r.suggested === null ? 'no recipe uses it' : `${qty(r.suggested)} ${r.unit}`}</td>
                            <td className="num" data-label="Par">
                              <input
                                className="input input-sm par-input"
                                type="number"
                                min="0"
                                step="any"
                                aria-label={`Par for ${r.name}, in ${r.unit}`}
                                defaultValue={r.par === null ? '' : Math.round(r.par * 10) / 10}
                                key={`${r.inventory_item_id}-${r.par}-${r.pinned}`}
                                onBlur={(e) => {
                                  const value = e.target.value === '' ? null : Number(e.target.value)
                                  if (value !== null && r.par !== null && Math.abs(value - Math.round(r.par * 10) / 10) < 1e-9) return
                                  if (value !== null) void act(r.inventory_item_id, () => setPar(r.inventory_item_id, value), `Par pinned for ${r.name}`)
                                }}
                              />
                            </td>
                            <td data-label="">
                              {r.pinned ? (
                                <button className="btn btn-ghost btn-sm" onClick={() => void act(r.inventory_item_id, () => setPar(r.inventory_item_id, null), `${r.name} is back to its worked-out par`)}>
                                  Pinned · unpin
                                </button>
                              ) : (
                                <span className="muted small">from sales</span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Loaded>
            </section>
          </div>
        )}
      </Loaded>
    </main>
  )
}
