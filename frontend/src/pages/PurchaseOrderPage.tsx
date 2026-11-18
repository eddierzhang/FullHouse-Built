import { useState } from 'react'
import { getPurchaseOrder, markOrderSent, sendOrder } from '../api/client'
import type { AppContext } from '../app/context'
import { Icon } from '../components/Icon'
import { Loaded } from '../components/PageState'
import { dateTime, money, qty, shortDate } from '../lib/format'
import { useHashParam } from '../lib/useHashRoute'
import { useLoad } from '../lib/useLoad'

/** One purchase order, printable, with Send for a draft. Sending needs an owner or admin; never the agent. */
export function PurchaseOrderPage({ app }: { app: AppContext }) {
  const id = useHashParam()
  const load = useLoad(() => getPurchaseOrder(id), [id])
  const [to, setTo] = useState('')
  const [busy, setBusy] = useState(false)

  const act = async (work: () => Promise<unknown>, done: string) => {
    setBusy(true)
    try {
      await work()
      app.toast(done)
      await load.reload()
      app.refresh()
    } catch (err) {
      app.toast('Couldn’t do that', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="pi">
      <div className="pi-page">
        <a className="pi-back" href="#inventory">
          <span style={{ display: 'inline-flex', transform: 'rotate(180deg)' }}>
            <Icon name="arrowRight" size={16} />
          </span>
          Inventory
        </a>
        <Loaded load={load} what="the order" placeholderHeight={200}>
          {(o) => (
            <>
              <header className="pi-stack-sm">
                <h1 className="pi-h1">Purchase order</h1>
                <p className="pi-secondary">
                  {o.supplier_name ?? 'No supplier named'} · {o.status}
                  {o.expected_on && ` · expected ${shortDate(o.expected_on)}`}
                  {o.sent_at && ` · sent ${dateTime(o.sent_at)} by ${o.sent_by ?? ''}`}
                </p>
              </header>
              <section className="pi-stack-sm">
                <div className="pi-table-wrap">
                  <table className="pi-table">
                    <thead>
                      <tr>
                        <th>Item</th>
                        <th className="num">Qty</th>
                        <th>Pack</th>
                        <th className="num">Expected price</th>
                        <th className="num">Received</th>
                      </tr>
                    </thead>
                    <tbody>
                      {o.lines.length > 0 ? (
                        o.lines.map((l) => (
                          <tr key={l.id}>
                            <td>{l.description}</td>
                            <td className="num pi-money">{qty(l.qty)}</td>
                            <td>{l.unit}</td>
                            <td className="num pi-money">{l.unit_price_expected != null ? money(l.unit_price_expected, true) : ''}</td>
                            <td className="num pi-money">{l.received_qty != null ? qty(l.received_qty) : ''}</td>
                          </tr>
                        ))
                      ) : (
                        <tr>
                          <td>{o.item_name}</td>
                          <td className="num pi-money">{o.packs ? qty(o.packs) : qty(o.quantity)}</td>
                          <td>{o.packs ? o.pack_unit : o.unit}</td>
                          <td className="num pi-money">{o.estimated_cost != null && o.packs ? money(o.estimated_cost / o.packs, true) : ''}</td>
                          <td className="num pi-money">{o.received_qty != null ? qty(o.received_qty) : ''}</td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
                {o.estimated_cost != null && <p className="pi-secondary">Expected total about {money(o.estimated_cost, true)} at the prices on your last invoices.</p>}
                {o.note && <p className="pi-secondary">{o.note}</p>}
              </section>
              {o.status === 'draft' && app.canManage && (
                <section className="pi-stack-sm">
                  <p className="pi-label-strong">Send</p>
                  <form
                    className="pi-answers"
                    style={{ alignItems: 'center' }}
                    onSubmit={(e) => {
                      e.preventDefault()
                      void act(() => sendOrder(o.id, to || o.rep_email), `Sent to ${to || o.rep_email}`)
                    }}
                    aria-label="Send the order"
                  >
                    <input className="pi-input pi-input-sm" type="email" placeholder={o.rep_email ?? 'rep@supplier.com'} value={to} onChange={(e) => setTo(e.target.value)} aria-label="Rep’s email" style={{ width: 240 }} />
                    <button type="submit" className="pi-btn" disabled={busy || (!to && !o.rep_email)}>
                      Send to rep
                    </button>
                    <button type="button" className="pi-link" disabled={busy} onClick={() => void act(() => markOrderSent(o.id), 'Marked as placed')}>
                      Placed by phone
                    </button>
                  </form>
                  <p className="pi-secondary">The rep gets the lines above and the same as a PDF. Nothing is sent until you press it.</p>
                </section>
              )}
              <div className="pi-stack-sm pi-desktop-only">
                <button type="button" className="pi-btn-o" onClick={() => window.print()}>
                  Print
                </button>
              </div>
            </>
          )}
        </Loaded>
      </div>
    </main>
  )
}
