import { useEffect, useState } from 'react'
import type { AppContext } from '../app/context'
import { readHashParam } from '../lib/useHashRoute'
import { useLoad } from '../lib/useLoad'
import { dismissFinding, fetchUsage, fetchUsageItem, setUsageTracking, type Usage, type UsageChecks, type UsageDetail, type UsageItem } from './api'
import { CoverageBar, GapLines, SortedBars, Sparkline } from './charts'
import { amount, dayLabel, dollars, percent, timeLabel } from './format'
import { HeaderSentence, LoadFailed, SetupChecklist, type SetupStep, SidePanel, Skeleton, StateBanner, StatTile, StatusWord, TileRow, useDeferred, useRowLimit, useWhy, ViewAsTable, type Why, takeHashParam } from './ui'

const TITLE = 'Usage'

export function usageSentence(data: Usage): string {
  if (data.state === 'setup') return `Recipes cover ${percent(data.coverage)} of sales. Usage needs ${percent(data.coverage_learning)}.`
  const n = data.header?.items ?? 0
  if (n === 0) return 'No ingredients are unexplained in the last 28 days.'
  return `About ${dollars(data.header?.weekly_dollars ?? 0)} a week of ingredients is unexplained, across ${n} ${n === 1 ? 'item' : 'items'}.`
}

