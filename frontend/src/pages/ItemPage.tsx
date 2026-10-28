import { type FormEvent, useState } from 'react'
import { type PriceItemDetail, type PriceSeries, createDraft, movePriceItem, setInventoryLink, setPackSize, getPriceItem } from '../api/client'
import type { AppContext } from '../app/context'
import { Icon } from '../components/Icon'
import { Loaded } from '../components/PageState'
import { askAboutItem, sectionClass, usd } from '../components/prices'
import { qty, shortDate } from '../lib/format'
import { useHashParam } from '../lib/useHashRoute'
import { useLoad } from '../lib/useLoad'

const WIDTH = 230
const HEIGHT = 88

/**
 * Eight weeks, one line per supplier, no axes or gridlines, a dot on the
 * latest point only. The current supplier is solid; the others at 60%.
 */
function Sparkline({ series }: { series: PriceSeries[] }) {
  const prices = series.flatMap((s) => s.points.map((p) => p.price)).filter((p): p is number => p !== null)
  if (prices.length === 0) return null
  const min = Math.min(...prices)
  const max = Math.max(...prices)
  const span = max - min || 1
  const n = Math.max(...series.map((s) => s.points.length), 2)
  const x = (i: number) => 8 + (i * (WIDTH - 16)) / (n - 1)
  const y = (p: number) => 10 + ((max - p) * (HEIGHT - 20)) / span

  const described = series
    .map((s) => {
      const known = s.points.filter((p) => p.price !== null)
      return known.length ? `${s.supplier.name} from ${usd(known[0].price!)} to ${usd(known[known.length - 1].price!)}` : null
    })
    .filter(Boolean)
    .join('. ')

  return (
    <div className="pi-sparkline">
      <svg role="img" aria-label={`Per-pack price over eight weeks. ${described}.`} width={WIDTH} height={HEIGHT} viewBox={`0 0 ${WIDTH} ${HEIGHT}`}>
        {series.map((s) => {
          const pts = s.points.map((p, i) => (p.price === null ? null : [x(i), y(p.price)] as const)).filter((p): p is readonly [number, number] => p !== null)
          if (pts.length === 0) return null
          const last = pts[pts.length - 1]
          return (
            <g key={s.supplier.id} opacity={s.current ? 1 : 0.6}>
              <polyline points={pts.map(([px, py]) => `${px},${py}`).join(' ')} fill="none" stroke="#111" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
              <circle cx={last[0]} cy={last[1]} r={4} fill="#111" />
            </g>
          )
        })}
      </svg>
      <div className="pi-sparkline-labels">
        {series.map((s) => {
          const known = s.points.filter((p) => p.price !== null)
          const last = known[known.length - 1]
          return (
            <div key={s.supplier.id}>
              <span style={s.current ? { fontWeight: 600 } : undefined}>{s.supplier.name}</span>
              <br />
              {last ? <span className="pi-money">{usd(last.price!)}</span> : <span>no invoice</span>}
              {s.current && ', current'}
            </div>
          )
        })}
      </div>
    </div>
  )
}

