import { type PriceItem, listPriceItems } from '../api/client'
import type { AppContext } from '../app/context'
import { Loaded } from '../components/PageState'
import { Money } from '../components/prices'
import { shortDate } from '../lib/format'
import { useLoad } from '../lib/useLoad'

/** The second line of a row: where it stands, in one sentence. */
function secondLine(item: PriceItem) {
  if (item.state === 'hidden') return `Hidden until ${item.hidden_until ? shortDate(item.hidden_until) : 'next month'}`
  if (item.state === 'insufficient') return 'Not enough matched invoices to compare yet.'
  if (item.state === 'awaiting')
    return `${item.questions_open} ${item.questions_open === 1 ? 'question' : 'questions'} open. Answer ${item.questions_open === 1 ? 'it' : 'them'}`
  if (item.last_price !== null && item.last_supplier && item.last_date)
    return `Last paid ${usdPlain(item.last_price)} a ${item.pack_unit} at ${item.last_supplier.name}, ${shortDate(item.last_date)}`
  return ''
}

const usdPlain = (n: number) => `$${n.toFixed(2)}`

const moved = (item: PriceItem) => item.change_8w !== null && item.change_8w !== 0 && item.state !== 'insufficient' && item.state !== 'awaiting'

/** Under the figure: the direction as a share, and the price it is measured from. */
function caption(item: PriceItem) {
  if (!moved(item)) return ''
  const pct = item.change_8w_pct != null ? `${item.change_8w! > 0 ? 'up' : 'down'} ${Math.abs(item.change_8w_pct).toFixed(0)}%` : item.change_8w! > 0 ? 'up' : 'down'
  const from = item.first_price_8w != null && item.first_date_8w ? ` from ${usdPlain(item.first_price_8w)} on ${shortDate(item.first_date_8w)}` : ' over 8 weeks'
  return `a ${item.pack_unit}, ${pct}${from}`
}

/** One sentence over the list: how many items, and how many moved which way. */
function summary(rows: PriceItem[]) {
  const up = rows.filter((r) => moved(r) && r.change_8w! > 0)
  const down = rows.filter((r) => moved(r) && r.change_8w! < 0)
  const waiting = rows.filter((r) => r.state === 'awaiting').length
  const young = rows.filter((r) => r.state === 'insufficient').length
  const upTotal = up.reduce((sum, r) => sum + r.change_8w!, 0)
  const parts = [`${rows.length} ${rows.length === 1 ? 'item' : 'items'} matched from your invoices.`]
  if (up.length) parts.push(`${up.length} ${up.length === 1 ? 'costs' : 'cost'} more a pack than eight weeks ago, ${usdPlain(upTotal)} more in all.`)
  if (down.length) parts.push(`${down.length} ${down.length === 1 ? 'costs' : 'cost'} less.`)
  if (waiting) parts.push(`${waiting} ${waiting === 1 ? 'waits' : 'wait'} on a question.`)
  if (young) parts.push(`${young} ${young === 1 ? 'is' : 'are'} too new to compare.`)
  return parts.join(' ')
}

/** Every canonical item, one row each: what you last paid, and how it moved over eight weeks. */
export function ItemsPage(_: { app: AppContext }) {
  const items = useLoad(listPriceItems, [])
  return (
    <main className="pi">
      <div className="pi-page">
        <div>
          <h1 className="pi-h1">Items</h1>
          <p className="pi-secondary pi-intro">
            Every item the invoices have been matched to, with the price you last paid per pack and how that price moved over the
            last eight weeks at the same supplier. The figure on the right is the change per pack: <span className="pi-up">red is up</span>
            , <span className="pi-owed">green is down</span>, biggest moves first. Tap a row for eight weeks of prices by supplier and the
            invoice lines behind them.
          </p>
        </div>
        <Loaded load={items} what="items" placeholderHeight={200}>
          {(rows) =>
            rows.length === 0 ? (
              <p className="pi-secondary">No matched items yet.</p>
            ) : (
              <div>
                <p className="pi-summary" aria-label="Items summary">
                  {summary(rows)}
                </p>
                <div aria-label="Items" role="list">
                  {rows.map((item) => {
                    const href = item.state === 'awaiting' ? '#questions' : `#item/${item.id}`
                    const cap = caption(item)
                    return (
                      <a key={item.id} role="listitem" className={`pi-row${item.state === 'hidden' ? ' pi-hidden' : ''}`} href={href}>
                        <span className="pi-row-text">
                          <span>{item.name}</span>
                          <span
                            className="pi-secondary pi-money"
                            style={item.state === 'awaiting' ? { color: 'var(--ink)', textDecoration: 'underline', textUnderlineOffset: 3 } : undefined}
                          >
                            {secondLine(item)}
                          </span>
                        </span>
                        {moved(item) ? (
                          <span className="pi-row-change" title={`Change per ${item.pack_unit} over eight weeks at ${item.last_supplier?.name ?? 'the same supplier'}`}>
                            <Money value={item.change_8w!} signed className={`pi-row-value ${item.state === 'hidden' ? '' : item.change_8w! > 0 ? 'pi-up' : 'pi-owed'}`.trim()} />
                            <span className="pi-row-caption">{cap}</span>
                          </span>
                        ) : (
                          item.state === 'ok' && <span className="pi-row-caption">Steady for 8 weeks</span>
                        )}
                      </a>
                    )
                  })}
                </div>
              </div>
            )
          }
        </Loaded>
      </div>
    </main>
  )
}
