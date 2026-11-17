import { useMemo, useState } from 'react'
import { uploadDocument } from '../api/client'
import { checkIn, type ExpectedOrder, fetchExpected, fetchReceipts, type LineStatus, supplierReplied } from '../api/receiving'
import type { AppContext } from '../app/context'
import { Icon } from '../components/Icon'
import { Empty, Freshness, Loaded } from '../components/PageState'
import { money, qty, relativeTime, shortDate } from '../lib/format'
import { useLoad } from '../lib/useLoad'

const STATUS_LABEL: Record<ExpectedOrder['status'], { label: string; badge: string }> = {
  ordered: { label: 'Ordered', badge: '' },
  confirmed: { label: 'Confirmed by supplier', badge: 'badge-info' },
  shipped: { label: 'On its way', badge: 'badge-info' },
  partial: { label: 'Part received', badge: 'badge-warning' },
}
const LINE_WORDS: Record<LineStatus, string> = { ok: 'All here', short: 'Short', over: 'Extra', substituted: 'Swapped', damaged: 'Damaged', missing: 'Missing', partial: 'Rest still coming' }
type Entry = { received: number; status: LineStatus | null; note: string }

const fileAsBase64 = (file: File) =>
  new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',')[1] ?? '')
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })

/** One delivery at the door: every line starts as ordered, so a delivery that is right takes one tap. */
function CheckIn({ app, order, onDone, onCancel }: { app: AppContext; order: ExpectedOrder; onDone: () => void; onCancel: () => void }) {
  const [entries, setEntries] = useState<Record<string, Entry>>(() => Object.fromEntries(order.lines.map((l) => [l.key, { received: l.expected, status: null, note: '' }])))
  const [photo, setPhoto] = useState<File | null>(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  // One key for this check-in: a retry after poor signal is answered with the first result, not booked twice.
  const key = useMemo(() => `door-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`, [])
  const set = (lineKey: string, change: Partial<Entry>) => setEntries((held) => ({ ...held, [lineKey]: { ...held[lineKey], ...change } }))
  const off = order.lines.filter((l) => Math.abs(entries[l.key].received - l.expected) > 1e-6 || (entries[l.key].status && entries[l.key].status !== 'ok'))

  const post = async () => {
    setBusy(true)
    try {
      let photo_document_id: string | undefined
      if (photo) {
        try {
          photo_document_id = (await uploadDocument(photo.name || 'packing-slip.jpg', photo.type || 'image/jpeg', await fileAsBase64(photo), null)).id
        } catch {
          app.toast('The photo didn’t upload', 'The delivery is still being checked in without it.')
        }
      }
      const result = await checkIn({
        po_id: order.id,
        client_key: key,
        note: note.trim() || undefined,
        photo_document_id,
        lines: order.lines.map((l) => ({ key: l.key, received: entries[l.key].received, status: entries[l.key].status ?? undefined, note: entries[l.key].note.trim() || undefined })),
      })
      app.toast(
        result.order_status === 'received' ? 'Delivery checked in' : 'Checked in; the rest is still expected',
        result.owed ? `${result.owed} line${result.owed === 1 ? '' : 's'} short. A message to the rep is drafted.` : 'Stock is updated.',
      )
      app.refresh()
      if (result.claim_draft_id) window.location.hash = `#draft/${result.claim_draft_id}`
      onDone()
    } catch (err) {
      app.toast('Could not check it in', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card" aria-labelledby="checkin-title">
      <div className="card-header">
        <div>
          <h2 className="card-title" id="checkin-title">
            {order.supplier_name}
          </h2>
          <div className="card-subtitle">Change only what is different from the order. Stock is updated when you finish.</div>
        </div>
        <button className="btn btn-ghost btn-sm" onClick={onCancel}>
          ← All deliveries
        </button>
      </div>
      <ul className="count-list receive-list">
        {order.lines.map((l) => {
          const entry = entries[l.key]
          const less = entry.received < l.expected - 1e-6
          return (
            <li key={l.key} className={`count-row${entry.status || Math.abs(entry.received - l.expected) > 1e-6 ? '' : ' done'}`}>
              <div className="count-row-main">
                <div className="count-row-name">{l.description}</div>
                <div className="muted small">
                  Ordered {qty(l.ordered)} {l.unit}
                  {l.received_so_far > 0 ? ` · ${qty(l.received_so_far)} already in` : ''}
                </div>
              </div>
              <div className="count-row-entry">
                <button className="btn btn-secondary btn-icon" aria-label={`One less ${l.description}`} onClick={() => set(l.key, { received: Math.max(0, entry.received - 1), status: null })}>
                  −
                </button>
                <input className="input count-input" type="number" inputMode="decimal" min="0" step="any" aria-label={`${l.description}, received in ${l.unit}`} value={entry.received} onChange={(e) => set(l.key, { received: Math.max(0, Number(e.target.value)), status: null })} />
                <button className="btn btn-secondary btn-icon" aria-label={`One more ${l.description}`} onClick={() => set(l.key, { received: entry.received + 1, status: null })}>
                  +
                </button>
              </div>
              {(less || entry.status) && (
                <div className="receive-why">
                  <div className="chip-row" role="group" aria-label={`What happened to ${l.description}`}>
                    {(['short', 'partial', 'damaged', 'substituted', 'missing'] as LineStatus[]).map((s) => (
                      <button key={s} type="button" className="chip-button" aria-pressed={(entry.status ?? (entry.received <= 0 ? 'missing' : 'short')) === s} onClick={() => set(l.key, { status: s })}>
                        {LINE_WORDS[s]}
                      </button>
                    ))}
                  </div>
                  <input className="input input-sm" type="text" maxLength={200} placeholder="Note for the record (optional)" aria-label={`Note about ${l.description}`} value={entry.note} onChange={(e) => set(l.key, { note: e.target.value })} />
                </div>
              )}
            </li>
          )
        })}
      </ul>
      <div className="card-body receive-finish">
        <label className="btn btn-secondary receive-photo">
          <input type="file" accept="image/*,application/pdf" capture="environment" hidden onChange={(e) => setPhoto(e.target.files?.[0] ?? null)} />
          {photo ? `Photo attached: ${photo.name}` : 'Add a photo of the slip'}
        </label>
        <input className="input" type="text" maxLength={255} placeholder="Anything about this delivery (optional)" aria-label="Note about this delivery" value={note} onChange={(e) => setNote(e.target.value)} />
        <button className="btn btn-primary" disabled={busy} onClick={() => void post()}>
          {busy ? 'Checking in…' : off.length === 0 ? 'Everything arrived' : `Check in with ${off.length} difference${off.length === 1 ? '' : 's'}`}
        </button>
      </div>
    </section>
  )
}

/**
 * Deliveries: what is due, soonest first, and one tap to check each in.
 * A supplier's confirmation or ship notice shows on the order; a late one
 * comes with a message to chase it, sent from your own mail.
 */
export function ReceivePage({ app }: { app: AppContext }) {
  const expected = useLoad(fetchExpected, [])
  const receipts = useLoad(fetchReceipts, [])
  const [openId, setOpenId] = useState<string | null>(null)
  const [replyFor, setReplyFor] = useState<string | null>(null)
  const [replyText, setReplyText] = useState('')
  const open = expected.data?.find((o) => o.id === openId)
  const reload = () => {
    expected.reload()
    receipts.reload()
  }

  const sendReply = async (orderId: string) => {
    try {
      const out = await supplierReplied(orderId, replyText)
      app.toast({ confirmed: 'Marked confirmed', shipped: 'Marked on its way', change: 'They want a change: have a look', other: 'Saved on the order' }[out.kind], out.note ?? undefined)
      setReplyFor(null)
      setReplyText('')
      reload()
    } catch (err) {
      app.toast('Could not read that', err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Deliveries</h1>
          <p className="page-subtitle">Check each delivery in against its order. Anything short becomes a message to the rep, drafted for you.</p>
        </div>
        <Freshness loads={[expected]} />
      </div>

      {open ? (
        <CheckIn
          app={app}
          order={open}
          onCancel={() => setOpenId(null)}
          onDone={() => {
            setOpenId(null)
            reload()
          }}
        />
      ) : (
        <div className="stack">
          <section className="card" aria-labelledby="due-title">
            <div className="card-header">
              <div>
                <h2 className="card-title" id="due-title">
                  Expected
                </h2>
                <div className="card-subtitle">Orders placed and not fully in.</div>
              </div>
            </div>
            <Loaded load={expected} what="deliveries" placeholderHeight={160}>
              {(orders) =>
                orders.length === 0 ? (
                  <div className="card-body">
                    <Empty title="Nothing on its way">An order shows here once it has been sent to the supplier, by you or by autopilot.</Empty>
                  </div>
                ) : (
                  <ul className="list">
                    {orders.map((o) => (
                      <li key={o.id} className="list-item receive-order">
                        <div className="list-item-main">
                          <div className="row wrap">
                            <span className="strong">{o.supplier_name}</span>
                            <span className={`badge ${STATUS_LABEL[o.status].badge}`}>{STATUS_LABEL[o.status].label}</span>
                            {o.late && (
                              <span className="badge badge-danger">
                                {o.days_late} day{o.days_late === 1 ? '' : 's'} late
                              </span>
                            )}
                            {o.autopilot && <span className="badge">Sent by autopilot</span>}
                          </div>
                          <div className="muted small">
                            {o.lines.length === 1 ? `${qty(o.lines[0].expected)} ${o.lines[0].unit} of ${o.lines[0].description}` : `${o.lines.length} lines`}
                            {o.estimated_cost ? ` · about ${money(o.estimated_cost)}` : ''}
                            {o.expected_on ? ` · due ${shortDate(o.expected_on)}` : ''}
                          </div>
                          {o.note && <div className="muted small">{o.note}</div>}
                          {replyFor === o.id && (
                            <form
                              className="receive-reply"
                              onSubmit={(e) => {
                                e.preventDefault()
                                if (replyText.trim()) void sendReply(o.id)
                              }}
                            >
                              <textarea className="input" rows={3} placeholder="Paste what the supplier said" aria-label="What the supplier said" value={replyText} onChange={(e) => setReplyText(e.target.value)} autoFocus />
                              <div className="row">
                                <button className="btn btn-secondary btn-sm" disabled={!replyText.trim()}>
                                  Read it
                                </button>
                                <button type="button" className="btn btn-ghost btn-sm" onClick={() => setReplyFor(null)}>
                                  Cancel
                                </button>
                              </div>
                            </form>
                          )}
                        </div>
                        <div className="row wrap receive-actions">
                          {o.late && o.chase && (
                            <a className="btn btn-ghost btn-sm" href={`mailto:${o.rep_email ?? ''}?subject=${encodeURIComponent(o.chase.subject)}&body=${encodeURIComponent(o.chase.body)}`}>
                              Chase it
                            </a>
                          )}
                          {app.canManage && replyFor !== o.id && (
                            <button className="btn btn-ghost btn-sm" onClick={() => setReplyFor(o.id)}>
                              They replied
                            </button>
                          )}
                          <button className="btn btn-primary btn-sm" onClick={() => setOpenId(o.id)}>
                            Check in
                          </button>
                        </div>
                      </li>
                    ))}
                  </ul>
                )
              }
            </Loaded>
          </section>

          <section className="card" aria-labelledby="past-title">
            <div className="card-header">
              <div>
                <h2 className="card-title" id="past-title">
                  Checked in lately
                </h2>
              </div>
            </div>
            <Loaded load={receipts} what="past deliveries" placeholderHeight={100}>
              {(rows) =>
                rows.length === 0 ? (
                  <div className="card-body">
                    <Empty title="None yet">Each delivery you check in is kept here with what was short.</Empty>
                  </div>
                ) : (
                  <ul className="list">
                    {rows.map((r) => {
                      const wrong = r.lines.filter((l) => l.status !== 'ok')
                      return (
                        <li key={r.id} className="list-item">
                          <Icon name={wrong.length ? 'alert' : 'check'} className={wrong.length ? 'alert-icon' : 'rule-ok'} />
                          <div className="list-item-main">
                            <div className="strong">
                              {r.supplier_name} · {r.lines.length} line{r.lines.length === 1 ? '' : 's'}
                            </div>
                            <div className="muted small">
                              {relativeTime(r.received_at)}
                              {r.received_by ? ` · ${r.received_by}` : ''}
                              {r.has_photo ? ' · photo kept' : ''}
                              {wrong.length ? ` · ${wrong.map((l) => `${l.description}: ${qty(l.received)} of ${qty(l.expected ?? 0)} ${l.unit}, ${LINE_WORDS[l.status].toLowerCase()}`).join('; ')}` : ' · all as ordered'}
                            </div>
                          </div>
                        </li>
                      )
                    })}
                  </ul>
                )
              }
            </Loaded>
          </section>
        </div>
      )}
    </main>
  )
}
