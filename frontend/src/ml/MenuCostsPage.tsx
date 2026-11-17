import { useEffect, useState } from 'react'
import { approveAction, rejectAction } from '../api/client'
import type { AppContext } from '../app/context'
import { readHashParam } from '../lib/useHashRoute'
import { useLoad } from '../lib/useLoad'
import { type DishRow, fetchMenuCosts, type MenuCosts, requestReprice } from './api'
import { Scatter, SortedBars, Sparkline } from './charts'
import { DishCostPage } from './DishCostPage'
import { dayLabel, dollars, percent, timeLabel } from './format'
import {
  HeaderSentence,
  LoadFailed,
  MoneyDelta,
  ProposalCard,
  SetupChecklist,
  type SetupStep,
  SidePanel,
  Skeleton,
  StateBanner,
  StatTile,
  StatusWord,
  TileRow,
  useDeferred,
  useRowLimit,
  useWhy,
  ViewAsTable,
  type Why,
} from './ui'

const TITLE = 'Menu costs'
const STEPS: Record<string, { label: string; detail: string; action: string; href: string }> = {
  invoices: { label: 'Add supplier invoices', detail: 'Ingredient prices come from your invoices.', action: 'Open invoices', href: '#invoices' },
  recipes: { label: 'Confirm a recipe', detail: 'A dish is costed from its confirmed recipe.', action: 'Open recipes', href: '#recipes' },
  costed: { label: 'Price every recipe line', detail: 'Each ingredient needs a matched invoice price.', action: 'Open recipes', href: '#recipes' },
  prices: { label: 'Add menu prices', detail: 'Import orders, or set prices on the menu.', action: 'Open connections', href: '#connect' },
}

/** The words after the number in the header sentence. */
export function headerSentence(data: MenuCosts): string {
  if (data.state === 'setup' || !data.header) {
    const done = data.setup.filter((s) => s.done).length
    return `${done} of ${data.setup.length} setup steps are done.`
  }
  const target = percent(data.target)
  const n = data.header.dishes_over
  if (n === 0) return `No dishes are over your ${target} food cost target.`
  return `${n} ${n === 1 ? 'dish is' : 'dishes are'} over your ${target} food cost target. Cost: about ${dollars(data.header.weekly_dollars)} a week.`
}

