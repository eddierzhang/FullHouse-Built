import { useState } from 'react'
import { fetchPrimeCost, fetchSavings, fetchSpend } from '../api/insights'
import type { AppContext } from '../app/context'
import { LineChart } from '../components/Charts'
import { CompareBars, PrimeCostChart } from '../components/InsightCharts'
import { Freshness, Loaded } from '../components/PageState'
import { money, pct, shortDate } from '../lib/format'
import { useLoad } from '../lib/useLoad'

const WINDOWS = [7, 28, 56] as const

function ViewToggle({ view, onChange, label }: { view: 'chart' | 'table'; onChange: (v: 'chart' | 'table') => void; label: string }) {
  return (
    <div className="segmented" role="group" aria-label={`${label}: chart or table`}>
      <button aria-pressed={view === 'chart'} onClick={() => onChange('chart')}>
        Chart
      </button>
      <button aria-pressed={view === 'table'} onClick={() => onChange('table')}>
        Table
      </button>
    </div>
  )
}

/**
 * Three questions an owner asks of the numbers, one card each: is this
 * paying for itself, am I inside my costs, and where did the money go.
 * Every chart has its table, and every headline figure links to the rows
 * behind it.
 */
export function InsightsPage({ app }: { app: AppContext }) {
  const savings = useLoad(fetchSavings, [])
  const prime = useLoad(fetchPrimeCost, [])
  const [days, setDays] = useState<(typeof WINDOWS)[number]>(28)
  const spend = useLoad(() => fetchSpend(days), [days])
  const [savingsView, setSavingsView] = useState<'chart' | 'table'>('chart')
  const [primeView, setPrimeView] = useState<'chart' | 'table'>('chart')
  const [split, setSplit] = useState<'supplier' | 'category'>('supplier')

  const s = savings.data
  const lastPrime = prime.data && prime.data.length > 0 ? prime.data[prime.data.length - 1] : null
  const spendChange = spend.data && spend.data.total_before > 0 ? ((spend.data.total_now - spend.data.total_before) / spend.data.total_before) * 100 : null

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Savings & costs</h1>
          <p className="page-subtitle">What FullHouse has found, what food and labor cost you, and where the money went.</p>
        </div>
        <Freshness loads={[savings, prime, spend]} />
      </div>

      <div className="stack">
        <section className="grid grid-stats" aria-label="Key figures">
          <a className="card stat stat-link" href="#digest">
            <div className="stat-label">Found so far</div>
            <div className="stat-value">{s ? money(s.found_to_date) : '—'}</div>
            <div className="stat-meta">{s ? `across ${s.weeks.length} weekly ${s.weeks.length === 1 ? 'digest' : 'digests'}` : ' '}</div>
          </a>
          <a className="card stat stat-link" href="#thread">
            <div className="stat-label">Acted on</div>
            <div className="stat-value">{s ? money(s.acted_to_date) : '—'}</div>
            <div className="stat-meta">moved, or raised with a rep</div>
          </a>
          <a className={`card stat stat-link${s && s.open_this_week > 0 ? ' attention' : ''}`} href="#digest">
            <div className="stat-label">Still open this week</div>
            <div className="stat-value">{s ? money(s.open_this_week) : '—'}</div>
            <div className="stat-meta">see this week’s digest</div>
          </a>
          <a className="card stat stat-link" href="#profit">
            <div className="stat-label">Prime cost, last week</div>
            <div className="stat-value">{lastPrime ? pct(lastPrime.prime_pct) : '—'}</div>
            <div className="stat-meta">{lastPrime ? `food ${pct(lastPrime.food_pct)}, labor ${pct(lastPrime.labor_pct)}` : 'needs sales and labor connected'}</div>
          </a>
        </section>

        <section className="card" aria-labelledby="savings-title">
          <div className="card-header">
            <div>
              <h2 className="card-title" id="savings-title">
                Is it paying for itself?
              </h2>
              <div className="card-subtitle">What each weekly digest found, added up, and how much of it you acted on.</div>
            </div>
            <div className="row wrap">
              <div className="legend" aria-hidden={savingsView === 'table'}>
                <span>
                  <span className="legend-swatch" style={{ background: 'var(--chart-1)' }} />
                  Found
                </span>
                <span>
                  <span className="legend-swatch" style={{ background: 'var(--chart-2)' }} />
                  Acted on
                </span>
              </div>
              <ViewToggle view={savingsView} onChange={setSavingsView} label="Savings" />
            </div>
          </div>
          <Loaded load={savings} what="savings" placeholderHeight={220}>
            {(data) =>
              data.weeks.length === 0 ? (
                <div className="card-body">
                  <div className="empty">
                    <div className="empty-title">No digest yet</div>
                    Add two invoices for the same item and the first digest shows what it found. <a href="#invoices">Add an invoice</a>
                  </div>
                </div>
              ) : savingsView === 'chart' ? (
                <div className="card-body">
                  <LineChart
                    dates={data.weeks.map((w) => w.week_start)}
                    series={[
                      { key: 'found', label: 'Found', color: 'var(--chart-1)', values: data.weeks.map((w) => w.found_to_date) },
                      { key: 'acted', label: 'Acted on', color: 'var(--chart-2)', values: data.weeks.map((w) => w.acted_to_date) },
                    ]}
                  />
                </div>
              ) : (
                <div className="table-wrap">
                  <table className="table table-cards">
                    <thead>
                      <tr>
                        <th>Week of</th>
                        <th className="num">Found that week</th>
                        <th className="num">Acted on</th>
                        <th className="num">Found to date</th>
                        <th className="num">Acted on to date</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...data.weeks].reverse().map((w) => (
                        <tr key={w.week_start}>
                          <td data-label="Week of">{shortDate(w.week_start)}</td>
                          <td className="num" data-label="Found that week">{money(w.found, true)}</td>
                          <td className="num" data-label="Acted on">{money(w.acted, true)}</td>
                          <td className="num strong" data-label="Found to date">{money(w.found_to_date, true)}</td>
                          <td className="num" data-label="Acted on to date">{money(w.acted_to_date, true)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )
            }
          </Loaded>
        </section>

        <section className="card" aria-labelledby="prime-title">
          <div className="card-header">
            <div>
              <h2 className="card-title" id="prime-title">
                Am I inside my costs?
              </h2>
              <div className="card-subtitle">Food and labor as a share of sales, by week. Together they are your prime cost.</div>
            </div>
            <div className="row wrap">
              <div className="legend" aria-hidden={primeView === 'table'}>
                <span>
                  <span className="legend-swatch" style={{ background: 'var(--chart-2)' }} />
                  Labor
                </span>
                <span>
                  <span className="legend-swatch" style={{ background: 'var(--chart-1)' }} />
                  Food
                </span>
              </div>
              <ViewToggle view={primeView} onChange={setPrimeView} label="Prime cost" />
            </div>
          </div>
          <Loaded load={prime} what="prime cost" placeholderHeight={220}>
            {(weeks) =>
              weeks.length === 0 ? (
                <div className="card-body">
                  <div className="empty">
                    <div className="empty-title">Needs sales and labor</div>
                    Connect your point of sale and payroll, or drop in their exports, and this fills in each Monday.{' '}
                    {app.flags.v2_connect && <a href="#connect">Open Connections</a>}
                  </div>
                </div>
              ) : primeView === 'chart' ? (
                <div className="card-body">
                  <PrimeCostChart weeks={weeks} />
                </div>
              ) : (
                <div className="table-wrap">
                  <table className="table table-cards">
                    <thead>
                      <tr>
                        <th>Week of</th>
                        <th className="num">Sales</th>
                        <th className="num">Food</th>
                        <th className="num">Labor</th>
                        <th className="num">Prime cost</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...weeks].reverse().map((w) => (
                        <tr key={w.week_of}>
                          <td data-label="Week of">{shortDate(w.week_of)}</td>
                          <td className="num" data-label="Sales">{money(w.sales)}</td>
                          <td className="num" data-label="Food">
                            {money(w.food)} · {pct(w.food_pct)}
                          </td>
                          <td className="num" data-label="Labor">
                            {money(w.labor)} · {pct(w.labor_pct)}
                          </td>
                          <td className="num strong" data-label="Prime cost">{pct(w.prime_pct)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )
            }
          </Loaded>
        </section>

        <section className="card" aria-labelledby="spend-title">
          <div className="card-header">
            <div>
              <h2 className="card-title" id="spend-title">
                Where did the money go?
              </h2>
              <div className="card-subtitle">
                {spend.data
                  ? `${money(spend.data.total_now)} bought ${shortDate(spend.data.from)} – ${shortDate(spend.data.to)}` +
                    (spendChange === null ? '' : `, ${spendChange >= 0 ? 'up' : 'down'} ${Math.abs(spendChange).toFixed(0)}% on the ${days} days before`)
                  : 'From your invoices.'}
              </div>
            </div>
            <div className="row wrap">
              <div className="segmented" role="group" aria-label="Split by">
                <button aria-pressed={split === 'supplier'} onClick={() => setSplit('supplier')}>
                  By supplier
                </button>
                <button aria-pressed={split === 'category'} onClick={() => setSplit('category')}>
                  By category
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
          <Loaded load={spend} what="spending" placeholderHeight={180}>
            {(data) => (
              <div className="card-body">
                <CompareBars rows={split === 'supplier' ? data.by_supplier : data.by_category} />
                <p className="muted small compare-key">
                  <span className="compare-key-bar" /> these {days} days <span className="compare-key-tick" /> the {days} days before
                </p>
              </div>
            )}
          </Loaded>
        </section>
      </div>
    </main>
  )
}
