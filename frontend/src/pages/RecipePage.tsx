import { type FormEvent, useState } from 'react'
import { type DishRecipe, type DishRecipeLineIn, costDishRecipe, editDishRecipe, getDishRecipe, repriceDishRecipe } from '../api/client'
import type { AppContext } from '../app/context'
import { Icon } from '../components/Icon'
import { Loaded } from '../components/PageState'
import { money, qty, shortDate } from '../lib/format'
import { useHashParam } from '../lib/useHashRoute'
import { useLoad } from '../lib/useLoad'

/** Eight weeks of plate cost as one line; the only chart. */
function CostLine({ points }: { points: { week_of: string; cost: number }[] }) {
  if (points.length < 2) return null
  const w = 230
  const h = 60
  const costs = points.map((p) => p.cost)
  const min = Math.min(...costs)
  const max = Math.max(...costs)
  const span = max - min || 1
  const x = (i: number) => 8 + (i * (w - 16)) / (points.length - 1)
  const y = (c: number) => 8 + ((max - c) * (h - 16)) / span
  const d = points.map((p, i) => `${x(i)},${y(p.cost)}`).join(' ')
  return (
    <div className="pi-sparkline">
      <svg role="img" aria-label={`Plate cost from ${money(points[0].cost, true)} to ${money(points[points.length - 1].cost, true)}`} width={w} height={h} viewBox={`0 0 ${w} ${h}`}>
        <polyline points={d} fill="none" stroke="#111" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={x(points.length - 1)} cy={y(points[points.length - 1].cost)} r={4} fill="#111" />
      </svg>
      <div className="pi-sparkline-labels">
        <div>
          week of {shortDate(points[0].week_of)}: {money(points[0].cost, true)}
        </div>
        <div>
          week of {shortDate(points[points.length - 1].week_of)}: {money(points[points.length - 1].cost, true)}
        </div>
      </div>
    </div>
  )
}

/** Lines as a small form; saving makes a recipe proposal, not a direct change. */
function EditLines({ recipe, app, onDone }: { recipe: DishRecipe; app: AppContext; onDone: () => void }) {
  const [rows, setRows] = useState<DishRecipeLineIn[]>(recipe.lines.map((l) => ({ ingredient_text: l.ingredient_text ?? l.item_name, item_id: l.item_id, sub_recipe_id: l.sub_recipe_id, qty: l.qty, unit: l.unit })))
  const [busy, setBusy] = useState(false)
  const save = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    try {
      const out = await editDishRecipe(recipe.id, rows.filter((r) => r.qty > 0))
      app.toast('Edit proposed', 'Approve it under Recommendations to confirm the new lines.')
      app.refresh()
      onDone()
      void out
    } catch (err) {
      app.toast('Couldn’t save', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <form className="pi-stack-sm" onSubmit={save} aria-label="Edit recipe lines">
      {rows.map((r, i) => (
        <div key={i} className="row wrap">
          <span style={{ minWidth: 160 }}>{r.ingredient_text}</span>
          <input className="pi-input pi-input-sm" type="number" step="any" min={0} value={r.qty} aria-label={`Quantity of ${r.ingredient_text}`} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, qty: Number(e.target.value) } : x)))} style={{ width: 90 }} />
          <input className="pi-input pi-input-sm" value={r.unit} aria-label={`Unit of ${r.ingredient_text}`} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, unit: e.target.value } : x)))} style={{ width: 80 }} />
        </div>
      ))}
      <p className="pi-secondary">Set a quantity to 0 to drop a line. The change is proposed, and takes effect when approved.</p>
      <div className="row">
        <button type="submit" className="pi-btn" disabled={busy}>
          Propose these lines
        </button>
        <button type="button" className="pi-link" onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  )
}

