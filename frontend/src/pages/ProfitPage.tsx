import { useEffect, useState } from 'react'
import { type ProfitSummary, fetchProfitSummary } from '../api/client'
import type { AppContext } from '../app/context'
import { LineChart } from '../components/Charts'
import { ForecastCard } from '../components/ForecastCard'
import { money, pct, shortDate, titleCase } from '../lib/format'

const WINDOWS = [7, 30, 90] as const

export function ProfitPage({ app }: { app: AppContext }) {
  const [days, setDays] = useState<(typeof WINDOWS)[number]>(30)
  const [data, setData] = useState<ProfitSummary | null>(null)
  const [view, setView] = useState<'chart' | 'table'>('chart')

  useEffect(() => {
    fetchProfitSummary(days)
      .then(setData)
      .catch((err) => app.toast('Couldn’t load profit data', String(err)))
  }, [days, app.toast])

  const maxCategory = Math.max(1, ...(data?.by_category ?? []).map((c) => c.revenue))
  const maxChannel = Math.max(1, ...(data?.by_channel ?? []).map((c) => c.revenue))

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Profit & forecast</h1>
          <p className="page-subtitle">{data ? `${shortDate(data.start)} – ${shortDate(data.end)} · ${data.orders} orders` : 'Loading…'}</p>
        </div>
        <div className="segmented" role="group" aria-label="Time window">
          {WINDOWS.map((w) => (
            <button key={w} aria-pressed={days === w} onClick={() => setDays(w)}>
              {w} days
            </button>
          ))}
        </div>
      </div>

      <div className="stack">
        <section className="grid grid-stats">
          <div className="card stat">
            <div className="stat-label">Revenue</div>
            <div className="stat-value">{data ? money(data.revenue) : '—'}</div>
          </div>
          <div className="card stat">
            <div className="stat-label">Gross profit</div>
            <div className="stat-value">{data ? money(data.gross_profit) : '—'}</div>
            <div className="stat-meta">{data ? `${money(data.cogs)} cost of goods` : ' '}</div>
          </div>
          <div className="card stat">
            <div className="stat-label">Gross margin</div>
            <div className="stat-value">{data ? pct(data.margin_pct) : '—'}</div>
          </div>
          <div className="card stat">
            <div className="stat-label">Average order</div>
            <div className="stat-value">{data ? money(data.average_order_value, true) : '—'}</div>
          </div>
          <div className="card stat">
            <div className="stat-label">Stock on hand</div>
            <div className="stat-value">{data ? money(data.inventory_value) : '—'}</div>
            <div className="stat-meta">capital tied up</div>
          </div>
        </section>

        <div className="grid grid-main" style={{ alignItems: 'start' }}>
          <section className="card">
            <div className="card-header">
              <div>
                <h2 className="card-title">Revenue and gross profit</h2>
                <div className="card-subtitle">Daily</div>
              </div>
              <div className="row wrap">
                <div className="legend" aria-hidden={view === 'table'}>
                  <span>
                    <span className="legend-swatch" style={{ background: 'var(--chart-1)' }} />
                    Revenue
                  </span>
                  <span>
                    <span className="legend-swatch" style={{ background: 'var(--chart-2)' }} />
                    Gross profit
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
              </div>
            </div>

            {!data && (
              <div className="card-body">
                <div className="skeleton" style={{ height: 220 }} />
              </div>
            )}

            {data && view === 'chart' && (
              <div className="card-body">
                <LineChart
                  dates={data.daily.map((d) => d.date)}
                  series={[
                    { key: 'revenue', label: 'Revenue', color: 'var(--chart-1)', values: data.daily.map((d) => d.revenue) },
                    { key: 'profit', label: 'Gross profit', color: 'var(--chart-2)', values: data.daily.map((d) => d.profit) },
                  ]}
                />
              </div>
            )}

            {data && view === 'table' && (
              <div className="table-wrap" style={{ maxHeight: 320, overflowY: 'auto' }}>
                <table className="table">
                  <thead>
                    <tr>
                      <th>Date</th>
                      <th className="num">Orders</th>
                      <th className="num">Revenue</th>
                      <th className="num">Cost</th>
                      <th className="num">Gross profit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...data.daily]
                      .filter((d) => d.orders > 0)
                      .reverse()
                      .map((d) => (
                        <tr key={d.date}>
                          <td>{shortDate(d.date)}</td>
                          <td className="num">{d.orders}</td>
                          <td className="num">{money(d.revenue, true)}</td>
                          <td className="num">{money(d.cogs, true)}</td>
                          <td className="num strong">{money(d.profit, true)}</td>
                        </tr>
                      ))}
                    {data.orders === 0 && (
                      <tr>
                        <td colSpan={5} className="empty">
                          No trade in this window.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <ForecastCard />
        </div>

        <div className="grid grid-2" style={{ alignItems: 'start' }}>
          <section className="card">
            <div className="card-header">
              <div>
                <h2 className="card-title">By category</h2>
                <div className="card-subtitle">Revenue, highest first</div>
              </div>
            </div>
            <div className="card-body stack" style={{ gap: 14 }}>
              {data?.by_category.length === 0 && <div className="muted">Nothing sold in this window.</div>}
              {data?.by_category.map((c) => (
                <div key={c.category}>
                  <div className="row" style={{ justifyContent: 'space-between', marginBottom: 6 }}>
                    <span className="strong">{c.category}</span>
                    <span className="tabular small">
                      {money(c.revenue)} <span className="muted">· {money(c.profit)} profit · {c.units} sold</span>
                    </span>
                  </div>
                  <div className="meter">
                    <div className="meter-fill chart" style={{ width: `${(c.revenue / maxCategory) * 100}%` }} />
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="card">
            <div className="card-header">
              <div>
                <h2 className="card-title">By channel</h2>
                <div className="card-subtitle">Where orders come from</div>
              </div>
            </div>
            <div className="card-body stack" style={{ gap: 14 }}>
              {data?.by_channel.length === 0 && <div className="muted">No orders in this window.</div>}
              {/* With a single channel a bar says nothing a sentence doesn't. */}
              {data?.by_channel.length === 1 && (
                <div className="muted">
                  All {data.by_channel[0].orders} orders were <span className="strong">{titleCase(data.by_channel[0].channel)}</span>, worth{' '}
                  {money(data.by_channel[0].revenue)}.
                </div>
              )}
              {data && data.by_channel.length > 1 &&
                data.by_channel.map((c) => (
                  <div key={c.channel}>
                    <div className="row" style={{ justifyContent: 'space-between', marginBottom: 6 }}>
                      <span className="strong">{titleCase(c.channel)}</span>
                      <span className="tabular small">
                        {money(c.revenue)} <span className="muted">· {c.orders} orders</span>
                      </span>
                    </div>
                    <div className="meter">
                      <div className="meter-fill chart" style={{ width: `${(c.revenue / maxChannel) * 100}%` }} />
                    </div>
                  </div>
                ))}
            </div>
          </section>
        </div>

        <p className="muted small" style={{ margin: 0 }}>
          Gross profit uses recipe costs for dishes that have a recipe and the estimated cost for the rest. There are no
          labour or overhead costs here, so this is gross, not net.
        </p>
      </div>
    </main>
  )
}
