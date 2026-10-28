import { useState } from 'react'
import { listDishRecipes, runRecipeOnboarding } from '../api/client'
import type { AppContext } from '../app/context'
import { Empty, Loaded } from '../components/PageState'
import { money, qty } from '../lib/format'
import { useLoad } from '../lib/useLoad'

/** Every recipe with this week's plate cost. Editing happens on the recipe's own page, as a proposal. */
export function RecipesPage({ app }: { app: AppContext }) {
  const load = useLoad(listDishRecipes, [])
  const [busy, setBusy] = useState(false)

  const onboard = async () => {
    setBusy(true)
    try {
      const out = await runRecipeOnboarding()
      app.toast(out.proposed ? `${out.proposed} recipe${out.proposed === 1 ? '' : 's'} proposed` : 'Nothing new to propose', out.proposed ? 'Under Recommendations.' : 'Every top seller has a recipe or a proposal waiting.')
      app.refresh()
    } catch (err) {
      app.toast('Couldn’t do that', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Recipes</h1>
          <p className="page-subtitle">What each dish costs to plate this week, from the prices on your invoices.</p>
        </div>
        {app.canManage && (
          <button className="btn btn-secondary" disabled={busy} onClick={() => void onboard()}>
            Propose recipes for top sellers
          </button>
        )}
      </div>
      <Loaded load={load} what="recipes" placeholderHeight={160}>
        {(recipes) =>
          recipes.length === 0 ? (
            <Empty title="No recipes yet">
              Once sales are connected, the agent proposes a recipe for each top seller, built from what you buy. Or upload a recipe card under Documents.
            </Empty>
          ) : (
            <section className="card">
              <div className="table-wrap">
                <table className="table table-cards" aria-label="Recipes">
                  <thead>
                    <tr>
                      <th>Dish</th>
                      <th className="num">Plate cost</th>
                      <th className="num">Sells at</th>
                      <th className="num">Margin</th>
                      <th className="num">Sold a week</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recipes.map((r) => (
                      <tr key={r.id}>
                        <td data-label="Dish">
                          <a href={`#recipe/${r.id}`} className="cell-title">
                            {r.name}
                          </a>
                          <div className="cell-sub">
                            {r.lines.length} {r.lines.length === 1 ? 'line' : 'lines'}
                            {r.unpriced.length > 0 && `, ${r.unpriced.length} unpriced`}
                          </div>
                        </td>
                        <td className="num" data-label="Plate cost">{r.plate_cost != null ? money(r.plate_cost, true) : '—'}</td>
                        <td className="num" data-label="Sells at">{r.price_a_plate != null ? money(r.price_a_plate, true) : '—'}</td>
                        <td className="num" data-label="Margin">{r.margin_pct != null ? `${r.margin_pct}%` : '—'}</td>
                        <td className="num" data-label="Sold a week">{r.sold_a_week != null ? qty(r.sold_a_week) : '—'}</td>
                        <td data-label="Status">
                          <span className={`badge ${r.status === 'confirmed' ? 'badge-success' : 'badge-warning'}`}>{r.status}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )
        }
      </Loaded>
    </main>
  )
}
