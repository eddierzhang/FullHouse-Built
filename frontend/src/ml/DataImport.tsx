import { useState } from 'react'
import type { AppContext } from '../app/context'
import { useLoad } from '../lib/useLoad'
import { fetchDataStatus, type ImportKind, importData, type ImportResult } from './api'
import { dayLabel, useRestaurantClock } from './format'
import { StateBanner } from './ui'

const FILES: { kind: ImportKind; label: string; needs: string }[] = [
  { kind: 'orders', label: 'Orders', needs: 'One row per item sold: order id, time opened, item, quantity, net.' },
  { kind: 'punches', label: 'Time punches', needs: 'One row per shift worked: id, employee, role, clock in, clock out.' },
  { kind: 'payouts', label: 'Delivery payouts', needs: 'One row per order: platform, order id, commission, fees, payout.' },
]

/**
 * Order-level data for the margin and labor modules, by CSV export until a point of sale is connected for it.
 * A file can be sent again: every row is matched on its own id, so nothing doubles. Shown on Connections when
 * any of the modules is on.
 */
export function DataImport({ app }: { app: AppContext }) {
  useRestaurantClock(app.restaurant.timezone)
  const status = useLoad(fetchDataStatus, [])
  const [busy, setBusy] = useState<ImportKind | null>(null)
  const [result, setResult] = useState<ImportResult | null>(null)

  const send = async (kind: ImportKind, file: File | undefined) => {
    if (!file) return
    setBusy(kind)
    setResult(null)
    try {
      const out = await importData(kind, await file.text())
      setResult(out)
      status.reload()
    } catch (err) {
      app.toast('The file was not imported', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }
  if (status.status === 'error' || !status.data) return null
  const data = status.data
  const facts: Record<ImportKind, string> = {
    orders: data.orders.rows ? `${data.orders.rows.toLocaleString('en-US')} orders over ${data.orders.business_dates} days, to ${dayLabel(data.orders.last ?? '')}` : 'None yet',
    punches: data.punches.rows ? `${data.punches.rows.toLocaleString('en-US')} punches, to ${dayLabel(data.punches.last ?? '')}` : 'None yet',
    payouts: data.payouts.rows ? `${data.payouts.rows.toLocaleString('en-US')} payout rows` : 'None yet',
  }
  return (
    <section className="card" aria-labelledby="ml-data-title">
      <div className="card-header">
        <div>
          <h2 className="card-title" id="ml-data-title">
            Order-level data
          </h2>
          <div className="card-subtitle">Exports from your point of sale, time clock and delivery platforms. Sending a file twice changes nothing.</div>
        </div>
      </div>
      {(data.orders.stale || data.punches.stale) && (
        <div className="card-body">
          <StateBanner kind="stale" text={`${data.orders.stale ? 'Orders' : 'Time punches'} were last imported ${dayLabel((data.orders.stale ? data.orders : data.punches).last_import_at ?? '')}. Import a newer export.`} />
        </div>
      )}
      <ul className="list">
        {FILES.map((file) => (
          <li key={file.kind} className="list-item">
            <div className="list-item-main">
              <div className="strong">{file.label}</div>
              <div className="muted small">{file.needs}</div>
              <div className="small">{facts[file.kind]}</div>
            </div>
            {app.canManage && (
              <label className={`btn btn-secondary btn-sm${busy ? ' disabled' : ''}`}>
                <input type="file" accept=".csv,text/csv" hidden disabled={busy !== null} onChange={(e) => void send(file.kind, e.target.files?.[0])} />
                {busy === file.kind ? 'Importing' : 'Import CSV'}
              </label>
            )}
          </li>
        ))}
      </ul>
      {result && (
        <div className="card-body" role="status">
          <p className="strong" style={{ margin: 0 }}>
            {result.rows.toLocaleString('en-US')} rows read: {result.created} new, {result.updated} changed, {result.unchanged} the same as before.
          </p>
          {result.problem_count > 0 && (
            <>
              <p className="muted small">
                {result.problem_count} {result.problem_count === 1 ? 'row was' : 'rows were'} left out. Fix them in the file and import it again.
              </p>
              <ul className="ml-problems">
                {result.problems.map((p) => (
                  <li key={`${p.line}-${p.problem}`}>
                    Line {p.line}: {p.problem}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </section>
  )
}
