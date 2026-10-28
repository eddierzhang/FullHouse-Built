import { type ChangeEvent, type FormEvent, Fragment, useEffect, useState } from 'react'
import {
  type AgentQuestion,
  type InvoiceSource,
  type NewInvoiceLine,
  type PriceStatus,
  type QuestionAnswer,
  answerQuestion,
  confirmInvoice,
  fetchPriceStatus,
  listInvoices,
  parseInvoiceText,
  readInvoiceFile,
  saveInvoice,
  readsPhotos,
  syncBillcom,
} from '../api/client'
import type { AppContext } from '../app/context'
import { Loaded } from '../components/PageState'
import { AgentMessage, Money, QuestionCard } from '../components/prices'
import { shortDate } from '../lib/format'
import { useHashParam } from '../lib/useHashRoute'
import { useLoad } from '../lib/useLoad'

type Mode = 'idle' | 'choose' | 'billcom' | 'forward' | 'photo' | 'lines'

/** What the lines form starts from: typed, parsed from text, or read from a photo or PDF. */
type Prefill = {
  lines: NewInvoiceLine[]
  supplier?: string | null
  date?: string | null
  reference?: string | null
  total?: number | null
  skipped?: number[]
  reasons?: string[]
}

const BLANK_LINE: NewInvoiceLine = { description: '', qty: 1, pack_text: '', unit_price: 0, delivered_qty: null }
const today = () => new Date().toISOString().slice(0, 10)

/** A Bill.com export, or any CSV with a description, quantity and price column, as lines. */
function linesFromCsv(text: string): NewInvoiceLine[] | null {
  const rows = text
    .split(/\r?\n/)
    .map((row) => row.split(',').map((cell) => cell.trim().replace(/^"|"$/g, '')))
    .filter((cells) => cells.some((c) => c))
  if (rows.length < 2) return null
  const header = rows[0].map((h) => h.toLowerCase())
  const find = (...names: string[]) => header.findIndex((h) => names.some((n) => h.includes(n)))
  const desc = find('description', 'item', 'desc', 'product')
  const qty = find('qty', 'quantity')
  const price = find('unit price', 'rate', 'price', 'cost')
  const pack = find('pack', 'size', 'uom', 'unit')
  const amount = find('amount', 'total', 'extended')
  if (desc === -1 || price === -1) return null
  const number = (s: string | undefined) => Number((s ?? '').replace(/[^0-9.-]/g, ''))
  return rows
    .slice(1)
    .map((cells) => ({
      description: cells[desc] ?? '',
      qty: qty === -1 ? 1 : number(cells[qty]) || 1,
      pack_text: pack === -1 || pack === price ? null : cells[pack] || null,
      unit_price: number(cells[price]) || 0,
      extended: amount === -1 ? null : number(cells[amount]) || null,
    }))
    .filter((line) => line.description)
}

