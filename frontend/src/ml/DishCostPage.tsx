import { useState } from 'react'
import { approveAction, rejectAction } from '../api/client'
import type { AppContext } from '../app/context'
import { useLoad } from '../lib/useLoad'
import { type DishDetail, fetchDish, requestReprice, saveRecipe } from './api'
import { SortedBars, StepLines } from './charts'
import { dayLabel, dollars, percent, timeLabel } from './format'
import { HeaderSentence, LoadFailed, ProposalCard, SidePanel, Skeleton, StateBanner, StatTile, StatusWord, TileRow, useDeferred, useWhy, ViewAsTable } from './ui'

/** The first sentence of a dish page: what it costs, against its price. */
export function dishSentence(data: DishDetail): string {
  const cost = data.cost
  if (!cost) return `${data.recipe.name} has no cost yet. 0 of ${data.recipe.lines.length} lines are priced.`
  const partial = cost.lines_priced !== cost.lines_total
  if (partial) return `${data.recipe.name} costs at least ${dollars(cost.plate_cost)} a plate. ${cost.lines_total - cost.lines_priced} of ${cost.lines_total} lines have no price.`
  if (!cost.menu_price || !cost.food_cost_pct) return `${data.recipe.name} costs ${dollars(cost.plate_cost)} a plate. It has no menu price.`
  return `${data.recipe.name} costs ${dollars(cost.plate_cost)} a plate, ${percent(cost.food_cost_pct)} of its ${dollars(cost.menu_price)} price.`
}