export function UsagePage({ app }: { app: AppContext }) {
  const [param, setParam] = useState(readHashParam)
  useEffect(() => {
    const onChange = () => setParam(readHashParam())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return param ? <IngredientPage app={app} itemId={param} /> : <UsageList app={app} />
}

/** The three things a person can check; the module cannot tell waste from over-portioning from theft. */
function Checks({ checks, unit, gap }: { checks: UsageChecks | null; unit: string; gap: boolean }) {
  return (
    <ol className="ml-checks">
      {gap && <li>Recipes used more than was bought. Look for cash purchases that never reached FullHouse.</li>}
      {checks?.top_dish && (
        <li>
          Weigh five portions of {checks.top_dish.name}. The recipe says {amount(checks.top_dish.per_portion, unit)} a portion.
        </li>
      )}
      {checks?.last_delivery && (
        <li>
          Check the {dayLabel(checks.last_delivery.day)} delivery against its invoice. <a href="#invoices">Open invoices</a>
        </li>
      )}
      {checks?.top_dish && (
        <li>
          Review the recipe quantity. <a href={`#menu-costs/${checks.top_dish.recipe_id}`}>Open {checks.top_dish.name}</a>
        </li>
      )}
    </ol>
  )
}

function UsageList({ app }: { app: AppContext }) {
  const load = useLoad(fetchUsage, [])
  const why = useWhy()
  const deferred = useDeferred()
  const [checking, setChecking] = useState<string | null>(() => takeHashParam('checks'))
  const [dismissed, setDismissed] = useState<Record<string, boolean>>({})
  const data = load.data
  const limit = useRowLimit(data?.items ?? [])

  if (load.status === 'loading') return <main className="page ml-page"><Skeleton title={TITLE} /></main>
  if (!data) return <main className="page ml-page"><LoadFailed title={TITLE} what="Usage" onRetry={() => void load.reload()} /></main>

  const updated = data.computed_at ? `${dayLabel(data.computed_at)} ${timeLabel(data.computed_at)}` : undefined
  const coverageWords = `Recipes cover ${percent(data.coverage)} of sales. Findings need ${percent(data.coverage_live)}.`
  if (data.state === 'setup') {
    const steps: SetupStep[] = data.has_sales
      ? data.missing_recipes.slice(0, 6).map((m) => ({ label: `${m.has_recipe ? 'Price every line for' : 'Add a recipe for'} ${m.name}`, detail: m.share ? `${percent(m.share)} of sales, last 28 days` : undefined, done: false, action: { label: 'Open recipes', href: '#recipes' } }))
      : [{ label: 'Import sales', detail: 'Usage compares what sold with what was bought.', done: false, action: { label: 'Open connections', href: '#connect' } }]
    return (
      <main className="page ml-page">
        <HeaderSentence title={TITLE} sentence={usageSentence(data)} state="setup" />
        <div className="card ml-pad">
          <CoverageBar value={Number(data.coverage)} needed={Number(data.coverage_live)} label={coverageWords} />
        </div>
        <SetupChecklist title={data.has_sales ? 'Top sellers that need a costed recipe' : 'Before usage can show'} steps={steps} />
      </main>
    )
  }

  const tiles = data.tiles!
  const flaggedCount = tiles.flagged.value
  const top = data.items.filter((i) => Number(i.weekly_dollars) > 0).slice(0, 10)
  const period = `28 days to ${dayLabel(data.window_end)}`
  const isDismissed = (item: UsageItem) => dismissed[item.item_id] === true

  const dismiss = (item: UsageItem) => {
    const id = item.finding?.id
    if (!id) return
    setDismissed((d) => ({ ...d, [item.item_id]: true }))
    setChecking(null)
    deferred.start(item.item_id, `${item.name} finding dismissed.`, () => {
      dismissFinding(id)
        .then(() => load.reload())
        .catch((err: unknown) => {
          setDismissed((d) => ({ ...d, [item.item_id]: false }))
          app.toast('The finding was not dismissed', err instanceof Error ? err.message : 'Try again.')
        })
    }, () => setDismissed((d) => ({ ...d, [item.item_id]: false })))
  }

  const tileWhy = {
    unexplained: (): Why => ({
      result: `${dollars(tiles.unexplained.value)} a week`,
      formula: 'Sum of dollars a week for each ingredient over the thresholds: 5% of expected use and $25 a week',
      inputs: data.items.filter((i) => i.material).map((i) => ({ label: i.name, value: `${dollars(i.weekly_dollars)} a week`, source: { label: 'Ingredient', href: `#usage/${i.item_id}` } })),
      dataBasis: `Sales, invoices and recipes for the ${period}`,
      freshness: updated ? `Computed ${updated}.` : 'Not computed yet.',
      fix: { label: 'Open recipes', href: '#recipes' },
    }),
    coverage: (): Why => ({
      result: `${percent(tiles.coverage.value)} of sales`,
      formula: 'Recipe coverage = net sales of items with a confirmed, fully priced recipe / all net sales',
      inputs: data.missing_recipes.length
        ? data.missing_recipes.map((m) => ({ label: m.name, value: `${m.share ? percent(m.share) : '0.0%'} of sales, no costed recipe`, source: { label: 'Recipes', href: '#recipes' } }))
        : [{ label: 'Items without a costed recipe', value: '0' }],
      dataBasis: `Net sales for the ${period}`,
      freshness: updated ? `Computed ${updated}.` : 'Not computed yet.',
      fix: { label: 'Open recipes', href: '#recipes' },
    }),
    flagged: (): Why => ({
      result: `${flaggedCount} ${flaggedCount === 1 ? 'item' : 'items'}`,
      formula: 'An item is flagged when its gap passes the thresholds on two weekly runs in a row',
      inputs: data.items.filter((i) => i.finding).length
        ? data.items.filter((i) => i.finding).map((i) => ({ label: i.name, value: i.finding?.type === 'usage_data_gap' ? 'Bought less than recipes used' : `${dollars(i.weekly_dollars)} a week`, source: { label: 'Ingredient', href: `#usage/${i.item_id}` } }))
        : [{ label: 'Items over the thresholds two weeks running', value: '0' }],
      dataBasis: `Two weekly windows, the latest ending ${dayLabel(data.window_end)}`,
      freshness: updated ? `Computed ${updated}.` : 'Not computed yet.',
      fix: { label: 'Open recipes', href: '#recipes' },
    }),
  }
  const check = checking ? data.items.find((i) => i.item_id === checking) : undefined

  return (
    <main className={`page ml-page${data.stale ? ' ml-stale' : ''}`}>
      <HeaderSentence title={TITLE} sentence={usageSentence(data)} updated={updated} state={data.state} />
      {data.stale && <StateBanner kind="stale" text={data.last_sales_date ? `Sales were last imported ${dayLabel(data.last_sales_date)}. Import a newer export.` : 'No sales are imported. Import an export.'} action={{ label: 'Open connections', href: '#connect' }} />}
      {data.state === 'learning' && (
        <>
          <StateBanner kind="learning" text={`Estimate. Recipe coverage is ${percent(data.coverage)}; findings need ${percent(data.coverage_live)}.`} action={{ label: 'Open recipes', href: '#recipes' }} />
          <div className="card ml-pad">
            <CoverageBar value={Number(data.coverage)} needed={Number(data.coverage_live)} label={coverageWords} />
          </div>
        </>
      )}
      <TileRow>
        <StatTile value={dollars(tiles.unexplained.value)} label="Unexplained use" compare={`a week; ${dollars(tiles.unexplained.then)} the week before`} tone={Number(tiles.unexplained.value) > Number(tiles.unexplained.then) ? 'bad' : undefined} onWhy={() => why.show(tileWhy.unexplained())} />
        <StatTile value={percent(tiles.coverage.value)} label="Recipe coverage" compare={`${percent(tiles.coverage.then)} the week before; findings need ${percent(tiles.coverage.needed)}`} tone={Number(tiles.coverage.value) >= Number(tiles.coverage.needed) ? 'good' : 'bad'} onWhy={() => why.show(tileWhy.coverage())} />
        <StatTile value={String(flaggedCount)} label="Items flagged" compare={`${tiles.flagged.then} over the thresholds the week before`} onWhy={() => why.show(tileWhy.flagged())} />
      </TileRow>

      {top.length > 0 && (
        <ViewAsTable
          title={`${top[0].name} is the largest gap, ${dollars(top[0].weekly_dollars)} a week`}
          summary={`Unexplained use in dollars a week by ingredient for the ${period}. ${top.length} ${top.length === 1 ? 'ingredient is' : 'ingredients are'} shown.`}
          chart={<SortedBars bars={top.map((i) => ({ key: i.item_id, label: i.name, value: Number(i.weekly_dollars), source: `Source: invoices and recipes, ${period}` }))} unit="dollars a week" format={(v) => dollars(v)} onPick={(key) => (window.location.hash = `#usage/${key}`)} />}
          columns={['Ingredient', 'Dollars a week']}
          rows={top.map((i) => [i.name, dollars(i.weekly_dollars)])}
        />
      )}

      {data.items.length > 0 && (
        <section className="card" aria-label="Ingredients">
          <div className="table-wrap">
            <table className="table ml-table">
              <thead>
                <tr>
                  <th>Ingredient</th>
                  <th className="num ml-hide-phone">Expected use</th>
                  <th className="num ml-hide-phone">Purchased</th>
                  <th className="num ml-hide-phone">Unexplained use</th>
                  <th className="num">Dollars a week</th>
                  <th className="ml-hide-phone">Trend</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {limit.shown.map((item) => (
                  <tr key={item.item_id}>
                    <td>
                      <a className="strong" href={`#usage/${item.item_id}`}>
                        {item.name}
                      </a>
                      {item.finding?.type === 'usage_data_gap' && <StatusWord tone="bad">Data gap</StatusWord>}
                      {data.state === 'learning' && <div className="muted small">Estimate</div>}
                    </td>
                    <td className="num ml-hide-phone">{amount(item.expected, item.base_unit)}</td>
                    <td className="num ml-hide-phone">{amount(item.purchased, item.base_unit)}</td>
                    <td className="num ml-hide-phone">{Number(item.unexplained) > 0 ? `${amount(item.unexplained, item.base_unit)}${item.pct ? `, ${percent(item.pct)}` : ''}` : <span className="muted">None</span>}</td>
                    <td className="num">
                      <button className="ml-why-link ml-cell-link" onClick={() => why.open('usage_variance', item.row_id)}>
                        {dollars(Math.max(0, Number(item.weekly_dollars)))}
                      </button>
                    </td>
                    <td className="ml-hide-phone">
                      <Sparkline values={item.trend.map((v) => (v == null ? null : Math.max(0, Number(v))))} label={`${item.name}: unexplained dollars a week over 8 weeks`} />
                    </td>
                    <td>
                      {deferred.pendingId === item.item_id ? (
                        <button className="btn btn-secondary btn-sm" onClick={deferred.undo}>
                          Undo
                        </button>
                      ) : isDismissed(item) ? (
                        <StatusWord tone="neutral">Dismissed</StatusWord>
                      ) : (
                        <button className="btn btn-secondary btn-sm" onClick={() => setChecking(item.item_id)}>
                          See checks
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {limit.more && <div className="card-footer">{limit.more}</div>}
        </section>
      )}
      {data.more_under && (
        <p className="muted small" data-testid="collapsed-rows">
          {data.more_under.count} more {data.more_under.count === 1 ? 'item' : 'items'} under {dollars(data.more_under.threshold)} a week.
        </p>
      )}

      {check && (
        <SidePanel title={check.name} onClose={() => setChecking(null)}>
          <div className="ml-stack">
            <p className="strong">{check.finding?.type === 'usage_data_gap' ? 'Less was bought than the recipes used.' : `About ${dollars(Math.max(0, Number(check.weekly_dollars)))} a week is unexplained.`}</p>
            <p className="muted small">FullHouse cannot tell waste from over-portioning or theft. These three checks can.</p>
            <Checks checks={check.finding?.checks ?? null} unit={check.base_unit} gap={check.finding?.type === 'usage_data_gap'} />
            <div className="row">
              <a className="btn btn-primary" href={`#usage/${check.item_id}`}>
                Open ingredient
              </a>
              {check.finding && app.canManage && (
                <button className="btn btn-secondary" onClick={() => dismiss(check)}>
                  Dismiss
                </button>
              )}
              <button className="btn btn-ghost" onClick={() => why.open('usage_variance', check.row_id)}>
                Why
              </button>
            </div>
          </div>
        </SidePanel>
      )}
      {deferred.bar}
      {why.panel}
    </main>
  )
}

export function ingredientSentence(data: UsageDetail): string {
  const name = data.item.name
  if (!data.item.track_usage) return `${name} is left out of Usage. 0 windows are tracked.`
  if (!data.current) return `${name} has no usage figures yet. Recipes cover ${percent(data.coverage)} of sales.`
  const gap = Number(data.current.unexplained)
  if (gap <= 0) return `${name} use matches purchases within 1 delivery, last 28 days.`
  return `${name}: about ${dollars(data.current.weekly_dollars)} a week is unexplained, ${percent(data.current.pct ?? 0)} of expected use.`
}

function IngredientPage({ app, itemId }: { app: AppContext; itemId: string }) {
  const load = useLoad(() => fetchUsageItem(itemId), [itemId])
  const why = useWhy()
  const [busy, setBusy] = useState(false)
  const data = load.data
  if (load.status === 'loading') return <main className="page ml-page"><Skeleton title={TITLE} /></main>
  if (!data) return <main className="page ml-page"><LoadFailed title={TITLE} what="This ingredient" onRetry={() => void load.reload()} /></main>

  const unit = data.item.base_unit
  const current = data.current
  const before = data.windows.length > 1 ? data.windows[data.windows.length - 2] : undefined
  const updated = data.computed_at ? `${dayLabel(data.computed_at)} ${timeLabel(data.computed_at)}` : undefined
  const over = data.windows.filter((w) => Number(w.unexplained) > 0).length
  const track = async (on: boolean) => {
    setBusy(true)
    try {
      await setUsageTracking(itemId, on)
      await load.reload()
    } catch (err) {
      app.toast('Tracking was not changed', err instanceof Error ? err.message : 'Try again.')
    } finally {
      setBusy(false)
    }
  }
  return (
    <main className="page ml-page">
      <a className="ml-back" href="#usage">
        Usage
      </a>
      <HeaderSentence title={data.item.name} sentence={ingredientSentence(data)} updated={updated} state={data.state}>
        {app.canManage && (
          <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => void track(!data.item.track_usage)}>
            {data.item.track_usage ? 'Stop tracking' : 'Track usage'}
          </button>
        )}
      </HeaderSentence>
      {data.state === 'learning' && <StateBanner kind="learning" text={`Estimate. Recipe coverage is ${percent(data.coverage)}; findings need 90.0%.`} />}
      {current && (
        <>
          <TileRow>
            <StatTile value={amount(current.expected, unit)} label="Expected use" compare={before ? `${amount(before.expected, unit)} the week before` : 'last 28 days'} onWhy={() => why.open('usage_variance', current.row_id)} />
            <StatTile value={amount(current.purchased, unit)} label="Purchased" compare={`${current.deliveries} deliveries, last 28 days`} onWhy={() => why.open('usage_variance', current.row_id)} />
            <StatTile value={Number(current.unexplained) > 0 ? amount(current.unexplained, unit) : amount(0, unit)} label="Unexplained use" compare={`after 1 delivery of ${amount(current.noise, unit)}`} tone={Number(current.unexplained) > 0 ? 'bad' : 'good'} onWhy={() => why.open('usage_variance', current.row_id)} />
          </TileRow>
          <ViewAsTable
            title={over > 0 ? `Purchases ran past expected use and a delivery in ${over} of ${data.windows.length} windows` : `Purchases stayed within a delivery of expected use for ${data.windows.length} windows`}
            summary={`Expected use and purchased quantity of ${data.item.name} for ${data.windows.length} weekly 28-day windows, with one typical delivery as a band.`}
            chart={<GapLines points={data.windows.map((w) => ({ date: w.window_end, expected: Number(w.expected), purchased: Number(w.purchased), noise: Number(w.noise) }))} format={(v) => amount(v, unit)} unit={unit === 'each' ? 'units' : unit === 'g' ? 'weight' : 'volume'} />}
            columns={['28 days ending', 'Expected use', 'Purchased', 'Unexplained use', 'Dollars a week']}
            rows={data.windows.map((w) => [dayLabel(w.window_end), amount(w.expected, unit), amount(w.purchased, unit), Number(w.unexplained) > 0 ? amount(w.unexplained, unit) : 'None', dollars(Math.max(0, Number(w.weekly_dollars)))])}
          />
          {current.prices_left_out > 0 && (
            <p className="muted small">
              {current.prices_left_out} invoice {current.prices_left_out === 1 ? 'line was' : 'lines were'} left out for a price far from the usual. <a href="#invoices">Open invoices</a>
            </p>
          )}
          {data.dishes.length > 0 && (
            <section className="card" data-testid="dishes-using" aria-label="Dishes that use it">
              <div className="card-header">
                <h2 className="card-title">
                  {data.dishes[0].name} uses the most, {amount(data.dishes[0].expected, unit)} in 28 days
                </h2>
              </div>
              <div className="card-body">
                <SortedBars bars={data.dishes.map((d) => ({ key: d.recipe_id, label: d.name, value: Number(d.expected), note: `${Number(d.sold).toLocaleString('en-US')} sold`, source: 'Source: the recipe and point-of-sale sales' }))} unit={`expected use, ${unit === 'each' ? 'units' : unit}, last 28 days`} format={(v) => amount(v, unit)} onPick={(key) => (window.location.hash = `#menu-costs/${key}`)} />
              </div>
            </section>
          )}
          <section className="card ml-pad" aria-label="Checks">
            <h2 className="card-title">Three checks</h2>
            <Checks checks={data.checks} unit={unit} gap={data.finding?.type === 'usage_data_gap'} />
          </section>
        </>
      )}
      {why.panel}
    </main>
  )
}
