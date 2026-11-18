import { useEffect, useState } from 'react'
import { type OutreachDraft, discardDraft, getDraft, sendDraft, updateDraft } from '../api/client'
import type { AppContext } from '../app/context'
import { Icon } from '../components/Icon'
import { Loaded } from '../components/PageState'
import { shortDate } from '../lib/format'
import { useHashParam } from '../lib/useHashRoute'
import { useLoad } from '../lib/useLoad'

type Fields = { to_name: string; to_email: string; subject: string; body: string }

const fieldsOf = (d: OutreachDraft): Fields => ({ to_name: d.to_name ?? '', to_email: d.to_email ?? '', subject: d.subject, body: d.body })

/** The address line for the mail app: "Dana Ruiz <dana@example.com>", or just the address. */
const recipient = (f: Fields) => (f.to_name.trim() && f.to_email.trim() ? `${f.to_name.trim()} <${f.to_email.trim()}>` : f.to_email.trim())

/**
 * The agent's draft to a supplier, full screen: edit it, then send it from
 * your own mail app, copy it, or discard it. Nothing is ever sent for you.
 */
export function DraftPage({ app }: { app: AppContext }) {
  const id = useHashParam()
  const draft = useLoad(() => getDraft(id), [id])
  const [fields, setFields] = useState<Fields | null>(null)
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (draft.data && fields === null) setFields(fieldsOf(draft.data))
  }, [draft.data, fields])

  const leave = () => {
    if (window.history.length > 1) window.history.back()
    else window.location.hash = '#digest'
  }

  const save = async (): Promise<OutreachDraft | null> => {
    if (!fields || !draft.data) return null
    const original = fieldsOf(draft.data)
    const changed = (Object.keys(fields) as (keyof Fields)[]).some((k) => fields[k] !== original[k])
    if (!changed) return draft.data
    return updateDraft(id, { to_name: fields.to_name.trim() || null, to_email: fields.to_email.trim() || null, subject: fields.subject, body: fields.body })
  }

  const send = async () => {
    if (!fields) return
    setBusy(true)
    try {
      await save()
      await sendDraft(id)
      const mailto = `mailto:${encodeURIComponent(recipient(fields))}?subject=${encodeURIComponent(fields.subject)}&body=${encodeURIComponent(fields.body)}`
      window.location.href = mailto
      app.toast('Opened in your mail app', 'Press send there. The rep is remembered for next time.')
      setConfirm(false)
      window.setTimeout(() => {
        window.location.hash = '#digest'
      }, 300)
    } catch (err) {
      app.toast('Couldn’t send', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const copy = async () => {
    if (!fields) return
    try {
      await save()
      await navigator.clipboard.writeText(`To: ${recipient(fields)}\nSubject: ${fields.subject}\n\n${fields.body}`)
      app.toast('Copied', 'Paste it into a text or an email.')
    } catch (err) {
      app.toast('Couldn’t copy', err instanceof Error ? err.message : String(err))
    }
  }

  const discard = async () => {
    setBusy(true)
    try {
      await discardDraft(id)
      app.toast('Draft discarded')
      leave()
    } catch (err) {
      app.toast('Couldn’t discard', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const set = (key: keyof Fields) => (value: string) => setFields((f) => (f ? { ...f, [key]: value } : f))

  return (
    <main className="pi">
      <div className="pi-page">
        <Loaded load={draft} what="the draft" placeholderHeight={300}>
          {(d) => {
            const f = fields ?? fieldsOf(d)
            const locked = d.status !== 'draft'
            return (
              <>
                <div className="pi-card-l1">
                  <h1 className="pi-h1">Message to {d.supplier.name}</h1>
                  <a className="pi-back" href="#digest" style={{ minHeight: 44 }}>
                    <Icon name="close" size={16} />
                    Close
                  </a>
                </div>
                {locked && <p className="pi-secondary">{d.status === 'sent' ? `Sent ${d.sent_at ? shortDate(d.sent_at) : ''}.` : 'Discarded.'}</p>}

                <div className="pi-field">
                  <label className="pi-label-strong" htmlFor="draft-to-name">
                    To
                  </label>
                  <div className="pi-answers">
                    <input id="draft-to-name" className="pi-input" placeholder="Rep’s name" value={f.to_name} disabled={locked} onChange={(e) => set('to_name')(e.target.value)} />
                    <input id="draft-to-email" className="pi-input" type="email" placeholder="rep@supplier.com" aria-label="Rep’s email" value={f.to_email} disabled={locked} onChange={(e) => set('to_email')(e.target.value)} />
                  </div>
                  {d.remembered_from && <p className="pi-label">Remembered from your {shortDate(d.remembered_from)} message.</p>}
                </div>
                <div className="pi-field">
                  <label className="pi-label-strong" htmlFor="draft-subject">
                    Subject
                  </label>
                  <input id="draft-subject" className="pi-input" value={f.subject} disabled={locked} onChange={(e) => set('subject')(e.target.value)} />
                </div>
                <div className="pi-field">
                  <label className="pi-label-strong" htmlFor="draft-body">
                    Body
                  </label>
                  <textarea id="draft-body" className="pi-textarea" rows={9} value={f.body} disabled={locked} onChange={(e) => set('body')(e.target.value)} />
                </div>
                <div className="pi-stack-sm">
                  <p className="pi-label-strong">Attached invoice lines</p>
                  <ul className="pi-lines" style={{ fontSize: 15, lineHeight: '20px' }}>
                    {d.lines.map((line) => (
                      <li key={line.line_id} className="pi-money">
                        {line.label}
                      </li>
                    ))}
                  </ul>
                </div>

                {!locked && (
                  <div className="pi-actions" style={{ justifyContent: 'flex-start' }}>
                    <button type="button" className="pi-btn" disabled={busy || !f.to_email.trim()} onClick={() => setConfirm(true)} title={f.to_email.trim() ? undefined : 'Add the rep’s email first'}>
                      Send via email
                    </button>
                    <button type="button" className="pi-btn-o" disabled={busy} onClick={() => void copy()}>
                      Copy
                    </button>
                    <button type="button" className="pi-link" disabled={busy} onClick={() => void discard()}>
                      Discard
                    </button>
                  </div>
                )}

                {confirm && (
                  <div className="pi-sheet-backdrop" onClick={() => setConfirm(false)}>
                    <div className="pi-sheet" role="dialog" aria-modal="true" aria-labelledby="confirm-title" onClick={(e) => e.stopPropagation()}>
                      <h2 id="confirm-title" className="pi-h1">
                        Send this email?
                      </h2>
                      <dl>
                        <dt>To</dt>
                        <dd>
                          {f.to_name.trim() && (
                            <>
                              {f.to_name.trim()}, {d.supplier.name}
                              <br />
                            </>
                          )}
                          <span className="pi-secondary">{f.to_email.trim()}</span>
                        </dd>
                        <dt>Subject</dt>
                        <dd>{f.subject}</dd>
                        <dt>Attached</dt>
                        <dd>
                          {d.lines.length} invoice {d.lines.length === 1 ? 'line' : 'lines'}
                        </dd>
                      </dl>
                      <p className="pi-label">Opens in your mail app with everything filled in. You press send there.</p>
                      <div className="pi-stack-sm">
                        <button type="button" className="pi-btn" disabled={busy} onClick={() => void send()}>
                          Send
                        </button>
                        <button type="button" className="pi-btn-o" onClick={() => setConfirm(false)}>
                          Cancel
                        </button>
                      </div>
                    </div>
                  </div>
                )}
              </>
            )
          }}
        </Loaded>
      </div>
    </main>
  )
}
