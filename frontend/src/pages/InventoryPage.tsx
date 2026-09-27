import { useCallback, useEffect, useMemo, useState } from 'react'
import { type SupplyChain, type Supplier, fetchSupplyChain, listSuppliers, updateInventoryItem } from '../api/client'
import type { AppContext } from '../app/context'
import { money, qty } from '../lib/format'

/** Fill scaled so the reorder point sits at the midpoint of the bar. */
const fill = (onHand: number, threshold: number) =>
  threshold <= 0 ? (onHand > 0 ? 100 : 0) : Math.min(100, (onHand / (threshold * 2)) * 100)

export function InventoryPage({ app }: { app: AppContext }) {
  const [filter, setFilter] = useState<'all' | 'low'>('all')
  const [suppliers, setSuppliers] = useState<Supplier[]>([])
  const [chain, setChain] = useState<SupplyChain | null>(null)
  const [adjusting, setAdjusting] = useState<string | null>(null)
  const [draft, setDraft] = useState('')

  const load = useCallback(() => {
    listSuppliers().then(setSuppliers).catch(() => {})
    fetchSupplyChain().then(setChain).catch(() => {})
  }, [])
  useEffect(load, [load])

  const supplierName = (id: string | null) => suppliers.find((s) => s.id === id)?.name ?? 'No supplier'
  const cover = useMemo(() => new Map((chain?.cover ?? []).map((c) => [c.id, c])), [chain])

  // Most urgent first: how far above its reorder point each item sits, relative
  // to that point. Ties break alphabetically.
  const rows = useMemo(() => {
    const urgency = (i: (typeof app.inventory)[number]) =>
      i.reorder_threshold > 0 ? i.quantity_on_hand / i.reorder_threshold : Number.POSITIVE_INFINITY
    return [...app.inventory]
      .filter((i) => filter === 'all' || i.quantity_on_hand <= i.reorder_threshold)
      .sort((a, b) => urgency(a) - urgency(b) || a.name.localeCompare(b.name))
  }, [app.inventory, filter])

  const lowCount = app.inventory.filter((i) => i.quantity_on_hand <= i.reorder_threshold).length
  const value = app.inventory.reduce((sum, i) => sum + i.quantity_on_hand * i.unit_cost, 0)

  const saveAdjustment = async (id: string) => {
    const next = Number(draft)
    if (!Number.isFinite(next) || next < 0) {
      app.toast('Enter a stock level of zero or more')
      return
    }
    try {
      await updateInventoryItem(id, { quantity_on_hand: next })
      app.toast('Stock updated')
      setAdjusting(null)
      app.refresh()
      load()
    } catch (err) {
      app.toast('Couldn’t update stock', err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Inventory</h1>
          <p className="page-subtitle">Stock on hand, what’s running low, and how long it will last.</p>
        </div>
      </div>

      <div className="stack">
        <section className="grid grid-stats">
          <div className="card stat">
            <div className="stat-label">Items tracked</div>
            <div className="stat-value">{app.inventory.length}</div>
          </div>
          <div className="card stat">
            <div className="stat-label">Stock value</div>
            <div className="stat-value">{money(value)}</div>
          </div>
          <div className={`card stat${lowCount ? ' attention' : ''}`}>
            <div className="stat-label">Below reorder point</div>
            <div className="stat-value">{lowCount}</div>
          </div>
          <div className={`card stat${chain?.at_risk_count ? ' attention' : ''}`}>
            <div className="stat-label">Runs out before resupply</div>
            <div className="stat-value">{chain ? chain.at_risk_count : '—'}</div>
            <div className="stat-meta">cover shorter than lead time</div>
          </div>
        </section>

        <section className="card">
          <div className="card-header">
            <h2 className="card-title">Stock levels</h2>
            <div className="segmented" role="group" aria-label="Filter stock">
              <button aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>
                All · {app.inventory.length}
              </button>
              <button aria-pressed={filter === 'low'} onClick={() => setFilter('low')}>
                Low · {lowCount}
              </button>
            </div>
          </div>

          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Item</th>
                  <th style={{ width: '22%' }}>Level</th>
                  <th className="num">On hand</th>
                  <th className="num">Reorder at</th>
                  <th className="num">Days of cover</th>
                  <th className="num">Value</th>
                  <th className="actions" aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={7} className="empty">
                      {filter === 'low' ? 'Nothing is below its reorder point.' : 'No inventory yet.'}
                    </td>
                  </tr>
                )}
                {rows.map((item) => {
                  const low = item.quantity_on_hand <= item.reorder_threshold
                  const c = cover.get(item.id)
                  return (
                    <tr key={item.id} className={low ? 'highlight' : undefined}>
                      <td>
                        <div className="cell-title">{item.name}</div>
                        <div className="cell-sub">{supplierName(item.supplier_id)}</div>
                      </td>
                      <td>
                        <div className="row">
                          <div className="meter" style={{ flex: 1 }} title={`Reorder point at ${qty(item.reorder_threshold)} ${item.unit}`}>
                            <div className={`meter-fill${low ? ' danger' : ''}`} style={{ width: `${fill(item.quantity_on_hand, item.reorder_threshold)}%` }} />
                            <span className="meter-mark" style={{ left: '50%' }} />
                          </div>
                          {low && <span className="badge badge-danger">Low</span>}
                        </div>
                      </td>
                      <td className="num">
                        {adjusting === item.id ? (
                          <input
                            className="input input-sm"
                            style={{ width: 90, textAlign: 'right' }}
                            aria-label={`New stock level for ${item.name}`}
                            autoFocus
                            inputMode="decimal"
                            value={draft}
                            onChange={(e) => setDraft(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') void saveAdjustment(item.id)
                              if (e.key === 'Escape') setAdjusting(null)
                            }}
                          />
                        ) : (
                          <>
                            <span className="strong">{qty(item.quantity_on_hand)}</span> <span className="muted">{item.unit}</span>
                          </>
                        )}
                      </td>
                      <td className="num muted">{qty(item.reorder_threshold)}</td>
                      <td className="num">
                        {c?.days_of_cover == null ? (
                          <span className="faint" title="No recipe links this item to sales yet">—</span>
                        ) : (
                          <span className={c.at_risk ? 'negative strong' : undefined}>
                            {qty(c.days_of_cover)}d{c.lead_time_days != null && <span className="muted"> / {c.lead_time_days}d lead</span>}
                          </span>
                        )}
                      </td>
                      <td className="num">{money(item.quantity_on_hand * item.unit_cost)}</td>
                      <td className="actions">
                        {adjusting === item.id ? (
                          <div className="row" style={{ justifyContent: 'flex-end' }}>
                            <button className="btn btn-ghost btn-sm" onClick={() => setAdjusting(null)}>
                              Cancel
                            </button>
                            <button className="btn btn-primary btn-sm" onClick={() => saveAdjustment(item.id)}>
                              Save
                            </button>
                          </div>
                        ) : (
                          <button
                            className="btn btn-ghost btn-sm"
                            onClick={() => {
                              setAdjusting(item.id)
                              setDraft(String(item.quantity_on_hand))
                            }}
                          >
                            Adjust
                          </button>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <div className="card-footer">
            The tick on each bar marks the reorder point. Days of cover comes from real sales and recipes, so it’s blank
            for items no recipe uses.
          </div>
        </section>
      </div>
    </main>
  )
}
