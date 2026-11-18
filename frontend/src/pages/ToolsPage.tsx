import { useEffect, useMemo, useState } from 'react'
import { listMenuPerformance, type MenuPerformance } from '../api/client'
import { fetchStockOutlook, fetchSwitchOptions, type OutlookItem, type StockOutlook, type SwitchOption } from '../api/insights'
import type { AppContext } from '../app/context'
import { RunwayChart, type RunwayDay } from '../components/InsightCharts'
import { Loaded } from '../components/PageState'
import { money, pct, qty, shortDate } from '../lib/format'
import { readHashParam } from '../lib/useHashRoute'
import { useLoad } from '../lib/useLoad'

type Tool = 'runway' | 'switch' | 'menu'
const TOOLS: { key: Tool; label: string }[] = [
  { key: 'runway', label: 'When do I run out?' },
  { key: 'switch', label: 'Switch supplier' },
  { key: 'menu', label: 'Menu price' },
]

function Slider({ label, value, min, max, step, onChange, show }: { label: string; value: number; min: number; max: number; step: number; onChange: (v: number) => void; show: string }) {
  const id = `slider-${label.replace(/\W+/g, '-').toLowerCase()}`
  return (
    <div className="field slider-field">
      <label className="field-label" htmlFor={id}>
        {label} <span className="slider-value">{show}</span>
      </label>
      <input id={id} type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} />
    </div>
  )
}

function Result({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: 'good' | 'bad' }) {
  return (
    <div className="tool-result">
      <div className="stat-label">{label}</div>
      <div className={`tool-result-value${tone ? ` ${tone}` : ''}`}>{value}</div>
      {hint && <div className="muted small">{hint}</div>}
    </div>
  )
}

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const HORIZON = 14
const days = (n: number) => `${qty(n)} ${n === 1 ? 'day' : 'days'}`

/** Stock a day at a time: today's level, less each day's use (busier days use more), plus what lands. */
export function project(item: OutlookItem, outlook: StockOutlook, extra: { day: number; quantity: number } | null): RunwayDay[] {
  const start = new Date(`${outlook.today}T12:00:00`)
  const days: RunwayDay[] = []
  let level = item.on_hand
  for (let i = 0; i <= HORIZON; i++) {
    const date = new Date(start)
    date.setDate(start.getDate() + i)
    const key = iso(date)
    let delivered = item.incoming.filter((o) => o.expected_on === key).reduce((sum, o) => sum + o.quantity, 0)
    if (extra && extra.day === i) delivered += extra.quantity
    if (i > 0) level -= item.daily_use * (outlook.weekday_factors[(date.getDay() + 6) % 7] ?? 1)
    level = Math.max(0, level) + delivered
    days.push({ date: key, level, delivered })
  }
  return days
}

