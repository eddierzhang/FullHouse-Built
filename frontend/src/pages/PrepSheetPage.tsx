import { useState } from 'react'
import { getPrepSheet, listPrepSheets, markPrepSheetPrinted } from '../api/client'
import type { AppContext } from '../app/context'
import { Icon } from '../components/Icon'
import { Empty, Loaded } from '../components/PageState'
import { qty, shortDate } from '../lib/format'
import { useHashParam } from '../lib/useHashRoute'
import { useLoad } from '../lib/useLoad'

/** The confirmed prep sheets, newest first. Proposed ones wait under Recommendations. */
export function PrepSheetsPage({ app }: { app: AppContext }) {
  const load = useLoad(listPrepSheets, [])
  void app
  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Prep sheets</h1>
          <p className="page-subtitle">What the forecast and your recipes call for, per ingredient, for the next days. Proposed by the agent, confirmed by you, printed for the kitchen.</p>
        </div>
      </div>
      <Loaded load={load} what="prep sheets" placeholderHeight={120}>
        {(sheets) =>
          sheets.length === 0 ? (
            <Empty title="No prep sheets yet">They are proposed once there is a forecast and confirmed recipes. Switch on the daily prep sheet under Profile › Features › Schedules.</Empty>
          ) : (
            <section className="card">
              <ul className="list" aria-label="Prep sheets">
                {sheets.map((s) => (
                  <li key={s.id} className="list-item">
                    <div className="list-item-main">
                      <a href={`#prep/${s.id}`} className="strong">
                        {shortDate(s.date)}
                      </a>
                      <div className="muted small">
                        {s.lines.length} ingredients · {s.status}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )
        }
      </Loaded>
    </main>
  )
}

/** One prep sheet, printable. */
export function PrepSheetPage({ app }: { app: AppContext }) {
  const id = useHashParam()
  const load = useLoad(() => getPrepSheet(id), [id])
  const [busy, setBusy] = useState(false)
  const print = async () => {
    setBusy(true)
    try {
      window.print()
      await markPrepSheetPrinted(id)
      await load.reload()
    } catch (err) {
      app.toast('Couldn’t mark it printed', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <main className="pi">
      <div className="pi-page">
        <a className="pi-back" href="#prep">
          <span style={{ display: 'inline-flex', transform: 'rotate(180deg)' }}>
            <Icon name="arrowRight" size={16} />
          </span>
          Prep sheets
        </a>
        <Loaded load={load} what="the prep sheet" placeholderHeight={200}>
          {(s) => (
            <>
              <header className="pi-stack-sm">
                <h1 className="pi-h1">Prep for {shortDate(s.date)}</h1>
                <p className="pi-secondary">{s.lines.length} ingredients · {s.status}</p>
              </header>
              <section className="pi-stack-sm">
                <div className="pi-table-wrap">
                  <table className="pi-table">
                    <thead>
                      <tr>
                        <th>Ingredient</th>
                        <th className="num">Amount</th>
                        <th>For</th>
                      </tr>
                    </thead>
                    <tbody>
                      {s.lines.map((l) => (
                        <tr key={l.item_id}>
                          <td>{l.item_name}</td>
                          <td className="num pi-money">
                            {qty(l.qty)} {l.unit}
                          </td>
                          <td className="pi-secondary">{l.from.map((f) => `${f.recipe} × ${qty(f.plates)}`).join(', ')}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <button type="button" className="pi-btn-o" disabled={busy} onClick={() => void print()}>
                  Print
                </button>
              </section>
            </>
          )}
        </Loaded>
      </div>
    </main>
  )
}
