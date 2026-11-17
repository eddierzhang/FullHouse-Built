import { useEffect, useMemo, useState } from 'react'
import type { InventoryItem } from '../api/client'
import { recordWaste, type WasteResult } from '../api/stock'
import { money, qty } from '../lib/format'

const REASONS: [string, string][] = [
  ['spoiled', 'Spoiled'],
  ['dropped', 'Dropped'],
  ['overcooked', 'Overcooked'],
  ['prep', 'Prep loss'],
  ['comped', 'Comped'],
  ['staff_meal', 'Staff meal'],
  ['other', 'Other'],
]

/** Something thrown away: what, how much, why. Three taps; a double tap or a retry is recorded once. */
export function WasteDialog({ open, items, onClose, onDone }: { open: boolean; items: InventoryItem[]; onClose: () => void; onDone: (result: WasteResult) => void }) {
  const sorted = useMemo(() => [...items].sort((a, b) => a.name.localeCompare(b.name)), [items])
  const [itemId, setItemId] = useState('')
  const [amount, setAmount] = useState('')
  const [reason, setReason] = useState('spoiled')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // One key per time the dialog opens: the server answers a repeat of the same entry with the first.
  const [key, setKey] = useState('')

  useEffect(() => {
    if (!open) return
    setItemId((held) => held || sorted[0]?.id || '')
    setAmount('')
    setNote('')
    setError(null)
    setKey(`waste-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`)
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!open) return null
  const item = sorted.find((i) => i.id === itemId)
  const quantity = Number(amount)
  const valid = !!item && Number.isFinite(quantity) && quantity > 0

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!valid || !item) return
    setBusy(true)
    setError(null)
    try {
      onDone(await recordWaste({ inventory_item_id: item.id, quantity, reason, note: note.trim() || undefined }, key))
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="overlay" onClick={onClose} />
      <div className="modal-wrap" onClick={(e) => e.target === e.currentTarget && onClose()}>
        <form className="modal" onSubmit={submit} role="dialog" aria-modal="true" aria-labelledby="waste-title">
          <div className="modal-header">
            <h2 className="card-title" id="waste-title">
              Log waste
            </h2>
            <div className="card-subtitle">What was thrown away, and why. It comes off the stock figures.</div>
          </div>
          <div className="modal-body">
            <div className="field">
              <label className="field-label" htmlFor="waste-item">
                Item
              </label>
              <select id="waste-item" className="select" value={itemId} onChange={(e) => setItemId(e.target.value)}>
                {sorted.map((i) => (
                  <option key={i.id} value={i.id}>
                    {i.name} · {qty(i.quantity_on_hand)} {i.unit} on hand
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label className="field-label" htmlFor="waste-amount">
                How much{item ? `, in ${item.unit}` : ''}
              </label>
              <input id="waste-amount" className="input count-input" type="number" inputMode="decimal" min="0" step="any" value={amount} onChange={(e) => setAmount(e.target.value)} autoFocus />
              {valid && item && <div className="field-hint">About {money(quantity * item.unit_cost, true)} at what you last paid.</div>}
            </div>
            <fieldset className="field">
              <legend className="field-label">Why</legend>
              <div className="chip-row">
                {REASONS.map(([value, label]) => (
                  <button key={value} type="button" className="chip-button" aria-pressed={reason === value} onClick={() => setReason(value)}>
                    {label}
                  </button>
                ))}
              </div>
            </fieldset>
            <div className="field">
              <label className="field-label" htmlFor="waste-note">
                Note (optional)
              </label>
              <input id="waste-note" className="input" type="text" maxLength={160} value={note} onChange={(e) => setNote(e.target.value)} />
            </div>
            {error && (
              <div className="callout callout-danger" role="alert">
                {error}
              </div>
            )}
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-ghost" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={!valid || busy}>
              {busy ? 'Saving…' : 'Log waste'}
            </button>
          </div>
        </form>
      </div>
    </>
  )
}