function RunwayTool({ outlook }: { outlook: StockOutlook }) {
  const usable = useMemo(() => [...outlook.items].sort((a, b) => (a.days_left ?? 1e9) - (b.days_left ?? 1e9)), [outlook])
  const [itemId, setItemId] = useState(() => readHashParam() || usable[0]?.id || '')
  const item = usable.find((i) => i.id === itemId) ?? usable[0]
  const [cover, setCover] = useState(4)
  const [safety, setSafety] = useState(outlook.safety_days)
  const [order, setOrder] = useState(true)
  useEffect(() => {
    if (item) setCover(Math.max(2, item.lead_time_days + 2))
  }, [item?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!item) return <div className="empty">Add stock items first, then come back.</div>

  const onOrder = item.incoming.reduce((sum, o) => sum + o.quantity, 0)
  const par = item.daily_use * (cover + safety)
  const reorderAt = item.daily_use * (item.lead_time_days + safety)
  const short = Math.max(0, par - item.on_hand - onOrder)
  const pack = item.pack_size && item.pack_size > 0 ? item.pack_size : null
  const orderQty = pack ? Math.ceil(short / pack) * pack : Math.ceil(short)
  const baseline = project(item, outlook, null)
  const runway = project(item, outlook, order && orderQty > 0 ? { day: Math.min(HORIZON, item.lead_time_days), quantity: orderQty } : null)
  const outBase = baseline.findIndex((d, i) => i > 0 && d.level <= 0)
  const outNow = runway.findIndex((d, i) => i > 0 && d.level <= 0)

  return (
    <div className="tool">
      <div className="tool-controls">
        <div className="field">
          <label className="field-label" htmlFor="runway-item">
            Item
          </label>
          <select id="runway-item" className="select" value={item.id} onChange={(e) => setItemId(e.target.value)}>
            {usable.map((i) => (
              <option key={i.id} value={i.id}>
                {i.name}
                {i.days_left === null ? ' (no recipe uses it)' : ` · ${days(i.days_left)} left`}
              </option>
            ))}
          </select>
        </div>
        <Slider label="Days of cover to hold" value={cover} min={1} max={14} step={1} onChange={setCover} show={days(cover)} />
        <Slider label="Safety stock" value={safety} min={0} max={5} step={0.5} onChange={setSafety} show={days(safety)} />
        <label className="checkbox-row">
          <input type="checkbox" checked={order} onChange={(e) => setOrder(e.target.checked)} /> Order up to par today
        </label>
        <p className="muted small">
          {item.name} uses about {qty(item.daily_use)} {item.unit} a day, from what you sold and your recipes.{' '}
          {item.supplier_name ? `${item.supplier_name} delivers in ${item.lead_time_days} ${item.lead_time_days === 1 ? 'day' : 'days'}.` : 'It has no supplier yet.'} Nothing here
          changes your stock or places an order.
        </p>
      </div>
      <div className="tool-output">
        {item.daily_use > 0 ? (
          <>
            <RunwayChart days={runway} unit={item.unit} reorderAt={reorderAt} par={par} />
            <div className="tool-results">
              <Result label="Par to hold" value={`${qty(par)} ${item.unit}`} hint={`${money(par * item.unit_cost)} on the shelf`} />
              <Result
                label="Order now"
                value={orderQty > 0 ? `${qty(orderQty)} ${item.unit}` : 'Nothing'}
                hint={orderQty > 0 ? `${pack ? `${qty(orderQty / pack)} ${item.pack_unit ?? 'pack'}${orderQty / pack === 1 ? '' : 's'}, ` : ''}about ${money(orderQty * item.unit_cost)}` : onOrder > 0 ? `${qty(onOrder)} ${item.unit} already on the way` : 'You are at par'}
              />
              <Result
                label={order && orderQty > 0 ? 'Runs out, with this one order' : 'Runs out'}
                value={outNow > 0 ? shortDate(runway[outNow].date) : `Not in ${HORIZON} days`}
                hint={outBase > 0 && outNow !== outBase ? `${shortDate(baseline[outBase].date)} without the order` : undefined}
                tone={outNow > 0 ? 'bad' : 'good'}
              />
              <Result label="Reorder when below" value={`${qty(reorderAt)} ${item.unit}`} hint={`it is set to ${qty(item.reorder_threshold)} now`} />
            </div>
          </>
        ) : (
          <div className="empty">
            <div className="empty-title">No usage to project</div>
            No dish with a recipe uses {item.name}, so there is no daily usage to draw. <a href="#menu">Add it to a recipe</a>
          </div>
        )}
      </div>
    </div>
  )
}

function SwitchTool({ options }: { options: SwitchOption[] }) {
  const [itemId, setItemId] = useState(options[0]?.item_id ?? '')
  const option = options.find((o) => o.item_id === itemId) ?? options[0]
  const [supplierId, setSupplierId] = useState('')
  const [packs, setPacks] = useState(0)
  useEffect(() => {
    if (!option) return
    setSupplierId(option.suppliers[0].supplier_id)
    setPacks(Math.max(1, Math.round(option.weekly_packs)))
  }, [option?.item_id]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!option) {
    return (
      <div className="empty">
        <div className="empty-title">Nothing to compare yet</div>
        This fills in once two suppliers have invoiced you for the same item.
      </div>
    )
  }
  const current = option.suppliers.find((s) => s.supplier_id === option.current_supplier_id) ?? option.suppliers[option.suppliers.length - 1]
  const chosen = option.suppliers.find((s) => s.supplier_id === supplierId) ?? option.suppliers[0]
  const yearly = (current.per_pack - chosen.per_pack) * packs * 52
  const everything = options.reduce((sum, o) => {
    const now = o.suppliers.find((s) => s.supplier_id === o.current_supplier_id) ?? o.suppliers[o.suppliers.length - 1]
    return sum + Math.max(0, now.per_pack - o.suppliers[0].per_pack) * o.weekly_packs * 52
  }, 0)

  return (
    <div className="tool">
      <div className="tool-controls">
        <div className="field">
          <label className="field-label" htmlFor="switch-item">
            Item
          </label>
          <select id="switch-item" className="select" value={option.item_id} onChange={(e) => setItemId(e.target.value)}>
            {options.map((o) => (
              <option key={o.item_id} value={o.item_id}>
                {o.item_name}
              </option>
            ))}
          </select>
        </div>
        <fieldset className="field">
          <legend className="field-label">Buy it from</legend>
          {option.suppliers.map((s) => (
            <label key={s.supplier_id} className="radio-row">
              <input type="radio" name="switch-supplier" checked={chosen.supplier_id === s.supplier_id} onChange={() => setSupplierId(s.supplier_id)} />
              <span>
                <span className="strong">{s.name}</span> · {money(s.per_pack, true)} a pack
                <span className="muted small">
                  {' '}
                  on {shortDate(s.last_date)}
                  {s.supplier_id === current.supplier_id ? ' · where you buy it now' : ''}
                </span>
              </span>
            </label>
          ))}
        </fieldset>
        <Slider label="Packs a week" value={packs} min={0} max={Math.max(10, Math.ceil(option.weekly_packs * 3))} step={1} onChange={setPacks} show={`${packs}`} />
        <p className="muted small">
          You have been buying about {qty(option.weekly_packs)} packs a week. Prices are the last each supplier invoiced, not quotes. <a href={`#item/${option.item_id}`}>See eight weeks of prices</a>
        </p>
      </div>
      <div className="tool-output">
        <div className="tool-results">
          <Result
            label={chosen.supplier_id === current.supplier_id ? 'No change' : `Moving to ${chosen.name}`}
            value={`${yearly >= 0 ? '' : '−'}${money(Math.abs(yearly))} a year`}
            hint={yearly > 0 ? 'saved at this volume' : yearly < 0 ? 'more than you pay now' : 'the same as now'}
            tone={yearly > 0 ? 'good' : yearly < 0 ? 'bad' : undefined}
          />
          <Result label="A week" value={money(Math.abs(yearly) / 52, true)} hint={`${money(Math.abs(current.per_pack - chosen.per_pack), true)} a pack × ${packs}`} />
          <Result label="Every item at its cheapest supplier" value={`${money(everything)} a year`} hint={`across ${options.length} ${options.length === 1 ? 'item' : 'items'} two suppliers sell, at what you buy now`} tone="good" />
        </div>
        <div className="table-wrap">
          <table className="table table-cards">
            <thead>
              <tr>
                <th>Item</th>
                <th>Cheapest</th>
                <th className="num">Now</th>
                <th className="num">Cheapest</th>
                <th className="num">A year</th>
              </tr>
            </thead>
            <tbody>
              {options.map((o) => {
                const now = o.suppliers.find((s) => s.supplier_id === o.current_supplier_id) ?? o.suppliers[o.suppliers.length - 1]
                const best = o.suppliers[0]
                return (
                  <tr key={o.item_id}>
                    <td data-label="Item">
                      <button className="link-button" onClick={() => setItemId(o.item_id)}>
                        {o.item_name}
                      </button>
                    </td>
                    <td data-label="Cheapest">{best.name}</td>
                    <td className="num" data-label="Now">{money(now.per_pack, true)}</td>
                    <td className="num" data-label="Cheapest">{money(best.per_pack, true)}</td>
                    <td className="num strong" data-label="A year">{money(Math.max(0, now.per_pack - best.per_pack) * o.weekly_packs * 52)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function MenuTool({ dishes }: { dishes: MenuPerformance[] }) {
  const sorted = useMemo(() => [...dishes].sort((a, b) => b.units_sold - a.units_sold), [dishes])
  const [dishId, setDishId] = useState(sorted[0]?.id ?? '')
  const dish = sorted.find((d) => d.id === dishId) ?? sorted[0]
  const [price, setPrice] = useState(0)
  const [costChange, setCostChange] = useState(0)
  const [volumeChange, setVolumeChange] = useState(0)
  useEffect(() => {
    if (!dish) return
    setPrice(dish.price)
    setCostChange(0)
    setVolumeChange(0)
  }, [dish?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!dish) return <div className="empty">Add a dish to the menu first.</div>
  const cost = dish.cost * (1 + costChange / 100)
  const perPlate = price - cost
  const margin = price > 0 ? (perPlate / price) * 100 : 0
  const plates = dish.units_sold * (1 + volumeChange / 100)
  const profitNow = (dish.price - dish.cost) * dish.units_sold
  const profitThen = perPlate * plates
  const difference = profitThen - profitNow
  const untouched = price === dish.price && costChange === 0 && volumeChange === 0

  return (
    <div className="tool">
      <div className="tool-controls">
        <div className="field">
          <label className="field-label" htmlFor="menu-dish">
            Dish
          </label>
          <select id="menu-dish" className="select" value={dish.id} onChange={(e) => setDishId(e.target.value)}>
            {sorted.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name} · {money(d.price, true)}
              </option>
            ))}
          </select>
        </div>
        <Slider label="Menu price" value={price} min={Math.max(0.5, Math.round(dish.price * 0.7 * 4) / 4)} max={Math.round(dish.price * 1.4 * 4) / 4} step={0.25} onChange={setPrice} show={money(price, true)} />
        <Slider label="Ingredient cost" value={costChange} min={-20} max={40} step={1} onChange={setCostChange} show={`${costChange >= 0 ? '+' : '−'}${Math.abs(costChange)}%`} />
        <Slider label="Plates sold" value={volumeChange} min={-30} max={20} step={1} onChange={setVolumeChange} show={`${volumeChange >= 0 ? '+' : '−'}${Math.abs(volumeChange)}%`} />
        <p className="muted small">
          {dish.name} costs {money(dish.cost, true)} a plate {dish.cost_source === 'recipe' ? 'from its recipe' : 'as typed in'}, and {dish.units_sold.toLocaleString()} have sold on record. Nothing here
          changes the menu. <a href="#menu">Open the menu</a>
        </p>
      </div>
      <div className="tool-output">
        <div className="tool-results">
          <Result label="Margin" value={pct(margin)} hint={`${pct(dish.margin_pct)} now`} tone={untouched ? undefined : margin >= dish.margin_pct ? 'good' : 'bad'} />
          <Result label="Profit a plate" value={money(perPlate, true)} hint={`${money(dish.price - dish.cost, true)} now`} />
          <Result
            label={`Over ${Math.round(plates).toLocaleString()} plates`}
            value={untouched ? money(profitNow) : `${difference >= 0 ? '+' : '−'}${money(Math.abs(difference))}`}
            hint={untouched ? 'gross profit as it stands' : `${money(profitThen)} against ${money(profitNow)} now`}
            tone={untouched ? undefined : difference >= 0 ? 'good' : 'bad'}
          />
        </div>
        <p className="muted small">
          Move the price and the plates together to test a rise: a {money(dish.price * 0.05, true)} rise that loses fewer than{' '}
          {dish.price - dish.cost > 0 ? pct(((dish.price * 0.05) / (dish.price * 1.05 - dish.cost)) * 100, 0) : '0%'} of plates still earns more.
        </p>
      </div>
    </div>
  )
}

/** Three calculators that read your own numbers and change nothing: each answers "what if" before you decide. */
export function ToolsPage({ app }: { app: AppContext }) {
  const [tool, setTool] = useState<Tool>('runway')
  const outlook = useLoad(fetchStockOutlook, [])
  const options = useLoad(() => (app.canManage ? fetchSwitchOptions() : Promise.resolve([])), [app.canManage])
  const dishes = useLoad(() => (app.canManage ? listMenuPerformance() : Promise.resolve([])), [app.canManage])
  const tools = app.canManage ? TOOLS : TOOLS.filter((t) => t.key === 'runway')

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Test a decision before you make it</h1>
          <p className="page-subtitle">See what a par, a supplier switch or a menu price would do, on your own numbers. Nothing here changes anything.</p>
        </div>
        <div className="segmented" role="group" aria-label="Tool">
          {tools.map((t) => (
            <button key={t.key} aria-pressed={tool === t.key} onClick={() => setTool(t.key)}>
              {t.label}
            </button>
          ))}
        </div>
      </div>
      <section className="card">
        <div className="card-body">
          {tool === 'runway' && (
            <Loaded load={outlook} what="stock" placeholderHeight={260}>
              {(data) => <RunwayTool outlook={data} />}
            </Loaded>
          )}
          {tool === 'switch' && (
            <Loaded load={options} what="supplier prices" placeholderHeight={260}>
              {(data) => <SwitchTool options={data} />}
            </Loaded>
          )}
          {tool === 'menu' && (
            <Loaded load={dishes} what="the menu" placeholderHeight={260}>
              {(data) => <MenuTool dishes={data} />}
            </Loaded>
          )}
        </div>
      </section>
    </main>
  )
}