export function MenuCostsPage({ app }: { app: AppContext }) {
  // "#menu-costs/abc" is one dish; "#menu-costs" is the list.
  const [param, setParam] = useState(readHashParam)
  useEffect(() => {
    const onChange = () => setParam(readHashParam())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return param ? <DishCostPage app={app} recipeId={param} /> : <MenuCostsList app={app} />
}

function reviewFromHash(): string | null {
  return new URLSearchParams(window.location.hash.split('?')[1] ?? '').get('review')
}

function MenuCostsList({ app }: { app: AppContext }) {
  const load = useLoad(fetchMenuCosts, [])
  const why = useWhy()
  const deferred = useDeferred()
  const [tab, setTab] = useState<'cost' | 'map'>('cost')
  const [reviewing, setReviewing] = useState<string | null>(reviewFromHash)
  const [decided, setDecided] = useState<Record<string, 'approved' | 'rejected'>>({})
  const [busy, setBusy] = useState(false)
  const data = load.data
  const limit = useRowLimit(data?.dishes ?? [])

  if (load.status === 'loading') return <main className="page ml-page"><Skeleton title={TITLE} /></main>
  if (!data) return <main className="page ml-page"><LoadFailed title={TITLE} what="Menu costs" onRetry={() => void load.reload()} /></main>

  const updated = data.computed_at ? `${dayLabel(data.computed_at)} ${timeLabel(data.computed_at)}` : undefined
  if (data.state === 'setup') {
    const steps: SetupStep[] = data.setup.map((s) => ({ label: STEPS[s.key].label, detail: STEPS[s.key].detail, done: s.done, action: { label: STEPS[s.key].action, href: STEPS[s.key].href } }))
    return (
      <main className="page ml-page">
        <HeaderSentence title={TITLE} sentence={headerSentence(data)} state="setup" />
        <SetupChecklist title="Before costs can show" steps={steps} />
      </main>
    )
  }

  const tiles = data.tiles!
  const over = data.header?.dishes_over ?? 0
  const target = Number(data.target)
  const costed = data.dishes.filter((d) => d.food_cost_pct != null)
  const top = [...costed].sort((a, b) => Number(b.food_cost_pct) - Number(a.food_cost_pct))
  const statusOf = (dish: DishRow) => decided[dish.recipe_id] ?? dish.finding?.proposal_status ?? null

  const decide = (dish: DishRow, decision: 'approved' | 'rejected') => {
    const id = dish.finding?.proposal_id
    if (!id) return
    setDecided((d) => ({ ...d, [dish.recipe_id]: decision }))
    setReviewing(null)
    deferred.start(dish.recipe_id, decision === 'approved' ? `${dish.name} reprice approved.` : `${dish.name} reprice dismissed.`, () => {
      const call = decision === 'approved' ? approveAction(id) : rejectAction(id)
      call
        .then(() => app.refresh())
        .catch((err: unknown) => {
          setDecided((d) => {
            const next = { ...d }
            delete next[dish.recipe_id]
            return next
          })
          app.toast('The decision was not saved', err instanceof Error ? err.message : 'Try again.')
        })
    }, () =>
      setDecided((d) => {
        const next = { ...d }
        delete next[dish.recipe_id]
        return next
      }),
    )
  }
  const suggest = async (dish: DishRow) => {
    setBusy(true)
    try {
      await requestReprice(dish.recipe_id)
      await load.reload()
    } catch (err) {
      app.toast('No price was suggested', err instanceof Error ? err.message : 'Try again.')
    } finally {
      setBusy(false)
    }
  }

  const tileWhy = {
    over: (): Why => ({
      result: `${tiles.dishes_over.value} of ${tiles.dishes_over.of} dishes`,
      formula: `A dish is over target when food cost is at least ${percent(target + 0.02)} and it sold 10 or more in 28 days`,
      inputs: top.map((d) => ({ label: d.name, value: `${percent(d.food_cost_pct ?? 0)} food cost, ${Number(d.sold_28).toLocaleString('en-US')} sold${d.finding ? ', over target' : ''}`, source: { label: 'Dish', href: `#menu-costs/${d.recipe_id}` } })),
      dataBasis: `${tiles.dishes_over.of} dishes with a full cost and 28 days of sales`,
      freshness: updated ? `Computed ${updated}.` : 'Not computed yet.',
      fix: { label: 'Open recipes', href: '#recipes' },
    }),
    average: (): Why => ({
      result: `${percent(tiles.average_food_cost.value ?? 0)} of sales`,
      formula: 'Average food cost = cost of every plate sold / sales of those plates, last 28 days',
      inputs: costed.filter((d) => Number(d.sold_28) > 0 && !d.partial).map((d) => ({ label: d.name, value: `${Number(d.sold_28).toLocaleString('en-US')} sold x ${dollars(d.plate_cost)} a plate`, source: { label: 'Dish', href: `#menu-costs/${d.recipe_id}` } })),
      dataBasis: '28 days of sales and the latest invoice prices',
      freshness: updated ? `Computed ${updated}.` : 'Not computed yet.',
      fix: { label: 'Open recipes', href: '#recipes' },
    }),
    increase: (): Why => {
      const up = tiles.largest_increase!
      return {
        result: `${up.name} up ${percent(up.pct)}`,
        formula: 'Increase = (price now - price 28 days ago) / price 28 days ago',
        inputs: [
          { label: 'Price now', value: `$${up.now} per ${up.base_unit}`, source: { label: 'Invoice', href: '#invoices' } },
          { label: `Price on ${dayLabel(up.since)}`, value: `$${up.then} per ${up.base_unit}`, source: { label: 'Invoice', href: '#invoices' } },
        ],
        dataBasis: 'Invoice prices of the ingredients in confirmed recipes',
        freshness: updated ? `Computed ${updated}.` : 'Not computed yet.',
        fix: { label: 'Open invoices', href: '#invoices' },
      }
    },
  }

  const review = reviewing ? data.dishes.find((d) => d.recipe_id === reviewing) : undefined
  const pending = deferred.pendingId ? data.dishes.find((d) => d.recipe_id === deferred.pendingId) : undefined

  return (
    <main className={`page ml-page${data.stale ? ' ml-stale' : ''}`}>
      <HeaderSentence title={TITLE} sentence={headerSentence(data)} updated={updated} state="live" />
      {data.stale && data.last_sales_date && (
        <StateBanner kind="stale" text={`Sales were last imported ${dayLabel(data.last_sales_date)}. Import a newer export.`} action={{ label: 'Open connections', href: '#connect' }} />
      )}
      <TileRow>
        <StatTile value={String(tiles.dishes_over.value)} label="Dishes over target" compare={`${tiles.dishes_over.then} 28 days ago`} tone={tiles.dishes_over.value > tiles.dishes_over.then ? 'bad' : undefined} onWhy={() => why.show(tileWhy.over())} />
        {tiles.average_food_cost.value != null && (
          <StatTile value={percent(tiles.average_food_cost.value)} label="Average food cost" compare={`${percent(tiles.average_food_cost.then ?? tiles.average_food_cost.value)} 28 days ago, target ${percent(target)}`} tone={Number(tiles.average_food_cost.value) > target ? 'bad' : 'good'} onWhy={() => why.show(tileWhy.average())} />
        )}
        {tiles.largest_increase && <StatTile value={percent(tiles.largest_increase.pct)} label="Largest ingredient increase" compare={`${tiles.largest_increase.name}, last 28 days`} tone="bad" onWhy={() => why.show(tileWhy.increase())} />}
      </TileRow>

      <div className="ml-tabs" role="tablist" aria-label="Chart">
        <button role="tab" aria-selected={tab === 'cost'} className={`ml-tab${tab === 'cost' ? ' active' : ''}`} onClick={() => setTab('cost')}>
          Food cost
        </button>
        <button role="tab" aria-selected={tab === 'map'} className={`ml-tab${tab === 'map' ? ' active' : ''}`} onClick={() => setTab('map')}>
          Menu map
        </button>
      </div>
      {tab === 'cost' ? (
        <ViewAsTable
          title={top.length ? `${top[0].name} has the highest food cost, ${percent(top[0].food_cost_pct ?? 0)}` : 'No dish has a menu price yet'}
          summary={`Food cost by dish against the ${percent(target)} target. ${over} of ${data.dishes.length} dishes are over.`}
          chart={<SortedBars bars={top.slice(0, 15).map((d) => ({ key: d.recipe_id, label: d.name, value: Number(d.food_cost_pct) * 100, source: 'Source: invoices and the recipe' }))} unit="food cost, % of menu price" format={(v) => `${v.toFixed(1)}%`} target={{ value: target * 100, label: 'Target' }} onPick={(key) => (window.location.hash = `#menu-costs/${key}`)} />}
          columns={['Dish', 'Food cost', 'Target']}
          rows={top.slice(0, 15).map((d) => [d.name, percent(d.food_cost_pct ?? 0), percent(d.target)])}
        />
      ) : (
        <ViewAsTable
          title="Dishes that sell well and keep little are the ones to reprice"
          summary="Each dish by units sold in the last 28 days and dollars kept a plate, with the median of each."
          chart={<Scatter points={costed.filter((d) => d.margin != null).map((d) => ({ key: d.recipe_id, label: d.name, x: Number(d.sold_28), y: Number(d.margin) }))} xUnit="units sold, last 28 days" yUnit="dollars kept a plate" formatY={(v) => dollars(v)} corners={['Keep', 'Promote', 'Reprice', 'Rethink']} onPick={(key) => (window.location.hash = `#menu-costs/${key}`)} />}
          columns={['Dish', 'Sold, last 28 days', 'Kept a plate']}
          rows={costed.filter((d) => d.margin != null).map((d) => [d.name, Number(d.sold_28).toLocaleString('en-US'), dollars(d.margin ?? 0)])}
        />
      )}

      <section className="card" aria-label="Dishes">
        <div className="table-wrap">
          <table className="table ml-table">
            <thead>
              <tr>
                <th>Dish</th>
                <th className="num ml-hide-phone">Price</th>
                <th className="num ml-hide-phone">Cost per plate</th>
                <th className="num">Food cost %</th>
                <th className="ml-hide-phone">Change in 28 days</th>
                <th className="num ml-hide-phone">Sold in 28 days</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {limit.shown.map((dish) => {
                const status = statusOf(dish)
                return (
                  <tr key={dish.recipe_id}>
                    <td>
                      <a className="strong" href={`#menu-costs/${dish.recipe_id}`}>
                        {dish.name}
                      </a>
                      {dish.partial && <div className="muted small">Missing: {dish.unpriced.join(', ')}</div>}
                    </td>
                    <td className="num ml-hide-phone">{dish.price ? dollars(dish.price) : <span className="muted">No price</span>}</td>
                    <td className="num ml-hide-phone">
                      <button className="ml-why-link ml-cell-link" onClick={() => why.open('dish_costs', dish.dish_cost_id)}>
                        {dish.partial ? `at least ${dollars(dish.plate_cost)}` : dollars(dish.plate_cost)}
                      </button>
                    </td>
                    <td className="num">
                      {dish.food_cost_pct && !dish.partial ? (
                        <>
                          <button className="ml-why-link ml-cell-link" onClick={() => why.open('dish_costs', dish.dish_cost_id, 'food_cost_pct')}>
                            {percent(dish.food_cost_pct)}
                          </button>
                          {dish.finding && <StatusWord tone="bad">Over target</StatusWord>}
                        </>
                      ) : (
                        <span className="muted">Not known</span>
                      )}
                    </td>
                    <td className="ml-hide-phone">
                      <span className="ml-trend">
                        {dish.change_28 != null ? <MoneyDelta amount={dish.change_28} /> : <span className="muted">No earlier cost</span>}
                        <Sparkline values={dish.trend.map((v) => (v == null ? null : Number(v)))} label={`${dish.name}: cost per plate over 12 weeks`} />
                      </span>
                    </td>
                    <td className="num ml-hide-phone">{Number(dish.sold_28).toLocaleString('en-US')}</td>
                    <td>
                      {deferred.pendingId === dish.recipe_id ? (
                        <button className="btn btn-secondary btn-sm" onClick={deferred.undo}>
                          Undo
                        </button>
                      ) : status === 'approved' ? (
                        <StatusWord tone="good">Approved</StatusWord>
                      ) : status === 'rejected' ? (
                        <StatusWord tone="neutral">Dismissed</StatusWord>
                      ) : dish.finding && app.canManage ? (
                        <button className="btn btn-secondary btn-sm" onClick={() => setReviewing(dish.recipe_id)}>
                          Review price
                        </button>
                      ) : null}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        {limit.more && <div className="card-footer">{limit.more}</div>}
      </section>
      {data.left_out > 0 && (
        <p className="muted small">
          {data.left_out} invoice {data.left_out === 1 ? 'price was' : 'prices were'} left out as more than 3 times, or under a third of, the usual. Open a dish to review.
        </p>
      )}

      {review && review.finding && (
        <SidePanel title={review.name} onClose={() => setReviewing(null)}>
          {review.finding.proposal_id && review.finding.suggested_price && review.price ? (
            <ProposalCard
              title={`Raise the price to ${dollars(review.finding.suggested_price)}`}
              sentence={`${review.name} costs ${dollars(review.plate_cost)} a plate, ${percent(review.food_cost_pct ?? 0)} of its ${dollars(review.price)} price.`}
              note={`${dollars(review.finding.suggested_price)} brings food cost to ${percent(review.finding.food_cost_pct_at_suggested ?? 0)}.${review.finding.cap_applied ? ' The rise is held to 10% of the price.' : ''} Change the price in your POS after you approve.`}
              impact={review.finding.weekly_dollars}
              primary={{ label: 'Approve', onClick: () => decide(review, 'approved') }}
              onDismiss={() => decide(review, 'rejected')}
              onWhy={() => why.open('findings', review.finding!.id)}
            />
          ) : (
            <div className="ml-stack">
              <p>No price is suggested yet for {review.name}.</p>
              <button className="btn btn-primary" disabled={busy} onClick={() => void suggest(review)}>
                Suggest price
              </button>
            </div>
          )}
        </SidePanel>
      )}
      {pending && deferred.bar}
      {why.panel}
    </main>
  )
}
