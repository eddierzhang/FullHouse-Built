import { type KeyboardEvent, type TouchEvent, useEffect, useRef, useState } from 'react'
import type { AgentAction } from '../api/client'
import type { AppContext } from '../app/context'
import { relativeTime } from '../lib/format'
import { ActionBody } from './ActionBody'
import { type Draft, ActionInputs, inputsFor } from './ActionList'
import { Icon } from './Icon'
import { actionDetail, actionMeta, actionTitle } from './actions'

/** How far a finger must travel to count as a swipe. */
const SWIPE_PX = 70

/**
 * The inbox, one card at a time: the proposal, why, and two big buttons. Approve
 * carries it out; Not now sends it to the back of the queue for this visit.
 * Swipe left for Not now. From a keyboard: Enter approves, Esc skips, Backspace
 * brings the last skipped card back. Built for a phone held in one hand.
 */
export function ApprovalDeck({ app, actions, onChanged }: { app: AppContext; actions: AgentAction[]; onChanged?: () => void }) {
  const [skipped, setSkipped] = useState<string[]>([])
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [doneHere, setDoneHere] = useState(0)
  const [offset, setOffset] = useState(0)
  const [leaving, setLeaving] = useState<'left' | 'right' | null>(null)
  const touchStart = useRef<number | null>(null)
  const cardRef = useRef<HTMLDivElement>(null)

  const ordered = [...actions.filter((a) => !skipped.includes(a.id)), ...skipped.map((id) => actions.find((a) => a.id === id)).filter((a): a is AgentAction => !!a)]
  const current = ordered[0] ?? null
  const skippedWaiting = skipped.filter((id) => actions.some((a) => a.id === id))
  // Fresh cards count up past the ones set aside; once everything is set aside the deck cycles through them.
  const position = current ? (skippedWaiting.includes(current.id) ? skippedWaiting.indexOf(current.id) + 1 : skippedWaiting.length + 1) : 0

  useEffect(() => {
    cardRef.current?.focus({ preventScroll: true })
  }, [current?.id])

  if (!current) {
    return (
      <div className="deck deck-done" role="status">
        <div className="deck-done-mark">
          <Icon name="approvals" size={28} />
        </div>
        <h2 className="deck-title">All clear</h2>
        <p className="muted">
          {doneHere > 0 ? `${doneHere} decided just now. ` : ''}
          Nothing is waiting on you.
        </p>
        {skippedWaiting.length > 0 && (
          <button className="btn btn-secondary" onClick={() => setSkipped([])}>
            Show the {skippedWaiting.length} you set aside
          </button>
        )}
      </div>
    )
  }

  const meta = actionMeta(current)
  const agent = app.agentsById.get(current.agent_definition_id)
  const draft = drafts[current.id] ?? { price: '', endsAt: '' }
  const setDraft = (patch: Partial<Draft>) => setDrafts({ ...drafts, [current.id]: { ...draft, ...patch } })
  const { blocked, needsPrice, priceOk, overrides } = inputsFor(current, draft)
  const busy = app.busyActionId === current.id
  const canApprove = app.canManage && !busy && !blocked && priceOk

  const skip = () => {
    if (ordered.length <= 1 && skipped.includes(current.id)) return
    setLeaving('left')
    window.setTimeout(() => {
      setSkipped((s) => [...s.filter((id) => id !== current.id), current.id])
      setLeaving(null)
      setOffset(0)
    }, 160)
  }
  const unskip = () => {
    const last = skippedWaiting[skippedWaiting.length - 1]
    if (last) setSkipped((s) => s.filter((id) => id !== last))
  }
  const approve = async () => {
    if (!canApprove) return
    if (await app.approve(current, Object.keys(overrides).length ? overrides : undefined)) {
      setDoneHere((n) => n + 1)
      setDrafts((d) => {
        const next = { ...d }
        delete next[current.id]
        return next
      })
      onChanged?.()
    }
  }
  const reject = async () => {
    if (!app.canManage || busy) return
    if (await app.reject(current)) {
      setDoneHere((n) => n + 1)
      onChanged?.()
    }
  }

  const onKey = (event: KeyboardEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement
    if (['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON', 'A'].includes(target.tagName)) return
    if (event.key === 'Enter') {
      event.preventDefault()
      void approve()
    } else if (event.key === 'Escape') {
      event.preventDefault()
      skip()
    } else if (event.key === 'Backspace') {
      event.preventDefault()
      unskip()
    }
  }
  const onTouchStart = (event: TouchEvent) => {
    touchStart.current = event.touches[0].clientX
  }
  const onTouchMove = (event: TouchEvent) => {
    if (touchStart.current === null) return
    const dx = event.touches[0].clientX - touchStart.current
    setOffset(Math.max(-140, Math.min(60, dx)))
  }
  const onTouchEnd = () => {
    if (touchStart.current === null) return
    const dx = offset
    touchStart.current = null
    if (dx <= -SWIPE_PX) skip()
    else setOffset(0)
  }

  return (
    <div className="deck">
      <div className="deck-progress" aria-live="polite">
        <span>
          {position} of {actions.length}
        </span>
        {skippedWaiting.length > 0 && (
          <button type="button" className="link-button" onClick={unskip}>
            · {skippedWaiting.length} set aside
          </button>
        )}
      </div>
      <div
        ref={cardRef}
        className={`deck-card${leaving ? ` deck-leaving-${leaving}` : ''}`}
        style={offset ? { transform: `translateX(${offset}px) rotate(${offset / 40}deg)`, transition: 'none' } : undefined}
        role="group"
        aria-label={`Proposal ${position} of ${actions.length}: ${actionTitle(current)}`}
        tabIndex={0}
        onKeyDown={onKey}
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
      >
        <div className="row wrap">
          <span className={`badge ${meta.badge}`}>{meta.label}</span>
          <span className="muted small">
            {agent?.name ?? 'Agent'} · {relativeTime(current.created_at)}
          </span>
        </div>
        <h2 className="deck-title">{actionTitle(current)}</h2>
        <p className="deck-why">{actionDetail(current)}</p>
        <ActionBody action={current} />
        {blocked && <div className="negative small">Nothing can carry this out yet. Reject it.</div>}
        {!blocked && <ActionInputs action={current} draft={draft} setDraft={setDraft} size="lg" />}
        <button type="button" className="link-button deck-details" onClick={() => app.openAction(current)}>
          Details and the run behind it
        </button>
      </div>
      <div className="deck-actions">
        <button type="button" className="btn btn-secondary deck-btn" onClick={skip} aria-label="Not now: set this aside and show the next">
          Not now
        </button>
        {app.canManage ? (
          <button type="button" className="btn btn-primary deck-btn" disabled={!canApprove} onClick={() => void approve()} title={needsPrice && !priceOk ? 'Enter the new price first' : undefined}>
            {busy ? 'Working…' : 'Approve'}
          </button>
        ) : (
          <span className="muted small">Owners and admins approve.</span>
        )}
      </div>
      {app.canManage && (
        <div className="deck-secondary">
          <button type="button" className="link-button negative" disabled={busy} onClick={() => void reject()}>
            Reject this one
          </button>
          <span className="muted small deck-hint">Enter approves · Esc sets aside · swipe left</span>
        </div>
      )}
    </div>
  )
}