/** The lines of one invoice, typed or read, checked by eye before they're saved. */
function LinesForm({
  status,
  initial,
  source,
  note,
  onSaved,
  onCancel,
  onError,
}: {
  status: PriceStatus
  initial: Prefill
  source: InvoiceSource
  note?: string
  onSaved: (first: AgentQuestion | null, summary: string) => void
  onCancel: () => void
  onError: (m: string) => void
}) {
  const guessId = status.suppliers.find((s) => s.name.toLowerCase() === (initial.supplier ?? '').toLowerCase())?.id
  const [supplierId, setSupplierId] = useState<string>(guessId ?? (initial.supplier ? 'new' : status.suppliers[0]?.id ?? 'new'))
  const [supplierName, setSupplierName] = useState(guessId ? '' : initial.supplier ?? '')
  const [date, setDate] = useState(initial.date ?? today())
  const [reference, setReference] = useState(initial.reference ?? '')
  const [printedTotal, setPrintedTotal] = useState(initial.total === null || initial.total === undefined ? '' : String(initial.total))
  const [lines, setLines] = useState<NewInvoiceLine[]>(initial.lines.length ? initial.lines : [{ ...BLANK_LINE }])
  const [busy, setBusy] = useState(false)

  const setLine = (i: number, patch: Partial<NewInvoiceLine>) => setLines((ls) => ls.map((l, j) => (j === i ? { ...l, ...patch } : l)))

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const kept = lines.filter((l) => l.description.trim())
    if (kept.length === 0) return onError('Type at least one line.')
    if (supplierId === 'new' && !supplierName.trim()) return onError('Name the supplier.')
    setBusy(true)
    try {
      const saved = await saveInvoice({
        supplier_id: supplierId === 'new' ? null : supplierId,
        supplier_name: supplierId === 'new' ? supplierName.trim() : null,
        invoice_date: date,
        reference: reference.trim() || null,
        source,
        total: printedTotal.trim() === '' || Number.isNaN(Number(printedTotal)) ? null : Number(printedTotal),
        lines: kept.map((l) => ({
          description: l.description.trim(),
          qty: Number(l.qty) || 1,
          pack_text: l.pack_text?.trim() || null,
          unit_price: Number(l.unit_price) || 0,
          extended: l.extended ?? null,
          delivered_qty: l.delivered_qty === null || l.delivered_qty === undefined || Number.isNaN(Number(l.delivered_qty)) ? null : Number(l.delivered_qty),
          confidence: l.confidence ?? null,
        })),
      })
      onSaved(
        saved.first_question,
        `Saved ${saved.line_count} ${saved.line_count === 1 ? 'line' : 'lines'}: ${saved.matched} matched, ${saved.questions_asked} ${saved.questions_asked === 1 ? 'question' : 'questions'}.`,
      )
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const total = lines.reduce((sum, l) => sum + (Number(l.qty) || 0) * (Number(l.unit_price) || 0), 0)
  const printed = printedTotal.trim() === '' ? null : Number(printedTotal)
  const differs = printed !== null && Number.isFinite(printed) && Math.abs(printed - total) > 0.02
  const unsure = (l: NewInvoiceLine) => l.confidence !== null && l.confidence !== undefined && l.confidence < 0.8

  return (
    <form className="pi-stack" onSubmit={submit} aria-label="Invoice lines">
      {note && <p className="pi-secondary">{note}</p>}
      {initial.reasons && initial.reasons.length > 0 && (
        <div className="pi-stack-sm" role="status">
          <p className="pi-label-strong">Check these before you save</p>
          <ul className="pi-secondary" style={{ margin: 0, paddingLeft: 18 }}>
            {initial.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </div>
      )}
      {initial.skipped && initial.skipped.length > 0 && (
        <p className="pi-secondary">
          Couldn’t read {initial.skipped.length === 1 ? 'line' : 'lines'} {initial.skipped.join(', ')}. Type {initial.skipped.length === 1 ? 'it' : 'them'} below if {initial.skipped.length === 1 ? 'it matters' : 'they matter'}.
        </p>
      )}
      <div className="pi-answers" style={{ flexWrap: 'wrap' }}>
        <div className="pi-field" style={{ flex: '1 1 200px' }}>
          <label className="pi-label-strong" htmlFor="inv-supplier">
            Supplier
          </label>
          <select id="inv-supplier" className="pi-input pi-input-sm" value={supplierId} onChange={(e) => setSupplierId(e.target.value)}>
            {status.suppliers.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
            <option value="new">Another supplier</option>
          </select>
        </div>
        {supplierId === 'new' && (
          <div className="pi-field" style={{ flex: '1 1 200px' }}>
            <label className="pi-label-strong" htmlFor="inv-supplier-name">
              Supplier name
            </label>
            <input id="inv-supplier-name" className="pi-input pi-input-sm" value={supplierName} onChange={(e) => setSupplierName(e.target.value)} />
          </div>
        )}
        <div className="pi-field" style={{ flex: '0 1 160px' }}>
          <label className="pi-label-strong" htmlFor="inv-date">
            Invoice date
          </label>
          <input id="inv-date" className="pi-input pi-input-sm" type="date" value={date} max={today()} onChange={(e) => setDate(e.target.value)} />
        </div>
        <div className="pi-field" style={{ flex: '0 1 160px' }}>
          <label className="pi-label-strong" htmlFor="inv-ref">
            Invoice number
          </label>
          <input id="inv-ref" className="pi-input pi-input-sm" value={reference} onChange={(e) => setReference(e.target.value)} />
        </div>
        <div className="pi-field" style={{ flex: '0 1 140px' }}>
          <label className="pi-label-strong" htmlFor="inv-total">
            Invoice total
          </label>
          <input id="inv-total" className="pi-input pi-input-sm pi-money" type="number" inputMode="decimal" step="0.01" min={0} placeholder="as printed" value={printedTotal} onChange={(e) => setPrintedTotal(e.target.value)} />
        </div>
      </div>

      <div className="pi-table-wrap">
        <table className="pi-table" style={{ minWidth: 640 }}>
          <thead>
            <tr>
              <th>As printed</th>
              <th className="num">Qty</th>
              <th>Pack</th>
              <th className="num">Price each</th>
              <th className="num">Delivered</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {lines.map((line, i) => (
              <tr key={i}>
                <td>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                    <input className="pi-input pi-input-sm" aria-label={`Line ${i + 1} as printed`} value={line.description} onChange={(e) => setLine(i, { description: e.target.value })} style={{ minWidth: 220 }} />
                    {unsure(line) && (
                      <span className="pi-label" title="The reader wasn’t sure of this line">
                        Check
                      </span>
                    )}
                  </span>
                </td>
                <td>
                  <input className="pi-input pi-input-sm num" aria-label={`Line ${i + 1} quantity`} type="number" inputMode="decimal" step="any" min={0} value={line.qty} onChange={(e) => setLine(i, { qty: Number(e.target.value) })} style={{ width: 72 }} />
                </td>
                <td>
                  <input className="pi-input pi-input-sm" aria-label={`Line ${i + 1} pack`} placeholder="24CT" value={line.pack_text ?? ''} onChange={(e) => setLine(i, { pack_text: e.target.value })} style={{ width: 96 }} />
                </td>
                <td>
                  <input className="pi-input pi-input-sm" aria-label={`Line ${i + 1} price each`} type="number" inputMode="decimal" step="0.01" min={0} value={line.unit_price} onChange={(e) => setLine(i, { unit_price: Number(e.target.value) })} style={{ width: 96 }} />
                </td>
                <td>
                  <input className="pi-input pi-input-sm" aria-label={`Line ${i + 1} delivered`} type="number" inputMode="decimal" step="any" min={0} placeholder="all" value={line.delivered_qty ?? ''} onChange={(e) => setLine(i, { delivered_qty: e.target.value === '' ? null : Number(e.target.value) })} style={{ width: 80 }} />
                </td>
                <td>
                  <button type="button" className="pi-link" aria-label={`Remove line ${i + 1}`} onClick={() => setLines((ls) => ls.filter((_, j) => j !== i))}>
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="pi-actions" style={{ justifyContent: 'flex-start' }}>
        <button type="button" className="pi-link" onClick={() => setLines((ls) => [...ls, { ...BLANK_LINE }])}>
          Add a line
        </button>
        <span className="pi-secondary pi-money" style={{ marginLeft: 'auto' }}>
          {differs && printed !== null ? (
            <>
              Lines add to <Money value={total} />; the invoice says <Money value={printed} />.
            </>
          ) : (
            <>
              Lines add to <Money value={total} />
            </>
          )}
        </span>
      </div>
      <div className="pi-actions" style={{ justifyContent: 'flex-start' }}>
        <button type="submit" className="pi-btn" disabled={busy}>
          Save invoice
        </button>
        <button type="button" className="pi-link" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  )
}

/**
 * Where invoices come in. Before the first one: three ways to add it, and
 * nothing else. After it: the agent's one question about the biggest line.
 * Then the list, and the same three ways behind "Add invoice".
 */
export function InvoicesPage({ app }: { app: AppContext }) {
  const status = useLoad(fetchPriceStatus, [])
  const invoices = useLoad(listInvoices, [])
  const highlighted = useHashParam()
  const [mode, setMode] = useState<Mode>('idle')
  const [parsed, setParsed] = useState<Prefill | null>(null)
  const [pasted, setPasted] = useState('')
  const [photo, setPhoto] = useState<string | null>(null)
  const [reading, setReading] = useState(false)
  const [readFrom, setReadFrom] = useState<'photo' | 'pdf' | null>(null)
  const [first, setFirst] = useState<AgentQuestion | null>(null)
  const [summary, setSummary] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => () => {
    if (photo) URL.revokeObjectURL(photo)
  }, [photo])

  const fail = (m: string) => app.toast('Couldn’t do that', m)
  const reset = () => {
    setMode('idle')
    setParsed(null)
    setPasted('')
    setPhoto(null)
    setReading(false)
    setReadFrom(null)
  }

  /** A photo or PDF, read on the server into lines for the person to check. */
  const readFile = async (file: File, from: 'photo' | 'pdf') => {
    setReading(true)
    setReadFrom(from)
    try {
      const result = await readInvoiceFile(file)
      setParsed({
        lines: result.lines,
        supplier: result.supplier_guess,
        date: result.date_guess,
        reference: result.reference,
        total: result.total,
        reasons: result.needs_review ? result.reasons : [],
      })
      setMode('lines')
    } catch (err) {
      fail(err instanceof Error ? err.message : String(err))
      // Fall back to typing the lines, as before.
      setReadFrom(null)
      setParsed({ lines: [] })
      setMode('lines')
    } finally {
      setReading(false)
    }
  }

  const onPdf = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    void readFile(file, 'pdf')
  }

  const pullBillcom = async () => {
    setBusy(true)
    try {
      const result = await syncBillcom()
      reset()
      setSummary(
        `Pulled ${result.pulled} ${result.pulled === 1 ? 'bill' : 'bills'}. ${result.added} new. ${result.questions_asked} ${result.questions_asked === 1 ? 'question' : 'questions'}.`,
      )
      void status.reload()
      void invoices.reload()
      app.refresh()
    } catch (err) {
      fail(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const confirm = async (id: string) => {
    try {
      await confirmInvoice(id)
      void invoices.reload()
      app.refresh()
    } catch (err) {
      fail(err instanceof Error ? err.message : String(err))
    }
  }

  const onSaved = (question: AgentQuestion | null, text: string) => {
    reset()
    setFirst(question)
    setSummary(text)
    void status.reload()
    void invoices.reload()
    app.refresh()
  }

  const parsePasted = async () => {
    if (!pasted.trim()) return
    setBusy(true)
    try {
      const result = await parseInvoiceText(pasted)
      if (result.lines.length === 0) return fail('Couldn’t read any lines from that. Type them instead.')
      setParsed({ lines: result.lines, supplier: result.supplier_guess, date: result.date_guess, skipped: result.skipped })
      setMode('lines')
    } catch (err) {
      fail(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const onCsv = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    const text = await file.text()
    const lines = linesFromCsv(text)
    if (lines && lines.length > 0) {
      setParsed({ lines })
      setMode('lines')
      return
    }
    // Not a table we recognise: read it as printed text.
    setPasted(text)
    await parsePasted()
  }

  const onPhoto = (reader: PriceStatus['reader']) => (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    setPhoto(URL.createObjectURL(file))
    if (readsPhotos(reader)) {
      void readFile(file, 'photo')
      return
    }
    setParsed({ lines: [] })
    setMode('lines')
  }

  const answerFirst = async (body: QuestionAnswer) => {
    if (!first) return
    await answerQuestion(first.id, body)
    window.setTimeout(() => {
      setFirst(null)
      void status.reload()
      app.refresh()
    }, 240)
  }

  const choices = (
    <div className="pi-stack">
      <button type="button" className="pi-choice" onClick={() => setMode('billcom')}>
        <span className="pi-choice-title">Connect Bill.com</span>
        <span className="pi-secondary">Reads the invoices you already pay there.</span>
      </button>
      <button type="button" className="pi-choice" onClick={() => setMode('forward')}>
        <span className="pi-choice-title">Forward invoices</span>
        <span className="pi-secondary">Send emailed invoices or PDFs to one address.</span>
      </button>
      <button type="button" className="pi-choice" onClick={() => setMode('photo')}>
        <span className="pi-choice-title">Snap a photo</span>
        <span className="pi-secondary">Shoot a paper invoice with your phone.</span>
      </button>
    </div>
  )

  const source: InvoiceSource = mode === 'billcom' ? 'billcom' : photo ? 'photo' : readFrom === 'pdf' ? 'pdf' : parsed && pasted ? 'email' : 'typed'

  return (
    <main className="pi">
      <div className="pi-page">
        <Loaded load={status} what="your invoices" placeholderHeight={200}>
          {(s) => {
            const onboarding = s.invoice_count === 0 && !first && !summary
            return (
              <>
                {!onboarding && (
                  <div className="pi-card-l1">
                    <h1 className="pi-h1">Invoices</h1>
                    {mode === 'idle' && app.canManage && (
                      <button type="button" className="pi-btn-o" onClick={() => setMode('choose')}>
                        Add invoice
                      </button>
                    )}
                  </div>
                )}

                {first && (
                  <AgentMessage>
                    <QuestionCard question={first} onAnswer={answerFirst} />
                  </AgentMessage>
                )}
                {summary && !first && <p className="pi-secondary">{summary}</p>}

                {onboarding && mode === 'idle' && (
                  <>
                    <p>Add one supplier invoice. Everything else follows from it.</p>
                    {app.canManage ? choices : <p className="pi-secondary">The owner adds the first invoice.</p>}
                  </>
                )}
                {!onboarding && mode === 'choose' && choices}

                {mode === 'billcom' && (
                  <section className="pi-stack">
                    <h2 className="pi-h1">Connect Bill.com</h2>
                    {s.billcom_configured ? (
                      <>
                        <p className="pi-secondary">Pulls the bills you pay in Bill.com, with their lines. A bill already in is skipped.</p>
                        <button type="button" className="pi-btn" style={{ alignSelf: 'flex-start' }} disabled={busy} onClick={() => void pullBillcom()}>
                          Pull bills from Bill.com
                        </button>
                      </>
                    ) : (
                      <>
                        <p className="pi-secondary">Bill.com isn’t connected on this site.</p>
                        <p className="pi-secondary">Export the bills from Bill.com as a CSV and pick the file. Each row becomes an invoice line for you to check.</p>
                        <label className="pi-btn-o" style={{ alignSelf: 'flex-start' }}>
                          Choose the CSV
                          <input type="file" accept=".csv,text/csv,text/plain" className="visually-hidden" onChange={(e) => void onCsv(e)} />
                        </label>
                      </>
                    )}
                    <button type="button" className="pi-link" style={{ alignSelf: 'flex-start' }} onClick={reset}>
                      Back
                    </button>
                  </section>
                )}

                {mode === 'forward' && (
                  <section className="pi-stack">
                    <h2 className="pi-h1">Forward invoices</h2>
                    {s.forward_address ? (
                      <p className="pi-secondary">
                        Forward emailed invoices to <span style={{ color: '#111' }}>{s.forward_address}</span>. Or paste the invoice text here.
                      </p>
                    ) : (
                      <p className="pi-secondary">This site can’t receive mail yet. Paste the invoice text here instead.</p>
                    )}
                    <label className="visually-hidden" htmlFor="pasted">
                      Invoice text
                    </label>
                    {reading && <AgentMessage pending><p>Reading the invoice…</p></AgentMessage>}
                    <textarea id="pasted" className="pi-textarea" rows={8} placeholder={'KALE GRN 24CT   10  CS   31.20   312.00'} value={pasted} onChange={(e) => setPasted(e.target.value)} />
                    <div className="pi-actions" style={{ justifyContent: 'flex-start' }}>
                      <button type="button" className="pi-btn" disabled={busy || reading || !pasted.trim()} onClick={() => void parsePasted()}>
                        Read the lines
                      </button>
                      <label className="pi-btn-o">
                        Upload a PDF
                        <input type="file" accept="application/pdf,.pdf" className="visually-hidden" aria-label="Invoice PDF" disabled={reading} onChange={onPdf} />
                      </label>
                      <button type="button" className="pi-link" onClick={reset}>
                        Back
                      </button>
                    </div>
                  </section>
                )}

                {mode === 'photo' && (
                  <section className="pi-stack">
                    <h2 className="pi-h1">Snap a photo</h2>
                    {readsPhotos(s.reader) ? (
                      <p className="pi-secondary">Take a picture of the invoice. The lines are read from it for you to check before anything is saved.</p>
                    ) : (
                      <p className="pi-secondary">Take a picture of the invoice, then type the lines from it. This site reads PDFs, not photos: it has no model connected.</p>
                    )}
                    {reading && <AgentMessage pending><p>Reading the invoice…</p></AgentMessage>}
                    <label className="pi-btn" style={{ alignSelf: 'flex-start' }}>
                      Take a photo
                      <input type="file" accept="image/*" capture="environment" className="visually-hidden" disabled={reading} onChange={onPhoto(s.reader)} />
                    </label>
                    <button type="button" className="pi-link" style={{ alignSelf: 'flex-start' }} onClick={reset}>
                      Back
                    </button>
                  </section>
                )}

                {mode === 'lines' && parsed && (
                  <section className="pi-stack">
                    <h2 className="pi-h1">{photo && !readFrom ? 'Type the lines from the photo' : 'Check the lines'}</h2>
                    {photo && <img src={photo} alt="The invoice you photographed" style={{ maxWidth: '100%', borderRadius: 8, border: '1px solid #e3e3e3' }} />}
                    <LinesForm status={s} initial={parsed} source={source} onSaved={onSaved} onCancel={reset} onError={fail} />
                  </section>
                )}

                {!onboarding && mode === 'idle' && (
                  <Loaded load={invoices} what="invoices" placeholderHeight={120}>
                    {(rows) =>
                      rows.length === 0 ? (
                        <p className="pi-secondary">No invoices yet.</p>
                      ) : (
                        <div className="pi-table-wrap">
                          <table className="pi-table" aria-label="Invoices">
                            <thead>
                              <tr>
                                <th>Date</th>
                                <th>Supplier</th>
                                <th>Number</th>
                                <th>From</th>
                                <th className="num">Lines</th>
                                <th className="num">Total</th>
                                <th className="num">Unmatched</th>
                              </tr>
                            </thead>
                            <tbody>
                              {rows.map((row) => (
                                <Fragment key={row.id}>
                                  <tr style={row.id === highlighted ? { background: '#efefef' } : undefined} aria-current={row.id === highlighted ? 'true' : undefined}>
                                    <td className="pi-money">{shortDate(row.invoice_date)}</td>
                                    <td>{row.supplier.name}</td>
                                    <td>{row.reference ?? ''}</td>
                                    <td>{row.source === 'billcom' ? 'Bill.com' : row.source === 'email' ? 'Email' : row.source === 'photo' ? 'Photo' : row.source === 'pdf' ? 'PDF' : 'Typed'}</td>
                                    <td className="num pi-money">{row.line_count}</td>
                                    <td className="num pi-money">
                                      <Money value={row.total} />
                                    </td>
                                    <td className="num pi-money">{row.unmatched || ''}</td>
                                  </tr>
                                  {row.status === 'needs_review' && (
                                    <tr className="pi-review-row">
                                      <td colSpan={7}>
                                        <div className="pi-stack-sm" role="status" aria-label={`Check the ${shortDate(row.invoice_date)} invoice from ${row.supplier.name}`}>
                                          <p className="pi-secondary">Not counted until checked.</p>
                                          {row.review_reasons.length > 0 && (
                                            <ul className="pi-secondary" style={{ margin: 0, paddingLeft: 18 }}>
                                              {row.review_reasons.map((r) => (
                                                <li key={r}>{r}</li>
                                              ))}
                                            </ul>
                                          )}
                                          {app.canManage && (
                                            <button type="button" className="pi-btn-o" style={{ alignSelf: 'flex-start' }} onClick={() => void confirm(row.id)}>
                                              Looks right
                                            </button>
                                          )}
                                        </div>
                                      </td>
                                    </tr>
                                  )}
                                </Fragment>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )
                    }
                  </Loaded>
                )}

                <p className="pi-label">{s.progress}</p>
              </>
            )
          }}
        </Loaded>
      </div>
    </main>
  )
}