/** One recipe: its lines and what each costs this week, the price points behind them, eight weeks of plate cost. */
export function RecipePage({ app }: { app: AppContext }) {
  const id = useHashParam()
  const load = useLoad(() => getDishRecipe(id), [id])
  const [busy, setBusy] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)

  const act = async (key: string, work: () => Promise<unknown>, done: string, detail?: string) => {
    setBusy(key)
    try {
      await work()
      app.toast(done, detail)
      await load.reload()
      app.refresh()
    } catch (err) {
      app.toast('Couldn’t do that', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  return (
    <main className="pi">
      <div className="pi-page">
        <a className="pi-back" href="#recipes">
          <span style={{ display: 'inline-flex', transform: 'rotate(180deg)' }}>
            <Icon name="arrowRight" size={16} />
          </span>
          Recipes
        </a>
        <Loaded load={load} what="the recipe" placeholderHeight={200}>
          {(r) => (
            <>
              <header className="pi-stack-sm">
                <h1 className="pi-h1">{r.name}</h1>
                <p className="pi-secondary">
                  Yields {qty(r.yield_qty)} {r.yield_unit}
                  {r.pos_item_id && ` · sold as POS item ${r.pos_item_id}`} · {r.status}
                </p>
              </header>
              <section className="pi-stack">
                <div className="pi-card-l1" style={{ fontWeight: 400 }}>
                  <p>Plate cost{r.plate_cost_week ? `, week of ${shortDate(r.plate_cost_week)}` : ''}</p>
                  <span className="pi-headline pi-money">{r.plate_cost != null ? money(r.plate_cost, true) : '—'}</span>
                </div>
                {r.price_a_plate != null && (
                  <p className="pi-secondary">
                    Sells at {money(r.price_a_plate, true)}, {qty(r.sold_a_week ?? 0)} a week
                    {r.margin_pct != null && `, margin ${r.margin_pct}%`}.
                  </p>
                )}
                {r.unpriced.length > 0 && <p className="pi-secondary">Not priced yet: {r.unpriced.join(', ')}. The cost shown leaves those out.</p>}
                <div className="pi-stack-sm">
                  <button type="button" className="pi-btn-o" disabled={busy === 'cost'} onClick={() => void act('cost', () => costDishRecipe(r.id), 'Plate cost recomputed')}>
                    Recompute cost
                  </button>
                  {app.canManage && (
                    <button type="button" className="pi-btn-o" disabled={busy === 'reprice'} onClick={() => void act('reprice', () => repriceDishRecipe(r.id), 'Reprice suggested', 'Under Recommendations.')}>
                      Suggest a reprice
                    </button>
                  )}
                  {app.canManage && !editing && (
                    <button type="button" className="pi-link" style={{ alignSelf: 'flex-start' }} onClick={() => setEditing(true)}>
                      Edit lines
                    </button>
                  )}
                </div>
                {editing && <EditLines recipe={r} app={app} onDone={() => setEditing(false)} />}
              </section>
              {r.history.length > 1 && (
                <section className="pi-stack-sm">
                  <p className="pi-label-strong">Last 8 weeks, per {r.yield_unit}</p>
                  <CostLine points={r.history} />
                </section>
              )}
              <section className="pi-stack-sm">
                <p className="pi-label-strong">Lines</p>
                <div className="pi-table-wrap">
                  <table className="pi-table">
                    <thead>
                      <tr>
                        <th>Ingredient</th>
                        <th className="num">Qty</th>
                        <th>Unit</th>
                        <th className="num">Cost</th>
                      </tr>
                    </thead>
                    <tbody>
                      {r.lines.map((l) => (
                        <tr key={l.id}>
                          <td>
                            {l.item_id ? <a href={`#item/${l.item_id}`}>{l.item_name}</a> : l.sub_recipe_id ? <a href={`#recipe/${l.sub_recipe_id}`}>{l.sub_recipe_name}</a> : l.ingredient_text}
                          </td>
                          <td className="num pi-money">{qty(l.qty)}</td>
                          <td>{l.unit}</td>
                          <td className="num pi-money">{l.cost != null ? money(l.cost, true) : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {r.basis.length > 0 && (
                  <p className="pi-secondary">
                    Priced from {r.basis.filter((b) => b.item_name).map((b) => `${b.item_name} at ${money(b.per_unit ?? 0, true)}/${b.unit}`).join(', ')}.
                  </p>
                )}
              </section>
            </>
          )}
        </Loaded>
      </div>
    </main>
  )
}
