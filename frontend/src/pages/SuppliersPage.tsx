import { useCallback, useEffect, useState } from 'react'
import {
  type SupplyChain,
  createSupplier,
  deleteSupplier,
  fetchSupplyChain,
  updateInventoryItem,
  updateSupplier,
} from '../api/client'
import type { AppContext } from '../app/context'
import { Icon } from '../components/Icon'
import { money, qty } from '../lib/format'

/** Above this share of stock value, one supplier is a single point of failure. */
const CONCENTRATION_RISK = 60
const BLANK = { name: '', contact_info: '', lead_time_days: '2' }

export function SuppliersPage({ app }: { app: AppContext }) {
  const [data, setData] = useState<SupplyChain | null>(null)
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState(BLANK)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editDraft, setEditDraft] = useState(BLANK)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    fetchSupplyChain().then(setData).catch(() => {})
  }, [])
  useEffect(load, [load])

  const run = async (work: () => Promise<unknown>, message: string) => {
    setBusy(true)
    try {
      await work()
      app.toast(message)
      load()
      app.refresh()
      return true
    } catch (err) {
      app.toast('That didn’t work', err instanceof Error ? err.message : String(err))
      return false
    } finally {
      setBusy(false)
    }
  }

  const leadDays = (value: string) => {
    const n = Number(value)
    return Number.isInteger(n) && n >= 0 ? n : null
  }

  if (!data) {
    return (
      <main className="page">
        <div className="skeleton" style={{ height: 320 }} />
      </main>
    )
  }

  const concentrated = data.suppliers.find((s) => s.share_pct >= CONCENTRATION_RISK)
  const atRisk = data.cover.filter((c) => c.at_risk)
  const pendingOrders = app.pendingActions.filter((a) => a.action_type === 'purchase_order').length

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Suppliers</h1>
          <p className="page-subtitle">Who supplies what, how exposed you are, and what to reorder.</p>
        </div>
        <button className="btn btn-primary" onClick={() => setAdding((v) => !v)}>
          <Icon name={adding ? 'close' : 'plus'} size={16} /> {adding ? 'Cancel' : 'Add supplier'}
        </button>
      </div>

      <div className="stack">
        {(concentrated || atRisk.length > 0 || data.unassigned_item_count > 0 || pendingOrders > 0) && (
          <div className="stack" style={{ gap: 10 }}>
            {atRisk.length > 0 && (
              <div className="callout callout-danger">
                <Icon name="alert" />
                <div>
                  <div className="callout-title">
                    {atRisk.length} {atRisk.length === 1 ? 'item runs' : 'items run'} out before a delivery could arrive
                  </div>
                  <div className="callout-body">
                    {atRisk.map((c) => `${c.name} (${qty(c.days_of_cover ?? 0)}d left, ${c.lead_time_days}d lead)`).join(' · ')}
                  </div>
                </div>
              </div>
            )}
            {concentrated && (
              <div className="callout callout-warning">
                <Icon name="alert" />
                <div>
                  <div className="callout-title">
                    {concentrated.share_pct}% of stock value comes from {concentrated.name}
                  </div>
                  <div className="callout-body">A disruption there would stop most of the kitchen. Consider a second source.</div>
                </div>
              </div>
            )}
            {data.unassigned_item_count > 0 && (
              <div className="callout callout-warning">
                <Icon name="alert" />
                <div>
                  <div className="callout-title">
                    {data.unassigned_item_count} {data.unassigned_item_count === 1 ? 'item has' : 'items have'} no supplier
                  </div>
                  <div className="callout-body">Assign one below so it can be reordered.</div>
                </div>
              </div>
            )}
            {pendingOrders > 0 && (
              <div className="callout callout-info">
                <Icon name="info" />
                <div className="callout-body">
                  {pendingOrders} purchase {pendingOrders === 1 ? 'order is' : 'orders are'} waiting for approval.{' '}
                  <a href="#approvals">Review</a>
                </div>
              </div>
            )}
          </div>
        )}

        <section className="grid grid-stats">
          <div className="card stat">
            <div className="stat-label">Stock value</div>
            <div className="stat-value">{money(data.total_stock_value)}</div>
            <div className="stat-meta">across {data.supplier_count} suppliers</div>
          </div>
          <div className={`card stat${data.low_stock_count ? ' attention' : ''}`}>
            <div className="stat-label">Need reordering</div>
            <div className="stat-value">{data.low_stock_count}</div>
            <div className="stat-meta">of {data.item_count} items</div>
          </div>
          <div className="card stat">
            <div className="stat-label">Cost to restock</div>
            <div className="stat-value">{money(data.restock_cost)}</div>
            <div className="stat-meta">to clear every shortfall</div>
          </div>
          <div className="card stat">
            <div className="stat-label">Longest lead time</div>
            <div className="stat-value">{data.longest_lead_days}d</div>
            <div className="stat-meta">on what is short</div>
          </div>
        </section>

        <section className="card">
          <div className="card-header">
            <div>
              <h2 className="card-title">Suppliers</h2>
              <div className="card-subtitle">Ranked by share of stock value</div>
            </div>
          </div>

          {adding && (
            <div className="inline-form">
              <div className="form-row">
                <label className="field">
                  <span className="field-label">Name</span>
                  <input className="input" autoFocus value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
                </label>
                <label className="field">
                  <span className="field-label">Contact</span>
                  <input className="input" placeholder="Email or phone" value={draft.contact_info} onChange={(e) => setDraft({ ...draft, contact_info: e.target.value })} />
                </label>
                <label className="field">
                  <span className="field-label">Lead time (days)</span>
                  <input className="input" inputMode="numeric" value={draft.lead_time_days} onChange={(e) => setDraft({ ...draft, lead_time_days: e.target.value })} />
                </label>
                <button
                  className="btn btn-primary"
                  disabled={busy || !draft.name.trim() || leadDays(draft.lead_time_days) === null}
                  onClick={async () => {
                    const ok = await run(
                      () => createSupplier({ name: draft.name.trim(), contact_info: draft.contact_info.trim() || null, lead_time_days: leadDays(draft.lead_time_days)! }),
                      'Supplier added',
                    )
                    if (ok) {
                      setDraft(BLANK)
                      setAdding(false)
                    }
                  }}
                >
                  Save supplier
                </button>
              </div>
            </div>
          )}

          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Supplier</th>
                  <th style={{ width: '28%' }}>Share of stock value</th>
                  <th className="num">Items</th>
                  <th className="num">Lead time</th>
                  <th className="actions" aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {data.suppliers.length === 0 && (
                  <tr>
                    <td colSpan={5} className="empty">No suppliers yet.</td>
                  </tr>
                )}
                {data.suppliers.map((s) =>
                  editingId === s.id ? (
                    <tr key={s.id}>
                      <td>
                        <input className="input input-sm" aria-label="Supplier name" value={editDraft.name} onChange={(e) => setEditDraft({ ...editDraft, name: e.target.value })} />
                      </td>
                      <td>
                        <input className="input input-sm" aria-label="Contact" value={editDraft.contact_info} onChange={(e) => setEditDraft({ ...editDraft, contact_info: e.target.value })} />
                      </td>
                      <td className="num">{s.item_count}</td>
                      <td className="num">
                        <input className="input input-sm" aria-label="Lead time in days" style={{ width: 70, textAlign: 'right' }} value={editDraft.lead_time_days} onChange={(e) => setEditDraft({ ...editDraft, lead_time_days: e.target.value })} />
                      </td>
                      <td className="actions">
                        <button className="btn btn-ghost btn-sm" onClick={() => setEditingId(null)}>
                          Cancel
                        </button>
                        <button
                          className="btn btn-primary btn-sm"
                          disabled={busy || leadDays(editDraft.lead_time_days) === null}
                          onClick={async () => {
                            const ok = await run(
                              () => updateSupplier(s.id, { name: editDraft.name.trim(), contact_info: editDraft.contact_info.trim() || null, lead_time_days: leadDays(editDraft.lead_time_days)! }),
                              'Supplier updated',
                            )
                            if (ok) setEditingId(null)
                          }}
                        >
                          Save
                        </button>
                      </td>
                    </tr>
                  ) : (
                    <tr key={s.id}>
                      <td>
                        <div className="cell-title">{s.name}</div>
                        <div className="cell-sub">{s.contact_info ?? 'No contact on file'}</div>
                      </td>
                      <td>
                        <div className="row">
                          <div className="meter" style={{ flex: 1 }}>
                            <div className="meter-fill chart" style={{ width: `${s.share_pct}%` }} />
                          </div>
                          <span className="tabular small" style={{ width: 96, textAlign: 'right' }}>
                            {s.share_pct}% · {money(s.stock_value)}
                          </span>
                        </div>
                      </td>
                      <td className="num">
                        {s.item_count}
                        {s.low_stock_count > 0 && <div className="negative small">{s.low_stock_count} low</div>}
                      </td>
                      <td className="num">{s.lead_time_days}d</td>
                      <td className="actions">
                        <button
                          className="btn btn-ghost btn-sm"
                          onClick={() => {
                            setEditingId(s.id)
                            setEditDraft({ name: s.name, contact_info: s.contact_info ?? '', lead_time_days: String(s.lead_time_days) })
                          }}
                        >
                          Edit
                        </button>
                        {s.item_count === 0 && (
                          <button
                            className="btn btn-ghost btn-sm negative"
                            disabled={busy}
                            onClick={() => window.confirm(`Remove ${s.name}?`) && run(() => deleteSupplier(s.id), `${s.name} removed`)}
                          >
                            Remove
                          </button>
                        )}
                      </td>
                    </tr>
                  ),
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="card">
          <div className="card-header">
            <div>
              <h2 className="card-title">Needs reordering</h2>
              <div className="card-subtitle">Below the reorder point, longest lead time first</div>
            </div>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Item</th>
                  <th>Source</th>
                  <th className="num">On hand</th>
                  <th className="num">Order</th>
                  <th className="num">Cost</th>
                </tr>
              </thead>
              <tbody>
                {data.low_stock.length === 0 && (
                  <tr>
                    <td colSpan={5} className="empty">Everything is above its reorder point.</td>
                  </tr>
                )}
                {data.low_stock.map((item) => (
                  <tr key={item.id} className="highlight">
                    <td className="cell-title">{item.name}</td>
                    <td>
                      {item.supplier_name ? (
                        <>
                          <div>{item.supplier_name}</div>
                          <div className="cell-sub">{item.lead_time_days}d lead time</div>
                        </>
                      ) : (
                        <select
                          className="select input-sm"
                          aria-label={`Supplier for ${item.name}`}
                          defaultValue=""
                          disabled={busy}
                          onChange={(e) => e.target.value && run(() => updateInventoryItem(item.id, { supplier_id: e.target.value }), `${item.name} sourced`)}
                        >
                          <option value="">Assign a supplier…</option>
                          {data.suppliers.map((s) => (
                            <option key={s.id} value={s.id}>
                              {s.name} ({s.lead_time_days}d)
                            </option>
                          ))}
                        </select>
                      )}
                    </td>
                    <td className="num">
                      {qty(item.quantity_on_hand)} <span className="muted">/ {qty(item.reorder_threshold)} {item.unit}</span>
                    </td>
                    <td className="num">
                      {qty(item.reorder_qty)} {item.unit}
                    </td>
                    <td className="num strong">{money(item.restock_cost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="card-footer">
            Approving a reorder books the goods as received straight away — there’s no in-transit tracking yet, so lead
            times are for planning.
          </div>
        </section>
      </div>
    </main>
  )
}
