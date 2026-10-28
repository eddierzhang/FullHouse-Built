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
    return `${usdPlain(item.last_price)} at ${item.last_supplier.name}, ${shortDate(item.last_date)}`
  return ''
}

const usdPlain = (n: number) => `$${n.toFixed(2)}`

/** Every canonical item, one row each: what you last paid, and how it moved over eight weeks. */
export function ItemsPage(_: { app: AppContext }) {
  const items = useLoad(listPriceItems, [])
  return (
    <main className="pi">
      <div className="pi-page">
        <h1 className="pi-h1">Items</h1>
        <Loaded load={items} what="items" placeholderHeight={200}>
          {(rows) =>
            rows.length === 0 ? (
              <p className="pi-secondary">No matched items yet.</p>
            ) : (
              <div aria-label="Items" role="list">
                {rows.map((item) => {
                  const href = item.state === 'awaiting' ? '#questions' : `#item/${item.id}`
                  return (
                    <a key={item.id} role="listitem" className={`pi-row${item.state === 'hidden' ? ' pi-hidden' : ''}`} href={href}>
                      <span className="pi-row-text">
                        <span>{item.name}</span>
                        <span
                          className="pi-secondary pi-money"
                          style={item.state === 'awaiting' ? { color: '#111', textDecoration: 'underline', textUnderlineOffset: 3 } : undefined}
                        >
                          {secondLine(item)}
                        </span>
                      </span>
                      {item.change_8w !== null && item.change_8w !== 0 && item.state !== 'insufficient' && (
                        <Money
                          value={item.change_8w}
                          signed
                          className={`pi-row-value ${item.state === 'hidden' ? '' : item.change_8w > 0 ? 'pi-up' : 'pi-owed'}`.trim()}
                        />
                      )}
                    </a>
                  )
                })}
              </div>
            )
          }
        </Loaded>
      </div>
    </main>
  )
}
