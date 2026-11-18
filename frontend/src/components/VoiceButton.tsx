import { useEffect, useRef, useState } from 'react'
import { askAgent } from '../api/client'
import { confirmSpeech, discardSpeech, learnAlias, parseSpeech, type VoicePreview } from '../api/voice'
import type { AppContext } from '../app/context'
import { qty } from '../lib/format'
import { say, useSpeech } from '../lib/speech'
import { Icon } from './Icon'

const EXAMPLES = ['Walk-in: mozzarella sixteen pounds, basil three bunches', 'Waste two pounds of chicken, dropped', 'Sysco delivery, everything arrived except one case of romaine', 'What did I pay for tomatoes last month?']
const REASONS: [string, string][] = [['spoiled', 'Spoiled'], ['dropped', 'Dropped'], ['overcooked', 'Overcooked'], ['prep', 'Prep loss'], ['comped', 'Comped'], ['staff_meal', 'Staff meal'], ['other', 'Other']]
const TITLES: Record<VoicePreview['intent'], string> = { count: 'Count', waste: 'Waste', receive: 'Delivery', ask: 'Question', navigate: 'Go to', unknown: 'Not sure' }

/**
 * Push to talk, from any page. What is said (or typed, where the browser
 * can't listen) comes back as a preview that is read aloud; nothing is
 * saved until Confirm. A question or "show me…" needs no confirm, because
 * neither changes anything.
 */
