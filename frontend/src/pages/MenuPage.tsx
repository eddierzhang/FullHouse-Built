import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { type MenuPerformance, createMenuItem, deleteMenuItem, listMenuPerformance, updateMenuItem } from '../api/client'
import type { AppContext } from '../app/context'
import { Icon } from '../components/Icon'
import { RecipeEditor } from '../components/RecipeEditor'
import { dateTime, money, pct } from '../lib/format'

type SortKey = 'units_sold' | 'profit' | 'margin_pct' | 'name'
const SORTS: { key: SortKey; label: string }[] = [
  { key: 'units_sold', label: 'Best selling' },
  { key: 'profit', label: 'Most profit' },
  { key: 'margin_pct', label: 'Margin' },
  { key: 'name', label: 'Name' },
]

/** Selling at or below this share of the best seller counts as slow. */
const SLOW_FRACTION = 0.25
const BLANK = { name: '', category: '', price: '', cost: '' }

export function MenuPage({ app }: { app: AppContext }) {
  const [rows, setRows] = useState<MenuPerformance[]>([])
  const [sort, setSort] = useState<SortKey>('units_sold')
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState(BLANK)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editDraft, setEditDraft] = useState({ price: '', description: '' })
  const [recipeFor, setRecipeFor] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    listMenuPerformance().then(setRows).catch(() => {})
  }, [])
  useEffect(load, [load])

  const run = async (work: () => Promise<unknown>, message: string) => {
    setBusy(true)
    try {
      await work()
      app.toast(message)
      load()
      return true
    } catch (err) {
      app.toast('That didn’t work', err instanceof Error ? err.message : String(err))
      return false
    } finally {
      setBusy(false)
    }
  }

  const sorted = useMemo(
    () =>
      [...rows].sort((a, b) =>
        sort === 'name' ? a.name.localeCompare(b.name) : b[sort] - a[sort] || a.name.localeCompare(b.name),
      ),
    [rows, sort],
  )

  const best = Math.max(0, ...rows.map((r) => r.units_sold))
  const slow = rows.filter((r) => r.is_available && r.units_sold <= best * SLOW_FRACTION)
  const revenue = rows.reduce((s, r) => s + r.revenue, 0)
  const profit = rows.reduce((s, r) => s + r.profit, 0)
  const costed = rows.filter((r) => r.cost_source === 'recipe').length
  const proposals = app.pendingActions.filter((a) => a.action_type === 'menu_change').length

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Menu</h1>
          <p className="page-subtitle">What each dish sells and earns, and what it costs to make.</p>
        </div>
        <button className="btn btn-primary" onClick={() => setAdding((v) => !v)}>
          <Icon name={adding ? 'close' : 'plus'} size={16} /> {adding ? 'Cancel' : 'Add dish'}
        </button>
      </div>

      <div className="stack">
        {(slow.length > 0 || proposals > 0) && (
          <div className="stack" style={{ gap: 10 }}>
            {slow.length > 0 && (
              <div className="callout callout-warning">
                <Icon name="trend" />
                <div>
                  <div className="callout-title">
                    {slow.length} {slow.length === 1 ? 'dish is' : 'dishes are'} barely selling
                  </div>
                  <div className="callout-body">{slow.map((r) => r.name).join(' · ')} — candidates for a promotion.</div>
                </div>
              </div>
            )}
            {proposals > 0 && (
              <div className="callout callout-info">
                <Icon name="info" />
                <div className="callout-body">
                  {proposals} menu {proposals === 1 ? 'change is' : 'changes are'} waiting for approval. <a href="#approvals">Review</a>
                </div>
              </div>
            )}
          </div>
        )}

        <section className="grid grid-stats">
          <div className="card stat">
            <div className="stat-label">Dishes</div>
            <div className="stat-value">{rows.length}</div>
            <div className="stat-meta">{rows.filter((r) => !r.is_available).length} off the menu</div>
          </div>
          <div className="card stat">
            <div className="stat-label">Revenue</div>
            <div className="stat-value">{money(revenue)}</div>
          </div>
          <div className="card stat">
            <div className="stat-label">Gross profit</div>
            <div className="stat-value">{money(profit)}</div>
          </div>
          <div className="card stat">
            <div className="stat-label">Costed from recipes</div>
            <div className="stat-value">
              {costed}/{rows.length}
            </div>
            <div className="stat-meta">the rest use estimated costs</div>
          </div>
        </section>

        <section className="card">
          <div className="card-header">
            <h2 className="card-title">Dishes</h2>
            <div className="segmented" role="group" aria-label="Sort dishes">
              {SORTS.map((s) => (
                <button key={s.key} aria-pressed={sort === s.key} onClick={() => setSort(s.key)}>
                  {s.label}
                </button>
              ))}
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
                  <span className="field-label">Category</span>
                  <input className="input" value={draft.category} onChange={(e) => setDraft({ ...draft, category: e.target.value })} />
                </label>
                <label className="field">
                  <span className="field-label">Price</span>
                  <input className="input" inputMode="decimal" value={draft.price} onChange={(e) => setDraft({ ...draft, price: e.target.value })} />
                </label>
                <label className="field">
                  <span className="field-label">Estimated cost</span>
                  <input className="input" inputMode="decimal" value={draft.cost} onChange={(e) => setDraft({ ...draft, cost: e.target.value })} />
                </label>
                <button
                  className="btn btn-primary"
                  disabled={busy || !draft.name.trim() || !draft.category.trim() || !(Number(draft.price) > 0)}
                  onClick={async () => {
                    const ok = await run(
                      () =>
                        createMenuItem({
                          name: draft.name.trim(),
                          category: draft.category.trim(),
                          price: Number(draft.price),
                          cost: Number(draft.cost) || 0,
                        }),
                      'Dish added',
                    )
                    if (ok) {
                      setDraft(BLANK)
                      setAdding(false)
                    }
                  }}
                >
                  Save dish
                </button>
              </div>
            </div>
          )}

          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Dish</th>
                  <th className="num">Price</th>
                  <th className="num">Cost</th>
                  <th className="num">Sold</th>
                  <th className="num">Revenue</th>
                  <th style={{ width: '14%' }}>Margin</th>
                  <th className="actions" aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {sorted.length === 0 && (
                  <tr>
                    <td colSpan={7} className="empty">No dishes yet.</td>
                  </tr>
                )}
                {sorted.map((row) => {
                  const onPromo = row.regular_price !== null
                  return (
                    <Fragment key={row.id}>
                      <tr style={row.is_available ? undefined : { opacity: 0.6 }}>
                        <td>
                          <div className="row wrap">
                            <span className="cell-title">{row.name}</span>
                            {!row.is_available && <span className="badge">Off menu</span>}
                            {onPromo && (
                              <span className="badge badge-brand" title={row.promo_ends_at ? `Ends ${dateTime(row.promo_ends_at)}` : 'No end date'}>
                                Promo{row.promo_ends_at ? ` · ends ${dateTime(row.promo_ends_at)}` : ''}
                              </span>
                            )}
                          </div>
                          <div className="cell-sub clamp-2" title={row.description ?? undefined}>
                            {row.category}
                            {row.description ? ` · ${row.description}` : ''}
                          </div>
                        </td>
                        <td className="num">
                          {editingId === row.id ? (
                            <input
                              className="input input-sm"
                              style={{ width: 80, textAlign: 'right' }}
                              aria-label={`Price for ${row.name}`}
                              autoFocus
                              inputMode="decimal"
                              value={editDraft.price}
                              onChange={(e) => setEditDraft({ ...editDraft, price: e.target.value })}
                            />
                          ) : (
                            <>
                              <span className="strong">{money(row.price, true)}</span>
                              {onPromo && <div className="cell-sub" style={{ textDecoration: 'line-through' }}>{money(row.regular_price!, true)}</div>}
                            </>
                          )}
                        </td>
                        <td className="num">
                          {money(row.cost, true)}
                          <div className="cell-sub">{row.cost_source === 'recipe' ? 'from recipe' : 'estimated'}</div>
                        </td>
                        <td className="num">{row.units_sold}</td>
                        <td className="num">
                          {money(row.revenue)}
                          <div className="cell-sub">{money(row.profit)} profit</div>
                        </td>
                        <td>
                          <div className="row">
                            <div className="meter" style={{ flex: 1 }}>
                              <div className="meter-fill chart" style={{ width: `${Math.max(0, Math.min(100, row.margin_pct))}%` }} />
                            </div>
                            <span className="tabular small" style={{ width: 44, textAlign: 'right' }}>
                              {pct(row.margin_pct, 0)}
                            </span>
                          </div>
                        </td>
                        <td className="actions">
                          {editingId === row.id ? (
                            <div className="row" style={{ justifyContent: 'flex-end' }}>
                              <button className="btn btn-ghost btn-sm" onClick={() => setEditingId(null)}>
                                Cancel
                              </button>
                              <button
                                className="btn btn-primary btn-sm"
                                disabled={busy || !(Number(editDraft.price) >= 0)}
                                onClick={async () => (await run(() => updateMenuItem(row.id, { price: Number(editDraft.price) }), 'Price updated')) && setEditingId(null)}
                              >
                                Save
                              </button>
                            </div>
                          ) : (
                            <div className="row" style={{ justifyContent: 'flex-end' }}>
                              <button
                                className="btn btn-ghost btn-sm"
                                onClick={() => {
                                  setEditingId(row.id)
                                  setEditDraft({ price: String(row.price), description: row.description ?? '' })
                                }}
                              >
                                Price
                              </button>
                              <button className="btn btn-ghost btn-sm" aria-expanded={recipeFor === row.id} onClick={() => setRecipeFor(recipeFor === row.id ? null : row.id)}>
                                Recipe
                              </button>
                              <button
                                className="btn btn-ghost btn-sm"
                                disabled={busy}
                                onClick={() => run(() => updateMenuItem(row.id, { is_available: !row.is_available }), row.is_available ? `${row.name} taken off the menu` : `${row.name} is back on`)}
                              >
                                {row.is_available ? 'Take off' : 'Put back'}
                              </button>
                              {row.units_sold === 0 && (
                                <button
                                  className="btn btn-ghost btn-sm negative"
                                  disabled={busy}
                                  onClick={() => window.confirm(`Delete ${row.name}? It has never sold.`) && run(() => deleteMenuItem(row.id), `${row.name} deleted`)}
                                >
                                  Delete
                                </button>
                              )}
                            </div>
                          )}
                        </td>
                      </tr>
                      {recipeFor === row.id && (
                        <tr>
                          <td colSpan={7} style={{ background: 'var(--surface-muted)' }}>
                            <RecipeEditor menuItemId={row.id} ingredients={app.inventory} toast={app.toast} onSaved={load} onClose={() => setRecipeFor(null)} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
          <div className="card-footer">
            Dishes with a recipe are costed from their ingredients and draw them from stock when sold. Dishes that have sold
            can’t be deleted — take them off the menu instead, so their sales history stays intact.
          </div>
        </section>
      </div>
    </main>
  )
}