/** "Reading this as 24 per case. Wrong?" with the correction inline. */
function PackSentence({ item, onSaved, onError }: { item: PriceItemDetail; onSaved: () => void; onError: (m: string) => void }) {
  const [editing, setEditing] = useState(false)
  const [size, setSize] = useState(String(item.pack_size))
  const [unit, setUnit] = useState(item.pack_unit)
  const [busy, setBusy] = useState(false)

  const save = async (event: FormEvent) => {
    event.preventDefault()
    const n = Number(size)
    if (!Number.isFinite(n) || n <= 0) return onError('Enter how many come in one pack, as a number.')
    setBusy(true)
    try {
      await setPackSize(item.id, n, unit.trim() || null)
      setEditing(false)
      onSaved()
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  if (!editing)
    return (
      <p className="pi-secondary">
        {item.pack_sentence}{' '}
        <button type="button" className="pi-link pi-link-inline" onClick={() => setEditing(true)}>
          Wrong?
        </button>
      </p>
    )
  return (
    <form className="pi-answers" onSubmit={save} aria-label="Correct the pack size" style={{ alignItems: 'center' }}>
      <label className="visually-hidden" htmlFor="pack-size">
        How many per pack
      </label>
      <input id="pack-size" className="pi-input pi-input-sm" type="number" inputMode="decimal" min={0.01} step="any" value={size} onChange={(e) => setSize(e.target.value)} style={{ width: 96 }} />
      <span className="pi-secondary">{item.unit} per</span>
      <label className="visually-hidden" htmlFor="pack-unit">
        Pack
      </label>
      <input id="pack-unit" className="pi-input pi-input-sm" type="text" value={unit} onChange={(e) => setUnit(e.target.value)} style={{ width: 96 }} />
      <button type="submit" className="pi-btn" disabled={busy}>
        Save
      </button>
      <button type="button" className="pi-link" onClick={() => setEditing(false)}>
        Cancel
      </button>
    </form>
  )
}

/**
 * "Counted in Inventory as Tomatoes: 6 lb on hand." The link between what the
 * invoices call it and what the kitchen counts, with the choice inline.
 */
function StockLine({ item, app, onSaved }: { item: PriceItemDetail; app: AppContext; onSaved: () => void }) {
  const [editing, setEditing] = useState(false)
  const [choice, setChoice] = useState(item.stock?.inventory_item_id ?? '')
  const [busy, setBusy] = useState(false)
  const s = item.stock

  const save = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    try {
      await setInventoryLink(item.id, choice || null)
      setEditing(false)
      onSaved()
      app.refresh()
    } catch (err) {
      app.toast('Couldn’t save', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  if (!editing) {
    const sentence = s
      ? `Counted in Inventory as ${s.name}: ${qty(s.quantity_on_hand)} ${s.unit} on hand${
          s.low_stock ? `, at or below the reorder point of ${qty(s.reorder_threshold)} ${s.unit}` : ''
        }.`
      : 'Not counted in Inventory.'
    return (
      <p className="pi-secondary">
        {sentence}{' '}
        {s && !s.converts && `Counted in ${s.unit} and priced in ${item.unit}, so deliveries aren’t booked. `}
        {s?.on_order && (
          <>
            <a className="pi-link pi-link-inline" href="#inventory">
              On order: {qty(s.on_order.quantity)} {s.on_order.unit}
              {s.on_order.supplier_name && ` from ${s.on_order.supplier_name}`}
              {s.on_order.expected_on && `, ${s.on_order.overdue ? 'was due' : 'due'} ${shortDate(s.on_order.expected_on)}`}
            </a>{' '}
          </>
        )}
        {s?.pending_action_id && (
          <>
            <a className="pi-link pi-link-inline" href="#approvals">
              Reorder proposed
            </a>{' '}
          </>
        )}
        {app.canManage && (
          <button type="button" className="pi-link pi-link-inline" onClick={() => setEditing(true)}>
            {s ? 'Change' : 'Count it'}
          </button>
        )}
      </p>
    )
  }
  return (
    <form className="pi-answers" onSubmit={save} aria-label="Count this item as" style={{ alignItems: 'center' }}>
      <label className="visually-hidden" htmlFor="stock-link">
        Counted in Inventory as
      </label>
      <select id="stock-link" className="pi-input pi-input-sm" value={choice} onChange={(e) => setChoice(e.target.value)}>
        <option value="">Not counted in Inventory</option>
        {app.inventory.map((i) => (
          <option key={i.id} value={i.id}>
            {i.name} ({i.unit})
          </option>
        ))}
      </select>
      <button type="submit" className="pi-btn" disabled={busy}>
        Save
      </button>
      <button type="button" className="pi-link" onClick={() => setEditing(false)}>
        Cancel
      </button>
    </form>
  )
}

/** One item: its recommendation, eight weeks of prices, and every invoice line behind them. */
export function ItemPage({ app }: { app: AppContext }) {
  const id = useHashParam()
  const item = useLoad(() => getPriceItem(id), [id])
  const [busy, setBusy] = useState(false)

  const draft = async (d: PriceItemDetail) => {
    setBusy(true)
    try {
      const created = await createDraft(d.id, d.card?.action.draft_kind ?? null, d.card?.action.supplier_id ?? d.last_supplier?.id ?? null)
      window.location.hash = `#draft/${created.id}`
    } catch (err) {
      app.toast('Couldn’t draft that', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const move = async (d: PriceItemDetail) => {
    if (!d.card?.action.supplier_id) return
    setBusy(true)
    try {
      await movePriceItem(d.id, d.card.action.supplier_id)
      app.toast(`${d.name}: moving`, 'The next digest checks the invoice.')
      void item.reload()
      app.refresh()
    } catch (err) {
      app.toast('Couldn’t record that', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="pi">
      <div className="pi-page">
        <a className="pi-back" href="#digest">
          <span style={{ display: 'inline-flex', transform: 'rotate(180deg)' }}>
            <Icon name="arrowRight" size={16} />
          </span>
          Digest
        </a>
        <Loaded load={item} what="the item" placeholderHeight={200}>
          {(d) => (
            <>
              <header className="pi-stack-sm">
                <h1 className="pi-h1">{d.name}</h1>
                <PackSentence item={d} onSaved={() => void item.reload()} onError={(m) => app.toast('Couldn’t save', m)} />
                <StockLine item={d} app={app} onSaved={() => void item.reload()} />
              </header>

              <section className="pi-stack">
                {d.card ? (
                  <>
                    <div className="pi-card-l1" style={{ fontWeight: 400 }}>
                      <p>{d.card.sentence}</p>
                      <span className={`pi-headline pi-money ${sectionClass(d.card.section)}`.trim()}>{usd(d.card.impact)}</span>
                    </div>
                    <p className="pi-label">{d.card.section === 'owed' ? 'outstanding' : 'a week'}</p>
                  </>
                ) : (
                  <p className="pi-secondary">{d.note ?? (d.moved_to ? `Moving to ${d.moved_to.name}. The next digest checks the invoice.` : 'Nothing to raise this week.')}</p>
                )}
                {d.card && d.note && <p className="pi-secondary">{d.note}</p>}
                <div className="pi-stack-sm">
                  {d.card?.action.kind === 'move' && app.canManage && (
                    <button type="button" className="pi-btn" disabled={busy} onClick={() => void move(d)}>
                      {d.card.action.label}
                    </button>
                  )}
                  {app.canManage && d.last_supplier && (
                    <button type="button" className={d.card?.action.kind === 'draft' ? 'pi-btn' : 'pi-btn-o'} disabled={busy} onClick={() => void draft(d)}>
                      Draft message to supplier
                    </button>
                  )}
                  <button type="button" className="pi-btn-o" onClick={() => askAboutItem({ id: d.id, name: d.name })}>
                    Ask about this item
                  </button>
                </div>
              </section>

              {d.series.length > 0 && (
                <section className="pi-stack-sm">
                  <p className="pi-label-strong">Last 8 weeks, per {d.pack_unit}</p>
                  <Sparkline series={d.series} />
                </section>
              )}

              <section className="pi-stack-sm">
                <p className="pi-label-strong">Invoice lines</p>
                {d.lines.length === 0 ? (
                  <p className="pi-secondary">No invoice lines matched to this item.</p>
                ) : (
                  <div className="pi-table-wrap">
                    <table className="pi-table">
                      <thead>
                        <tr>
                          <th>Date</th>
                          <th>Supplier</th>
                          <th>As printed</th>
                          <th className="num">Qty</th>
                          <th>Pack</th>
                          <th className="num">Per {d.pack_unit}</th>
                          <th className="num">Per {d.unit}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {d.lines.map((line) => (
                          <tr key={line.id}>
                            <td className="pi-money">{shortDate(line.invoice_date)}</td>
                            <td>{line.supplier.name}</td>
                            <td>{line.description}</td>
                            <td className="num pi-money">
                              {line.qty}
                              {line.delivered_qty !== null && line.delivered_qty !== line.qty && ` (${line.delivered_qty} delivered)`}
                            </td>
                            <td>{line.pack_text ?? (line.pack_size ? `${line.pack_size} ${d.unit}` : '')}</td>
                            <td className="num pi-money">{usd(line.unit_price)}</td>
                            <td className="num pi-money">{line.per_unit === null ? '' : usd(line.per_unit)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
            </>
          )}
        </Loaded>
      </div>
    </main>
  )
}
