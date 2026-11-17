import { useState } from 'react'
import { fetchStockStatus, fetchVariance, fetchWaste, runStockNow } from '../api/stock'
import type { AppContext } from '../app/context'
import { Icon } from '../components/Icon'
import { ShrinkChart } from '../components/InsightCharts'
import { Empty, Freshness, Loaded } from '../components/PageState'
import { WasteDialog } from '../components/WasteDialog'
import { money, pct, qty, relativeTime, shortDate } from '../lib/format'
import { useLoad } from '../lib/useLoad'

const WINDOWS = [7, 28, 56] as const

/**
 * Where the stock went. Sales took some off through the recipes, some was
 * thrown away and written down, and a count shows the rest: what nobody can
 * account for. An item that hasn't been counted has no such figure, and the
 * page says so instead of showing a zero.
 */
export function VariancePage({ app }: { app: AppContext }) {
  const [days, setDays] = useState<(typeof WINDOWS)[number]>(28)
  const [view, setView] = useState<'chart' | 'table'>('chart')
  const [wasteOpen, setWasteOpen] = useState(false)
  const [running, setRunning] = useState(false)
  const variance = useLoad(() => (app.canManage ? fetchVariance(days) : Promise.resolve(null)), [days, app.canManage])
  const status = useLoad(fetchStockStatus, [])
  const waste = useLoad(fetchWaste, [])
  const v = variance.data

  const run = async () => {
    setRunning(true)
    try {
      const result = await runStockNow()
      app.toast(result.moved ? 'Sales taken off the shelf' : 'Already up to date', result.moved ? `${result.moved} stock movements from ${result.sales} sale lines.` : undefined)
      app.refresh()
      status.reload()
      variance.reload()
    } catch (err) {
      app.toast('Could not run it', err instanceof Error ? err.message : String(err))
    } finally {
      setRunning(false)
    }
  }

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Variance & waste</h1>
          <p className="page-subtitle">Where the stock went: what sales used, what was thrown away, and what counts found missing.</p>
        </div>
        <div className="row wrap">
          <Freshness loads={[status, waste]} />
          <button className="btn btn-secondary" onClick={() => setWasteOpen(true)}>
            Log waste
          </button>
          <a className="btn btn-primary" href="#count">
            Count stock
          </a>
        </div>
      </div>

      <div className="stack">
        {status.data && status.data.alerts.length > 0 && (
          <section className="card" aria-label="Needs a decision today">
            <ul className="list">
              {status.data.alerts.slice(0, 6).map((a) => (
                <li key={a.title} className="list-item">
                  <Icon name="alert" className={a.kind === 'eighty_six' ? 'alert-icon danger' : 'alert-icon'} />
                  <div className="list-item-main">
                    <div className="strong">{a.title}</div>
                    <div className="muted small">{a.detail}</div>
                  </div>
                  <a className="btn btn-ghost btn-sm" href={a.kind === 'eighty_six' ? '#menu' : app.flags.v3_nav ? '#tools' : '#inventory'}>
                    {a.kind === 'eighty_six' ? 'Open the menu' : 'See when'}
                  </a>
                </li>
              ))}
            </ul>
          </section>
        )}

        {app.canManage && (
          <>
            <section className="grid grid-stats" aria-label="Key figures">
              <div className={`card stat${v && v.missing_dollars > 0 ? ' attention' : ''}`}>
                <div className="stat-label">Missing</div>
                <div className="stat-value">{v ? money(v.missing_dollars) : '—'}</div>
                <div className="stat-meta">{v ? (v.counted ? 'found by counts, beyond sales and waste' : 'count to find out') : ' '}</div>
              </div>
              <div className="card stat">
                <div className="stat-label">Wasted</div>
                <div className="stat-value">{v ? money(v.wasted_dollars) : '—'}</div>
                <div className="stat-meta">{v && v.used_dollars > 0 ? `${pct((v.wasted_dollars / v.used_dollars) * 100)} of what sales used` : 'written down by the team'}</div>
              </div>
              <div className="card stat">
                <div className="stat-label">Used by sales</div>
                <div className="stat-value">{v ? money(v.used_dollars) : '—'}</div>
                <div className="stat-meta">through your recipes</div>
              </div>
              <a className="card stat stat-link" href="#count">
                <div className="stat-label">Counted</div>
                <div className="stat-value">{v ? `${v.counted} of ${v.counted + v.not_counted}` : '—'}</div>
                <div className="stat-meta">{v && v.not_counted ? `${v.not_counted} not counted in ${days} days` : 'items'}</div>
              </a>
            </section>

            <section className="card" aria-labelledby="shrink-title">
              <div className="card-header">
                <div>
                  <h2 className="card-title" id="shrink-title">
                    What counts found
                  </h2>
                  <div className="card-subtitle">{v ? `${shortDate(v.from)} – ${shortDate(v.to)}. The difference between what the ledger expected and what was on the shelf.` : 'Loading…'}</div>
                </div>
                <div className="row wrap">
                  <div className="legend" aria-hidden={view === 'table'}>
                    <span>
                      <span className="legend-swatch" style={{ background: 'var(--chart-2)' }} />
                      Missing
                    </span>
                    <span>
                      <span className="legend-swatch" style={{ background: 'var(--chart-1)' }} />
                      Extra
                    </span>
                  </div>
                  <div className="segmented" role="group" aria-label="View">
                    <button aria-pressed={view === 'chart'} onClick={() => setView('chart')}>
                      Chart
                    </button>
                    <button aria-pressed={view === 'table'} onClick={() => setView('table')}>
                      Table
                    </button>
                  </div>
                  <div className="segmented" role="group" aria-label="Time window">
                    {WINDOWS.map((w) => (
                      <button key={w} aria-pressed={days === w} onClick={() => setDays(w)}>
                        {w} days
                      </button>
                    ))}
                  </div>
                </div>
              </div>
              <Loaded load={variance} what="the variance report" placeholderHeight={220}>
                {(data) => {
                  if (!data) return null
                  const counted = data.items.filter((r) => r.unexplained !== null)
                  if (view === 'chart') {
                    return counted.length === 0 ? (
                      <div className="card-body">
                        <Empty title="Nothing counted yet" action={<a className="btn btn-primary" href="#count">Count an area</a>}>
                          Stock is running on sales and deliveries alone. One count of the walk-in shows what went missing.
                        </Empty>
                      </div>
                    ) : (
                      <div className="card-body">
                        <ShrinkChart
                          rows={counted.map((r) => ({
                            key: r.inventory_item_id,
                            label: r.name,
                            dollars: r.unexplained_dollars ?? 0,
                            detail: r.unexplained === 0 ? `as expected` : `${qty(Math.abs(r.unexplained ?? 0))} ${r.unit}${r.pct_of_use ? `, ${pct(Math.abs(r.pct_of_use))} of use` : ''}`,
                          }))}
                        />
                        {data.not_counted > 0 && (
                          <p className="muted small" style={{ marginTop: 12 }}>
                            {data.not_counted} {data.not_counted === 1 ? 'item was' : 'items were'} not counted in this window, so nothing is known about {data.not_counted === 1 ? 'it' : 'them'} beyond sales and
                            deliveries. They are in the table.
                          </p>
                        )}
                      </div>
                    )
                  }
                  return (
                    <div className="table-wrap">
                      <table className="table table-cards">
                        <thead>
                          <tr>
                            <th>Item</th>
                            <th className="num">Received</th>
                            <th className="num">Used by sales</th>
                            <th className="num">Wasted</th>
                            <th className="num">Counts found</th>
                            <th className="num">In dollars</th>
                            <th>Last counted</th>
                          </tr>
                        </thead>
                        <tbody>
                          {data.items.map((r) => (
                            <tr key={r.inventory_item_id}>
                              <td data-label="Item">{r.name}</td>
                              <td className="num" data-label="Received">
                                {qty(r.received)} {r.unit}
                              </td>
                              <td className="num" data-label="Used by sales">
                                {qty(r.used)} {r.unit}
                              </td>
                              <td className="num" data-label="Wasted">
                                {qty(r.wasted)} {r.unit}
                              </td>
                              <td className="num" data-label="Counts found">
                                {r.unexplained === null ? '—' : r.unexplained === 0 ? 'as expected' : `${r.unexplained < 0 ? '−' : '+'}${qty(Math.abs(r.unexplained))} ${r.unit}`}
                              </td>
                              <td className="num strong" data-label="In dollars">
                                {r.unexplained_dollars === null ? '—' : `${r.unexplained_dollars < 0 ? '−' : ''}${money(Math.abs(r.unexplained_dollars), true)}`}
                              </td>
                              <td data-label="Last counted">{r.last_counted_at ? relativeTime(r.last_counted_at) : 'not in this window'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )
                }}
              </Loaded>
            </section>
          </>
        )}

        <section className="card" aria-labelledby="waste-log-title">
          <div className="card-header">
            <div>
              <h2 className="card-title" id="waste-log-title">
                Waste log
              </h2>
              <div className="card-subtitle">What the team wrote down in the last 28 days.</div>
            </div>
            <button className="btn btn-secondary btn-sm" onClick={() => setWasteOpen(true)}>
              Log waste
            </button>
          </div>
          <Loaded load={waste} what="the waste log" placeholderHeight={120}>
            {(log) =>
              log.entries.length === 0 ? (
                <div className="card-body">
                  <Empty title="Nothing logged">When something is thrown away, log it here in three taps. It keeps the stock figures honest.</Empty>
                </div>
              ) : (
                <ul className="list">
                  {log.entries.map((e) => (
                    <li key={e.id} className="list-item">
                      <div className="list-item-main">
                        <div className="row wrap">
                          <span className="strong">
                            {qty(e.quantity)} {e.unit} of {e.item_name}
                          </span>
                          <span className="badge">{e.reason_label}</span>
                        </div>
                        <div className="muted small">
                          {e.note ? `${e.note} · ` : ''}
                          {relativeTime(e.at)}
                          {e.by && e.by !== 'system' ? ` · ${e.by}` : ''}
                        </div>
                      </div>
                      <div className="strong">{money(e.dollars, true)}</div>
                    </li>
                  ))}
                </ul>
              )
            }
          </Loaded>
        </section>

        <section className="card" aria-labelledby="tracking-title">
          <div className="card-header">
            <div>
              <h2 className="card-title" id="tracking-title">
                How stock is tracked
              </h2>
              <div className="card-subtitle">
                {status.data?.tracking_since
                  ? `Sales have come off the shelf through your recipes since ${shortDate(status.data.tracking_since)}.` +
                    (status.data.last_depleted_at ? ` Last taken off ${relativeTime(status.data.last_depleted_at)}.` : status.data.last_sales_day ? ` Sales are in through ${shortDate(status.data.last_sales_day)}.` : '')
                  : 'Sales come off the shelf through your recipes, starting with the next sales sync.'}
              </div>
            </div>
            {app.canManage && (
              <button className="btn btn-secondary btn-sm" disabled={running} onClick={() => void run()}>
                {running ? 'Running…' : 'Take sales off now'}
              </button>
            )}
          </div>
          {status.data && status.data.unmapped.length > 0 && (
            <div className="card-body">
              <p className="muted small" style={{ marginTop: 0 }}>
                These sold but took nothing off the shelf. Give each dish a recipe and its ingredients are tracked from then on. <a href="#menu">Open the menu</a>
              </p>
              <ul className="list">
                {status.data.unmapped.map((u) => (
                  <li key={u.pos_item_name} className="list-item">
                    <div className="list-item-main">
                      <div className="strong">{u.pos_item_name}</div>
                      <div className="muted small">
                        {qty(u.qty)} sold, {money(u.net)} · {u.why}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      </div>

      <WasteDialog
        open={wasteOpen}
        items={app.inventory}
        onClose={() => setWasteOpen(false)}
        onDone={(r) => {
          app.toast('Waste logged', `${qty(r.taken)} ${r.unit} of ${r.name}, ${money(r.dollars, true)}.`)
          app.refresh()
          waste.reload()
          variance.reload()
        }}
      />
    </main>
  )
}