export function DishCostPage({ app, recipeId }: { app: AppContext; recipeId: string }) {
  const load = useLoad(() => fetchDish(recipeId), [recipeId])
  const why = useWhy()
  const deferred = useDeferred()
  const [editing, setEditing] = useState(false)
  const [decided, setDecided] = useState<'approved' | 'rejected' | null>(null)
  const [busy, setBusy] = useState(false)
  const data = load.data

  if (load.status === 'loading') return <main className="page ml-page"><Skeleton title="Menu costs" /></main>
  if (!data) return <main className="page ml-page"><LoadFailed title="Menu costs" what="This dish" onRetry={() => void load.reload()} /></main>

  const cost = data.cost
  const updated = data.computed_at ? `${dayLabel(data.computed_at)} ${timeLabel(data.computed_at)}` : undefined
  const partial = cost ? cost.lines_priced !== cost.lines_total : true
  const then = data.history.length > 4 ? data.history[data.history.length - 5] : undefined
  const priced = data.history.filter((h) => h.plate_cost != null)
  const first = priced[0]
  const last = priced[priced.length - 1]
  const moved = first && last && Number(first.plate_cost) > 0 ? (Number(last.plate_cost) - Number(first.plate_cost)) / Number(first.plate_cost) : 0
  const weeks = priced.length
  const chartTitle =
    weeks < 2
      ? `${data.recipe.name} has ${weeks} ${weeks === 1 ? 'week' : 'weeks'} of cost history`
      : Math.abs(moved) < 0.005
        ? `${data.recipe.name} cost per plate held steady for ${weeks} weeks`
        : `${data.recipe.name} cost per plate ${moved > 0 ? 'rose' : 'fell'} ${percent(Math.abs(moved))} in ${weeks} weeks`
  const lines = data.lines.filter((l) => l.priced && l.line_cost)
  const proposal = data.proposal
  const status = decided ?? proposal?.status ?? null

  const decide = (decision: 'approved' | 'rejected') => {
    if (!proposal) return
    setDecided(decision)
    deferred.start(recipeId, decision === 'approved' ? 'Reprice approved.' : 'Reprice dismissed.', () => {
      const call = decision === 'approved' ? approveAction(proposal.id) : rejectAction(proposal.id)
      call
        .then(() => {
          app.refresh()
          return load.reload()
        })
        .catch((err: unknown) => {
          setDecided(null)
          app.toast('The decision was not saved', err instanceof Error ? err.message : 'Try again.')
        })
    }, () => setDecided(null))
  }
  const suggest = async () => {
    setBusy(true)
    try {
      await requestReprice(recipeId)
      await load.reload()
    } catch (err) {
      app.toast('No price was suggested', err instanceof Error ? err.message : 'Try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className={`page ml-page${data.stale ? ' ml-stale' : ''}`}>
      <a className="ml-back" href="#menu-costs">
        Menu costs
      </a>
      <HeaderSentence title={data.recipe.name} sentence={dishSentence(data)} updated={updated} state={cost ? 'live' : 'setup'}>
        {app.canManage && (
          <button className="btn btn-secondary btn-sm" onClick={() => setEditing(true)}>
            Edit recipe
          </button>
        )}
      </HeaderSentence>
      {cost && partial && <StateBanner kind="learning" text={`Not priced: ${cost.unpriced.join('; ')}. The cost is a floor until they are.`} />}
      {cost && (
        <TileRow>
          <StatTile value={partial ? `at least ${dollars(cost.plate_cost)}` : dollars(cost.plate_cost)} label="Cost per plate" compare={then?.plate_cost ? `${dollars(then.plate_cost)} 28 days ago` : 'No cost 28 days ago'} onWhy={() => why.open('dish_costs', cost.id)} />
          {cost.food_cost_pct && !partial && <StatTile value={percent(cost.food_cost_pct)} label="Food cost" compare={`Target ${percent(data.target)}`} tone={Number(cost.food_cost_pct) > Number(data.target) ? 'bad' : 'good'} onWhy={() => why.open('dish_costs', cost.id, 'food_cost_pct')} />}
          {cost.margin && !partial && <StatTile value={dollars(cost.margin)} label="Kept per plate" compare={`${Number(data.sold_28).toLocaleString('en-US')} sold, last 28 days`} onWhy={() => why.open('dish_costs', cost.id, 'margin')} />}
        </TileRow>
      )}

      {cost && (
        <ViewAsTable
          title={chartTitle}
          summary={`Cost per plate and menu price by week for ${data.history.length} weeks, with a marker where an ingredient price changed.`}
          chart={<StepLines points={data.history.map((h) => ({ date: h.week, value: h.plate_cost == null ? null : Number(h.plate_cost), second: h.menu_price == null ? null : Number(h.menu_price), marks: h.changed }))} label="Cost per plate" secondLabel="Menu price" unit="dollars" format={(v) => dollars(v)} />}
          columns={['Week ending', 'Cost per plate', 'Menu price', 'Price changes']}
          rows={data.history.map((h) => [dayLabel(h.week), h.plate_cost == null ? 'Not priced' : dollars(h.plate_cost), h.menu_price == null ? 'None' : dollars(h.menu_price), h.changed.join(', ') || 'None'])}
        />
      )}

      {data.finding && (
        proposal && proposal.suggested_price && cost?.menu_price ? (
          status === 'pending' && app.canManage ? (
            <ProposalCard
              title={`Raise the price to ${dollars(proposal.suggested_price)}`}
              sentence={`Food cost falls from ${percent(cost.food_cost_pct ?? 0)} to ${percent(proposal.food_cost_pct_at_suggested ?? 0)} at ${dollars(proposal.suggested_price)}.`}
              note={`${proposal.cap_applied ? 'The rise is held to 10% of the price. ' : ''}Change the price in your POS after you approve.`}
              impact={data.finding.weekly_dollars}
              primary={{ label: 'Approve', onClick: () => decide('approved') }}
              onDismiss={() => decide('rejected')}
              onWhy={() => why.open('findings', data.finding!.id)}
            />
          ) : (
            <div className="card ml-proposal">
              <div className="ml-proposal-main">
                <StatusWord tone={status === 'approved' ? 'good' : 'neutral'}>{status === 'approved' ? `Approved: ${dollars(proposal.suggested_price)}. Change the price in your POS.` : status === 'rejected' ? 'Reprice dismissed' : 'A reprice is waiting for a manager'}</StatusWord>
              </div>
            </div>
          )
        ) : app.canManage ? (
          <div className="card ml-proposal">
            <div className="ml-proposal-main">
              <StatusWord tone="bad">Over target</StatusWord>
            </div>
            <button className="btn btn-primary" disabled={busy} onClick={() => void suggest()}>
              Suggest price
            </button>
          </div>
        ) : null
      )}

      {lines.length > 0 && (
        <section className="card" data-testid="ingredient-costs" aria-label="Cost by ingredient">
          <div className="card-header">
            <h2 className="card-title">{lines[0].name} is the largest cost, {dollars(lines[0].line_cost ?? 0)} a plate</h2>
          </div>
          <div className="card-body">
            <SortedBars
              bars={lines.map((l) => ({ key: l.line_id, label: l.name, value: Number(l.line_cost), note: l.change_28 != null && Number(l.change_28) !== 0 ? `${Number(l.change_28) > 0 ? 'up' : 'down'} ${dollars(Math.abs(Number(l.change_28)))} in 28 days` : 'no change in 28 days', source: l.supplier ? `Source: ${l.supplier}${l.price_date ? `, ${dayLabel(l.price_date)}` : ''}` : 'Source: the sub-recipe' }))}
              unit="dollars a plate"
              format={(v) => dollars(v)}
            />
          </div>
        </section>
      )}

      {data.left_out.length > 0 && (
        <section className="card" aria-label="Prices left out">
          <div className="card-header">
            <h2 className="card-title">
              {data.left_out.length} {data.left_out.length === 1 ? 'price was' : 'prices were'} left out of this cost
            </h2>
          </div>
          <ul className="list">
            {data.left_out.map((o) => (
              <li key={`${o.invoice_id}-${o.item}`} className="list-item">
                <div className="list-item-main">
                  <div className="strong">
                    {o.item}: ${o.unit_price} per {o.base_unit} on {dayLabel(o.day)}
                  </div>
                  <div className="muted small">The usual price is ${o.median} per {o.base_unit}. Check the invoice for a unit or reading error.</div>
                </div>
                <a className="btn btn-secondary btn-sm" href="#invoices">
                  Review invoice
                </a>
              </li>
            ))}
          </ul>
        </section>
      )}

      {editing && <EditRecipe data={data} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); void load.reload() }} app={app} />}
      {deferred.pendingId && deferred.bar}
      {why.panel}
    </main>
  )
}

