import { useState } from 'react'
import type { AgentAction } from '../api/client'
import type { AppContext } from '../app/context'
import { relativeTime } from '../lib/format'
import { Icon } from './Icon'
import { Empty } from './PageState'
import { actionDetail, actionMeta, actionTitle, isPromotion } from './actions'

type Props = {
  app: AppContext
  actions: AgentAction[]
  /** Compact rows for the overview; the approvals page shows everything. */
  compact?: boolean
  /** Called after an approval or rejection goes through, so the page can reload. */
  onChanged?: () => void
}

type Draft = { price: string; endsAt: string }

/**
 * Pending proposals with approve/reject.
 *
 * Asks for anything a proposal left blank -- a promotion the agent described
 * in words but never priced -- before Approve is enabled, instead of letting
 * the approval fail afterwards.
 */
export function ActionList({ app, actions, compact = false, onChanged }: Props) {
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})

  if (actions.length === 0) {
    return <Empty title="Nothing to review">New recommendations from the Assistant Manager appear here.</Empty>
  }

  return (
    <ul className="list">
      {actions.map((action) => {
        const meta = actionMeta(action)
        const agent = app.agentsById.get(action.agent_definition_id)
        const draft = drafts[action.id] ?? { price: '', endsAt: '' }
        const setDraft = (patch: Partial<Draft>) => setDrafts({ ...drafts, [action.id]: { ...draft, ...patch } })

        const blocked = action.appliable === false
        const needsPrice = (action.needs_input ?? []).includes('new_price')
        const priceOk = !needsPrice || (draft.price.trim() !== '' && Number(draft.price) >= 0)
        const offersEnd = isPromotion(action) && !action.payload.promo_ends_at
        const busy = app.busyActionId === action.id

        const overrides: Record<string, unknown> = {}
        if (needsPrice) overrides.new_price = Number(draft.price)
        if (offersEnd && draft.endsAt) overrides.promo_ends_at = new Date(draft.endsAt).toISOString()

        return (
          <li key={action.id} className="list-item" style={{ alignItems: 'flex-start' }}>
            <div className="avatar">
              <Icon name={meta.icon} size={16} />
            </div>

            <div className="list-item-main">
              <div className="row wrap">
                <span className={`badge ${meta.badge}`}>{meta.label}</span>
                <span className="muted small">
                  {agent?.name ?? 'Agent'} · {relativeTime(action.created_at)}
                </span>
              </div>
              <div className="strong" style={{ marginTop: 6 }}>
                {actionTitle(action)}
              </div>
              {!compact && <div className="muted small" style={{ marginTop: 2 }}>{actionDetail(action)}</div>}
              {!compact && action.action_type === 'recipe' && Array.isArray(action.payload.lines) && (
                <ul className="small" style={{ margin: '8px 0 0 16px', padding: 0 }} aria-label="Recipe lines">
                  {(action.payload.lines as { ingredient_text: string; item_name: string | null; qty: number; unit: string; matched: boolean }[]).map((l, i) => (
                    <li key={i} className={l.matched ? undefined : 'muted'}>
                      {l.qty} {l.unit} {l.item_name ?? l.ingredient_text}
                      {!l.matched && ' (not on the invoices; left out unless you add it later)'}
                    </li>
                  ))}
                </ul>
              )}
              {!compact && action.action_type === 'menu_reprice' && (
                <div className="small" style={{ marginTop: 6 }}>
                  Plate cost {action.payload.plate_cost_before != null ? `$${Number(action.payload.plate_cost_before).toFixed(2)} → ` : ''}
                  ${Number(action.payload.plate_cost_after ?? 0).toFixed(2)}
                  {action.payload.current_price != null && ` · sells at $${Number(action.payload.current_price).toFixed(2)}`}
                </div>
              )}

              {blocked && (
                <div className="negative small" style={{ marginTop: 6 }}>
                  Nothing can carry this out yet — reject it.
                </div>
              )}

              {!blocked && (needsPrice || offersEnd) && (
                <div className="row wrap" style={{ marginTop: 10 }}>
                  {needsPrice && (
                    <label className="field" style={{ width: 130 }}>
                      <span className="field-label">New price</span>
                      <input
                        className="input input-sm"
                        inputMode="decimal"
                        placeholder="0.00"
                        value={draft.price}
                        onChange={(e) => setDraft({ price: e.target.value })}
                      />
                    </label>
                  )}
                  {offersEnd && (
                    <label className="field" style={{ width: 200 }}>
                      <span className="field-label">Ends (optional)</span>
                      <input
                        className="input input-sm"
                        type="datetime-local"
                        value={draft.endsAt}
                        onChange={(e) => setDraft({ endsAt: e.target.value })}
                      />
                    </label>
                  )}
                </div>
              )}
            </div>

            <div className="row" style={{ alignSelf: 'center' }}>
              {!compact && (
                <button className="btn btn-ghost btn-sm" onClick={() => app.openAction(action)}>
                  Details
                </button>
              )}
              {app.canManage && (
                <>
                  <button
                    className="btn btn-danger btn-sm"
                    disabled={busy}
                    onClick={async () => {
                      if (await app.reject(action)) onChanged?.()
                    }}
                  >
                    Reject
                  </button>
                  <button
                    className="btn btn-primary btn-sm"
                    disabled={busy || blocked || !priceOk}
                    title={needsPrice && !priceOk ? 'Enter the new price first' : undefined}
                    onClick={async () => {
                      if (await app.approve(action, Object.keys(overrides).length ? overrides : undefined)) {
                        setDrafts((current) => {
                          const next = { ...current }
                          delete next[action.id]
                          return next
                        })
                        onChanged?.()
                      }
                    }}
                  >
                    {busy ? 'Working…' : 'Approve'}
                  </button>
                </>
              )}
            </div>
          </li>
        )
      })}
    </ul>
  )
}
