import { type ChangeEvent, useState } from 'react'
import { type DocType, type DocumentRow, listDocuments, setDocumentType, uploadDocument } from '../api/client'
import type { AppContext } from '../app/context'
import { Icon } from '../components/Icon'
import { Empty, Loaded } from '../components/PageState'
import { relativeTime } from '../lib/format'
import { useLoad } from '../lib/useLoad'

const TYPE_LABELS: Record<DocType, string> = {
  invoice: 'Invoice', price_list: 'Price list', recipe: 'Recipe', prep_sheet: 'Prep sheet', license: 'License',
  permit: 'Permit', inspection: 'Inspection report', warranty: 'Warranty', other: 'Something else',
}
const STATUS: Record<DocumentRow['status'], { label: string; badge: string }> = {
  received: { label: 'Reading', badge: '' },
  extracted: { label: 'Read', badge: 'badge-info' },
  needs_review: { label: 'Needs a look', badge: 'badge-warning' },
  complete: { label: 'Done', badge: 'badge-success' },
}
const MAX_BYTES = 15 * 1024 * 1024

/** Anything handed over, read for what it is: an invoice, a price list, a recipe, a permit. */
export function DocumentsPage({ app }: { app: AppContext }) {
  const load = useLoad(listDocuments, [])
  const [busy, setBusy] = useState<string | null>(null)
  const [hint, setHint] = useState<DocType | ''>('')

  const onFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    if (file.size > MAX_BYTES) return app.toast('That file is over 15 MB')
    setBusy('upload')
    try {
      const buffer = new Uint8Array(await file.arrayBuffer())
      let binary = ''
      for (let i = 0; i < buffer.length; i += 0x8000) binary += String.fromCharCode(...buffer.subarray(i, i + 0x8000))
      const doc = await uploadDocument(file.name, file.type || 'application/pdf', btoa(binary), hint || null)
      app.toast('Received', `${doc.filename} is being read.`)
      await load.reload()
      window.setTimeout(() => void load.reload(), 2500)
    } catch (err) {
      app.toast('Couldn’t upload', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  const pick = async (doc: DocumentRow, docType: DocType) => {
    setBusy(doc.id)
    try {
      await setDocumentType(doc.id, docType)
      app.toast('Reading it as ' + TYPE_LABELS[docType].toLowerCase())
      await load.reload()
      window.setTimeout(() => void load.reload(), 2500)
    } catch (err) {
      app.toast('Couldn’t do that', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  const resultLink = (doc: DocumentRow) => {
    if (doc.result_table === 'supplier_invoices') return <a href="#invoices">Invoices</a>
    if (doc.result_table === 'agent_actions') return <a href="#approvals">Recommendations</a>
    return null
  }

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Documents</h1>
          <p className="page-subtitle">Invoices, price lists, recipe cards, permits and warranties. Each is read for what it is and turned into something to check.</p>
        </div>
        <label className="field" style={{ width: 180 }}>
          <span className="field-label">It is a…</span>
          <select className="input" value={hint} onChange={(e) => setHint(e.target.value as DocType | '')}>
            <option value="">let the reader decide</option>
            {(Object.keys(TYPE_LABELS) as DocType[]).filter((t) => t !== 'other' && t !== 'prep_sheet').map((t) => (
              <option key={t} value={t}>
                {TYPE_LABELS[t]}
              </option>
            ))}
          </select>
        </label>
        <label className="btn btn-primary">
          <Icon name="plus" size={16} /> Upload
          <input type="file" accept=".pdf,image/*,.txt,.csv" capture="environment" onChange={(e) => void onFile(e)} style={{ display: 'none' }} aria-label="Upload a document" />
        </label>
      </div>
      <Loaded load={load} what="documents" placeholderHeight={160}>
        {(docs) =>
          docs.length === 0 ? (
            <Empty title="Nothing yet">Upload a supplier’s price list, a recipe card or a permit, or forward one to your inbound address under Connections.</Empty>
          ) : (
            <section className="card">
              <ul className="list" aria-label="Documents">
                {docs.map((doc) => {
                  const status = STATUS[doc.status]
                  const unsure = doc.status === 'needs_review' && (doc.type_confidence ?? 0) < 0.9
                  return (
                    <li key={doc.id} className="list-item" style={{ alignItems: 'flex-start' }}>
                      <div className="list-item-main">
                        <div className="row wrap">
                          <span className="strong">{doc.filename ?? 'document'}</span>
                          <span className="badge badge-brand">{TYPE_LABELS[doc.doc_type]}</span>
                          <span className={`badge ${status.badge}`.trim()}>{status.label}</span>
                          <span className="muted small">
                            {doc.source === 'email' ? 'by email' : 'uploaded'} {relativeTime(doc.created_at)}
                          </span>
                        </div>
                        <div className="muted small" style={{ marginTop: 4 }}>
                          {doc.note}
                          {doc.extracted_summary.supplier ? ` · ${String(doc.extracted_summary.supplier)}` : ''}
                          {doc.extracted_summary.proposed != null ? ` · ${String(doc.extracted_summary.proposed)} proposed` : ''}
                          {Array.isArray(doc.extracted_summary.unmatched) && doc.extracted_summary.unmatched.length > 0
                            ? ` · not matched: ${(doc.extracted_summary.unmatched as string[]).join(', ')}`
                            : ''}
                          {doc.extracted_summary.expires_on ? ` · expires ${String(doc.extracted_summary.expires_on)}` : ''}
                          {resultLink(doc) && <> · {resultLink(doc)}</>}
                          {doc.file_url && (
                            <>
                              {' · '}
                              <a href={doc.file_url} target="_blank" rel="noreferrer">
                                Open file
                              </a>
                            </>
                          )}
                        </div>
                        {unsure && app.canManage && (
                          <div className="row wrap" style={{ marginTop: 8 }} aria-label={`What is ${doc.filename}?`}>
                            <span className="small">What is it?</span>
                            {(['invoice', 'price_list', 'recipe', 'license', 'permit', 'inspection', 'warranty'] as DocType[]).map((t) => (
                              <button key={t} className="btn btn-secondary btn-sm" disabled={busy === doc.id} onClick={() => void pick(doc, t)}>
                                {TYPE_LABELS[t]}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    </li>
                  )
                })}
              </ul>
            </section>
          )
        }
      </Loaded>
    </main>
  )
}