/** Quantities, yields and portions, edited beside the page. Lines are added and removed on the Recipes page. */
function EditRecipe({ data, onClose, onSaved, app }: { data: DishDetail; onClose: () => void; onSaved: () => void; app: AppContext }) {
  const [portions, setPortions] = useState(String(Number(data.recipe.portions)))
  const [lines, setLines] = useState(data.recipe.lines.map((l) => ({ ...l, qty: String(Number(l.qty)), yieldPct: String(Math.round(Number(l.yield_pct) * 1000) / 10) })))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const set = (id: string, patch: Partial<(typeof lines)[number]>) => setLines((all) => all.map((l) => (l.id === id ? { ...l, ...patch } : l)))
  const save = async () => {
    const bad = lines.find((l) => !(Number(l.qty) > 0) || !(Number(l.yieldPct) > 0 && Number(l.yieldPct) <= 100))
    if (bad || !(Number(portions) > 0)) {
      setError(bad ? `${bad.name}: the quantity must be more than 0 and the yield from 1 to 100.` : 'Portions must be more than 0.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      await saveRecipe(data.recipe.id, {
        portions,
        lines: lines.map((l) => ({ ingredient_text: l.name, item_id: l.item_id, sub_recipe_id: l.sub_recipe_id, qty: l.qty, unit: l.unit, yield_pct: (Number(l.yieldPct) / 100).toFixed(4) })),
      })
      app.toast('Recipe saved', 'The cost was worked out again.')
      onSaved()
    } catch (err) {
      setError(err instanceof Error ? `The recipe was not saved. ${err.message}` : 'The recipe was not saved. Try again.')
    } finally {
      setSaving(false)
    }
  }
  return (
    <SidePanel title="Edit recipe" onClose={onClose}>
      <div className="ml-stack">
        <label className="field">
          <span className="field-label">Portions a batch</span>
          <input className="input" inputMode="decimal" value={portions} onChange={(e) => setPortions(e.target.value)} />
        </label>
        {lines.map((line) => (
          <fieldset key={line.id} className="ml-edit-line">
            <legend>{line.name}</legend>
            <label className="field">
              <span className="field-label">Quantity, {line.unit}</span>
              <input className="input" inputMode="decimal" value={line.qty} onChange={(e) => set(line.id, { qty: e.target.value })} />
            </label>
            {line.item_id && (
              <label className="field">
                <span className="field-label">Yield, %</span>
                <input className="input" inputMode="decimal" value={line.yieldPct} onChange={(e) => set(line.id, { yieldPct: e.target.value })} />
              </label>
            )}
          </fieldset>
        ))}
        {error && <StateBanner kind="error" text={error} />}
        <div className="row">
          <button className="btn btn-primary" disabled={saving} onClick={() => void save()}>
            Save
          </button>
          <button className="btn btn-secondary" onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    </SidePanel>
  )
}
