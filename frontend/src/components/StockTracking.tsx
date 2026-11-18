import { useState } from 'react'
import { listMenuPerformance } from '../api/client'
import { mapPosItem, runStockNow, setConversion, setSalesSource, type StockStatus, unmapPosItem } from '../api/stock'
import type { AppContext } from '../app/context'
import { money, qty, relativeTime, shortDate } from '../lib/format'
import { useLoad } from '../lib/useLoad'

/** One POS item that took nothing off the shelf: say it is a dish, say what it uses, or say it isn't food. */
function Unmapped({ app, row, dishes, onDone }: { app: AppContext; row: StockStatus['unmapped'][number]; dishes: { id: string; name: string }[]; onDone: () => void }) {
  const [choice, setChoice] = useState('')
  const [itemId, setItemId] = useState('')
  const [amount, setAmount] = useState('')
  const [busy, setBusy] = useState(false)
  const item = app.inventory.find((i) => i.id === itemId)
  const save = async (body: Parameters<typeof mapPosItem>[0]) => {
    setBusy(true)
    try {
      await mapPosItem(body)
      app.toast(`${row.pos_item_name} is set`, 'Its sales come off the shelf from the next run.')
      onDone()
    } catch (err) {
      app.toast('Could not save that', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <li className="list-item tracking-row">
      <div className="list-item-main">
        <div className="strong">{row.pos_item_name}</div>
        <div className="muted small">
          {qty(row.qty)} sold, {money(row.net)} · {row.why}
        </div>
      </div>
      <div className="tracking-controls">
        <select
          className="select input-sm"
          aria-label={`What ${row.pos_item_name} is`}
          value={choice}
          disabled={busy}
          onChange={(e) => {
            const value = e.target.value
            setChoice(value)
            if (value === 'ignore') void save({ pos_item_name: row.pos_item_name, ignore: true })
            else if (value.startsWith('dish:')) void save({ pos_item_name: row.pos_item_name, menu_item_id: value.slice(5) })
          }}
        >
          <option value="">What is it?</option>
          <option value="uses">It uses an ingredient…</option>
          <option value="ignore">Not food: ignore it</option>
          <optgroup label="Another name for a dish">
            {dishes.map((d) => (
              <option key={d.id} value={`dish:${d.id}`}>
                {d.name}
              </option>
            ))}
          </optgroup>
        </select>
        {choice === 'uses' && (
          <form
            className="row"
            onSubmit={(e) => {
              e.preventDefault()
              if (itemId && Number(amount) > 0) void save({ pos_item_name: row.pos_item_name, lines: [{ inventory_item_id: itemId, quantity: Number(amount) }] })
            }}
          >
            <input className="input input-sm par-input" type="number" min="0" step="any" placeholder="0.25" aria-label="How much one uses" value={amount} onChange={(e) => setAmount(e.target.value)} />
            <span className="muted small">{item?.unit ?? ''} of</span>
            <select className="select input-sm" aria-label="Ingredient" value={itemId} onChange={(e) => setItemId(e.target.value)}>
              <option value="">ingredient</option>
              {[...app.inventory]
                .sort((a, b) => a.name.localeCompare(b.name))
                .map((i) => (
                  <option key={i.id} value={i.id}>
                    {i.name}
                  </option>
                ))}
            </select>
            <button className="btn btn-secondary btn-sm" disabled={busy || !itemId || !(Number(amount) > 0)}>
              Save
            </button>
          </form>
        )}
      </div>
    </li>
  )
}

/** An item the invoices price in one unit and the kitchen counts in another: one number joins them. */
function Conversion({ app, row, onDone }: { app: AppContext; row: StockStatus['needs_conversion'][number]; onDone: () => void }) {
  const [factor, setFactor] = useState('')
  const [busy, setBusy] = useState(false)
  return (
    <li className="list-item tracking-row">
      <div className="list-item-main">
        <div className="strong">{row.name}</div>
        <div className="muted small">
          Counted in {row.unit}, invoiced as {row.invoiced_as}. Until this is filled in, its deliveries can’t be booked from the invoice.
        </div>
      </div>
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault()
          setBusy(true)
          setConversion(row.inventory_item_id, row.other_unit, Number(factor))
            .then(() => {
              app.toast(`Saved: 1 ${row.other_unit} of ${row.name} is ${factor} ${row.unit}`)
              onDone()
            })
            .catch((err: unknown) => app.toast('Could not save that', err instanceof Error ? err.message : String(err)))
            .finally(() => setBusy(false))
        }}
      >
        <span className="muted small">1 {row.other_unit} =</span>
        <input className="input input-sm par-input" type="number" min="0" step="any" aria-label={`How many ${row.unit} in one ${row.other_unit} of ${row.name}`} value={factor} onChange={(e) => setFactor(e.target.value)} />
        <span className="muted small">{row.unit}</span>
        <button className="btn btn-secondary btn-sm" disabled={busy || !(Number(factor) > 0)}>
          Save
        </button>
      </form>
    </li>
  )
}

