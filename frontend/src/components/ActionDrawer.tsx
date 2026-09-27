import { useEffect } from 'react'
import type { AgentAction } from '../api/client'
import type { AppContext } from '../app/context'
import { dateTime } from '../lib/format'
import { Icon } from './Icon'
import { actionDetail, actionMeta, actionTitle } from './actions'

export function ActionDrawer({ app, action, onClose }: { app: AppContext; action: AgentAction | null; onClose: () => void }) {
  useEffect(() => {
    if (!action) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [action, onClose])

  if (!action) return null
  const meta = actionMeta(action)
  const agent = app.agentsById.get(action.agent_definition_id)
  const needsInput = (action.needs_input ?? []).length > 0

  return (
    <>
      <div className="overlay" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">
        <div className="drawer-header">
          <div>
            <span className={`badge ${meta.badge}`}>{meta.label}</span>
            <h2 id="drawer-title" className="page-title" style={{ fontSize: 18, marginTop: 10 }}>
              {actionTitle(action)}
            </h2>
          </div>
          <button className="btn btn-ghost btn-icon" onClick={onClose} aria-label="Close">
            <Icon name="close" />
          </button>
        </div>

        <div className="drawer-body stack">
          <p style={{ margin: 0 }} className="muted">
            {actionDetail(action)}
          </p>
          <dl className="definition-list">
            <dt>Proposed by</dt>
            <dd>{agent?.name ?? 'Agent'}</dd>
            <dt>Submitted</dt>
            <dd>{dateTime(action.created_at)}</dd>
            <dt>Status</dt>
            <dd>{action.status}</dd>
          </dl>
          <div>
            <div className="field-label" style={{ marginBottom: 6 }}>
              Payload
            </div>
            <pre className="mono card" style={{ margin: 0, padding: 12, whiteSpace: 'pre-wrap' }}>
              {JSON.stringify(action.payload, null, 2)}
            </pre>
          </div>
          {needsInput && (
            <div className="callout callout-info">
              <Icon name="info" />
              <div className="callout-body">
                This proposal needs a value before it can be approved. Enter it in the approvals list.
              </div>
            </div>
          )}
        </div>

        {action.status === 'pending' && (
          <div className="drawer-footer">
            <button
              className="btn btn-danger"
              disabled={app.busyActionId === action.id}
              onClick={async () => (await app.reject(action)) && onClose()}
            >
              Reject
            </button>
            <button
              className="btn btn-primary"
              disabled={app.busyActionId === action.id || needsInput || action.appliable === false}
              onClick={async () => (await app.approve(action)) && onClose()}
            >
              Approve
            </button>
          </div>
        )}
      </aside>
    </>
  )
}