export function VoiceButton({ app }: { app: AppContext }) {
  const speech = useSpeech()
  const [open, setOpen] = useState(false)
  const [typed, setTyped] = useState('')
  const [preview, setPreview] = useState<VoicePreview | null>(null)
  const [amounts, setAmounts] = useState<Record<string, number>>({})
  const [picked, setPicked] = useState<Record<string, string>>({})
  const [reason, setReason] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [aloud, setAloud] = useState(true)
  const field = useRef<HTMLInputElement>(null)

  const close = () => {
    speech.cancel()
    if (preview && preview.status === 'previewed') void discardSpeech(preview.id).catch(() => {})
    setOpen(false)
    setPreview(null)
    setError(null)
    setTyped('')
  }
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && close()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }) // eslint-disable-line react-hooks/exhaustive-deps

  const understand = async (transcript: string, source: 'browser' | 'typed') => {
    if (source === 'typed') speech.cancel()
    setBusy(true)
    setError(null)
    try {
      const result = await parseSpeech(transcript, source)
      if (result.intent === 'navigate' && result.route) {
        window.location.hash = `#${result.route}`
        app.toast(`Opening ${result.route_label ?? result.route}`)
        setOpen(false)
        return
      }
      if (result.intent === 'ask' && result.question) {
        await askAgent(result.question)
        window.location.hash = '#thread'
        setOpen(false)
        return
      }
      setPreview(result)
      setAmounts(Object.fromEntries(result.lines.map((l) => [l.inventory_item_id, l.quantity])))
      setPicked({})
      setReason(result.reason)
      if (aloud) void say(result.blocked ? `${result.speak} ${result.blocked}` : `${result.speak} Confirm?`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }
  const talk = async () => {
    setPreview(null)
    setError(null)
    try {
      await understand(await speech.listen(), 'browser')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      field.current?.focus()
    }
  }
  const start = () => {
    setOpen(true)
    if (speech.supported) void talk()
  }

  const confirm = async () => {
    if (!preview) return
    setBusy(true)
    try {
      // Unclear items the person picked are learned as what this restaurant calls them, then counted in.
      const extra: { inventory_item_id: string; quantity: number }[] = []
      for (const u of preview.unmatched) {
        const itemId = picked[u.said]
        if (!itemId) continue
        await learnAlias(u.said, itemId).catch(() => {})
        const item = app.inventory.find((i) => i.id === itemId)
        const perPack = item?.pack_size && u.unit && item.pack_unit === u.unit ? item.pack_size : 1
        extra.push({ inventory_item_id: itemId, quantity: u.quantity * perPack })
      }
      const lines = [...preview.lines.map((l) => ({ inventory_item_id: l.inventory_item_id, quantity: amounts[l.inventory_item_id] ?? l.quantity })), ...extra]
      const done = await confirmSpeech(preview.id, lines, reason)
      app.toast(done.done)
      if (aloud) void say(done.done)
      app.refresh()
      setOpen(false)
      setPreview(null)
      if (done.claim_draft_id) window.location.hash = `#draft/${done.claim_draft_id}`
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const nothing = preview && preview.lines.length === 0 && Object.keys(picked).length === 0 && !(preview.intent === 'receive' && preview.all_arrived)
  return (
    <>
      <button className="voice-fab" aria-label="Speak a command" title="Speak: count, waste, a delivery or a question" onClick={start}>
        <Icon name="mic" size={22} />
      </button>
      {open && (
        <>
          <div className="overlay" onClick={close} />
          <div className="modal-wrap voice-wrap" onClick={(e) => e.target === e.currentTarget && close()}>
            <div className="modal voice" role="dialog" aria-modal="true" aria-labelledby="voice-title">
              <div className="modal-header voice-header">
                <h2 className="card-title" id="voice-title">
                  {preview ? TITLES[preview.intent] : speech.listening ? 'Listening…' : 'Say it, or type it'}
                </h2>
                <button className="btn btn-ghost btn-icon" aria-label="Close" onClick={close}>
                  <Icon name="close" size={16} />
                </button>
              </div>
              <div className="modal-body">
                {!preview && (
                  <>
                    {speech.listening ? (
                      <div className="voice-heard" role="status" aria-live="polite">
                        <span className="voice-pulse" aria-hidden="true" />
                        {speech.heard || 'Go ahead.'}
                      </div>
                    ) : (
                      <div className="muted small">
                        {speech.supported ? 'Tap the microphone and speak. ' : 'This browser has no voice typing, so type what you would say. '}
                        Try:
                        <ul className="voice-examples">
                          {EXAMPLES.map((e) => (
                            <li key={e}>
                              <button className="link-button" onClick={() => void understand(e, 'typed')}>
                                “{e}”
                              </button>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    <form
                      className="row voice-type"
                      onSubmit={(e) => {
                        e.preventDefault()
                        if (typed.trim()) void understand(typed.trim(), 'typed')
                      }}
                    >
                      <input ref={field} className="input" type="text" aria-label="Type a command" placeholder="mozzarella sixteen pounds" value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus={!speech.supported} />
                      <button className="btn btn-secondary" disabled={busy || !typed.trim()}>
                        Go
                      </button>
                    </form>
                  </>
                )}

                {preview && (
                  <>
                    <p className="voice-said">“{preview.transcript}”</p>
                    {preview.intent === 'unknown' ? (
                      <p className="muted">{preview.speak}</p>
                    ) : (
                      <>
                        {(preview.area || preview.supplier) && (
                          <div className="muted small">
                            {preview.area ? `In ${preview.area}` : `${preview.supplier}: ${preview.all_arrived ? 'everything arrived' : 'delivery'}${preview.lines.length ? ', except' : ''}`}
                          </div>
                        )}
                        <ul className="voice-lines">
                          {preview.lines.map((l) => (
                            <li key={l.inventory_item_id}>
                              <span className="voice-line-name">
                                {l.name}
                                {!l.sure && <span className="muted small"> · unit assumed</span>}
                              </span>
                              <input className="input input-sm par-input" type="number" min="0" step="any" aria-label={`${l.name}, in ${l.unit}`} value={amounts[l.inventory_item_id] ?? l.quantity} onChange={(e) => setAmounts({ ...amounts, [l.inventory_item_id]: Math.max(0, Number(e.target.value)) })} />
                              <span className="muted small">{l.unit}</span>
                            </li>
                          ))}
                          {preview.unmatched.map((u) => (
                            <li key={u.said}>
                              <span className="voice-line-name">
                                “{u.said}” <span className="muted small">· {qty(u.quantity)} {u.unit ?? ''}</span>
                              </span>
                              <select className="select input-sm" aria-label={`Which item is ${u.said}`} value={picked[u.said] ?? ''} onChange={(e) => setPicked({ ...picked, [u.said]: e.target.value })}>
                                <option value="">Which item?</option>
                                {u.choices.map((c) => (
                                  <option key={c.id} value={c.id}>
                                    {c.name}
                                  </option>
                                ))}
                                <option disabled>──────</option>
                                {app.inventory
                                  .filter((i) => !u.choices.some((c) => c.id === i.id))
                                  .map((i) => (
                                    <option key={i.id} value={i.id}>
                                      {i.name}
                                    </option>
                                  ))}
                              </select>
                            </li>
                          ))}
                        </ul>
                        {preview.intent === 'waste' && (
                          <div className="chip-row" role="group" aria-label="Why">
                            {REASONS.map(([value, label]) => (
                              <button key={value} type="button" className="chip-button" aria-pressed={reason === value} onClick={() => setReason(value)}>
                                {label}
                              </button>
                            ))}
                          </div>
                        )}
                        {preview.blocked && (
                          <div className="callout callout-warning" role="status">
                            {preview.blocked}
                          </div>
                        )}
                      </>
                    )}
                  </>
                )}
                {error && (
                  <div className="callout callout-danger" role="alert">
                    {error}
                  </div>
                )}
              </div>
              <div className="modal-footer voice-footer">
                <label className="checkbox-row small muted">
                  <input type="checkbox" checked={aloud} onChange={(e) => setAloud(e.target.checked)} /> Read back aloud
                </label>
                <div className="row">
                  {speech.listening ? (
                    <button className="btn btn-secondary" onClick={speech.stop}>
                      Done talking
                    </button>
                  ) : (
                    speech.supported && (
                      <button className="btn btn-secondary" disabled={busy} onClick={() => void talk()}>
                        <Icon name="mic" size={16} /> {preview ? 'Say it again' : 'Speak'}
                      </button>
                    )
                  )}
                  {preview && preview.intent !== 'unknown' && (
                    <button className="btn btn-primary" disabled={busy || !!preview.blocked || !!nothing} onClick={() => void confirm()}>
                      {busy ? 'Saving…' : 'Confirm'}
                    </button>
                  )}
                </div>
              </div>
            </div>
          </div>
        </>
      )}
    </>
  )
}