/**
 * How stock is tracked, and the three things that keep it honest: one
 * source of sales (so nothing comes off twice), a conversion where an
 * invoice's unit isn't the kitchen's, and a meaning for every POS item
 * that isn't a dish.
 */
export function StockTracking({ app, status, onChanged }: { app: AppContext; status: StockStatus; onChanged: () => void }) {
  const dishes = useLoad(() => (app.canManage ? listMenuPerformance() : Promise.resolve([])), [app.canManage])
  const [busy, setBusy] = useState(false)
  const act = async (work: () => Promise<unknown>, done: string, detail?: string) => {
    setBusy(true)
    try {
      await work()
      app.toast(done, detail)
      app.refresh()
      onChanged()
    } catch (err) {
      app.toast('That didn’t work', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }
  const fromPos = status.sales_source === 'pos'
  return (
    <section className="card" aria-labelledby="tracking-title">
      <div className="card-header">
        <div>
          <h2 className="card-title" id="tracking-title">
            How stock is tracked
          </h2>
          <div className="card-subtitle">
            {fromPos
              ? status.tracking_since
                ? `Point-of-sale sales have come off the shelf through your recipes since ${shortDate(status.tracking_since)}.` +
                  (status.last_depleted_at ? ` Last taken off ${relativeTime(status.last_depleted_at)}.` : status.last_sales_day ? ` Sales are in through ${shortDate(status.last_sales_day)}.` : '')
                : 'Point-of-sale sales come off the shelf through your recipes, starting with the next sync.'
              : 'Orders taken in FullHouse take their ingredients off the shelf as they are placed.'}{' '}
            A sale only ever comes off from one of the two, so nothing is counted twice.
          </div>
        </div>
        {app.canManage && (
          <div className="row wrap">
            <div className="segmented" role="group" aria-label="Where sales come off from">
              <button aria-pressed={fromPos} disabled={busy} onClick={() => void act(() => setSalesSource('pos'), 'Sales come off from the point of sale', 'Orders taken in FullHouse are recorded but no longer deduct stock.')}>
                Point of sale
              </button>
              <button aria-pressed={!fromPos} disabled={busy} onClick={() => void act(() => setSalesSource('app'), 'Sales come off from orders taken here', 'Point-of-sale lines are left alone.')}>
                Orders taken here
              </button>
            </div>
            {fromPos && (
              <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => void act(async () => runStockNow(), 'Checked for new sales')}>
                Take sales off now
              </button>
            )}
          </div>
        )}
      </div>

      {app.canManage && status.needs_conversion.length > 0 && (
        <div className="card-body tracking-block">
          <div className="strong">Units that need joining up</div>
          <ul className="list">
            {status.needs_conversion.map((row) => (
              <Conversion key={row.inventory_item_id} app={app} row={row} onDone={onChanged} />
            ))}
          </ul>
        </div>
      )}
      {app.canManage && status.conversions.length > 0 && (
        <div className="card-body tracking-block">
          <div className="muted small">
            {status.conversions.map((c) => (
              <span key={`${c.inventory_item_id}-${c.other_unit}`} className="tracking-chip">
                1 {c.other_unit} of {c.name} = {qty(c.factor)} {c.unit}
                <button className="link-button" aria-label={`Forget the ${c.name} conversion`} onClick={() => void act(() => setConversion(c.inventory_item_id, c.other_unit, null), 'Conversion removed')}>
                  remove
                </button>
              </span>
            ))}
          </div>
        </div>
      )}

      {fromPos && status.unmapped.length > 0 && (
        <div className="card-body tracking-block">
          <div className="strong">Sold, but took nothing off the shelf</div>
          <p className="muted small" style={{ margin: '2px 0 0' }}>
            {app.canManage ? 'Say what each one is: another name for a dish, an add-on that uses an ingredient, or not food.' : 'A manager can say what each one is.'}
          </p>
          <ul className="list">
            {status.unmapped.map((row) =>
              app.canManage ? (
                <Unmapped key={row.pos_item_name} app={app} row={row} dishes={(dishes.data ?? []).map((d) => ({ id: d.id, name: d.name }))} onDone={onChanged} />
              ) : (
                <li key={row.pos_item_name} className="list-item">
                  <div className="list-item-main">
                    <div className="strong">{row.pos_item_name}</div>
                    <div className="muted small">
                      {qty(row.qty)} sold · {row.why}
                    </div>
                  </div>
                </li>
              ),
            )}
          </ul>
        </div>
      )}
      {app.canManage && status.pos_maps.length > 0 && (
        <div className="card-body tracking-block">
          <div className="muted small">
            {status.pos_maps.map((m) => (
              <span key={m.pos_item_name} className="tracking-chip">
                {m.pos_item_name}: {m.ignore ? 'ignored' : m.menu_item_id ? 'a dish' : 'uses an ingredient'}
                <button className="link-button" aria-label={`Forget what ${m.pos_item_name} is`} onClick={() => void act(() => unmapPosItem(m.pos_item_name), 'Removed')}>
                  remove
                </button>
              </span>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}
