import { type FormEvent, useEffect, useState } from 'react'
import { sendFeedback } from '../api/client'
import { Icon } from './Icon'

/**
 * "Something wrong? Tell us" from anywhere in the app. Notes which page the
 * person was on, so a report like "this doesn't add up" can be followed up.
 */
export function FeedbackButton({ onSent }: { onSent: () => void }) {
  const [open, setOpen] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!message.trim()) return setError('Write what happened or what you’d like.')
    setBusy(true)
    setError(null)
    try {
      await sendFeedback(message.trim(), window.location.hash.replace(/^#\/?/, '') || 'overview')
      setMessage('')
      setOpen(false)
      onSent()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <button className="btn btn-ghost btn-sm feedback-button" onClick={() => setOpen(true)}>
        <Icon name="info" size={14} /> Send feedback
      </button>
      {open && (
        <>
          <div className="overlay" onClick={() => setOpen(false)} />
          <div className="modal-wrap" onClick={(e) => e.target === e.currentTarget && setOpen(false)}>
            <form className="modal" onSubmit={submit} role="dialog" aria-modal="true" aria-labelledby="feedback-title">
              <div className="modal-header">
                <h2 id="feedback-title" className="card-title">
                  Send feedback
                </h2>
                <p className="card-subtitle">Something broken, confusing, or missing? Say what happened, in your own words.</p>
              </div>
              <div className="modal-body">
                <label className="field">
                  <span className="field-label">What happened?</span>
                  <textarea
                    className="textarea"
                    autoFocus
                    maxLength={2000}
                    placeholder="e.g. I logged a delivery but the count on Today didn’t change"
                    value={message}
                    onChange={(e) => (setMessage(e.target.value), setError(null))}
                  />
                </label>
                {error && (
                  <div className="auth-error" role="alert">
                    {error}
                  </div>
                )}
              </div>
              <div className="modal-footer">
                <button type="button" className="btn btn-ghost" onClick={() => setOpen(false)} disabled={busy}>
                  Cancel
                </button>
                <button type="submit" className="btn btn-primary" disabled={busy}>
                  {busy ? 'Sending…' : 'Send'}
                </button>
              </div>
            </form>
          </div>
        </>
      )}
    </>
  )
}
