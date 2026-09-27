import { useState } from 'react'
import type { AppContext } from '../app/context'
import { ActionList } from '../components/ActionList'
import { ACTION_META } from '../components/actions'

export function ApprovalsPage({ app }: { app: AppContext }) {
  const [filter, setFilter] = useState<string>('all')

  // Only offer filters for kinds of proposal that are actually waiting.
  const kinds = Object.keys(ACTION_META).filter((type) => app.pendingActions.some((a) => a.action_type === type))
  const visible = filter === 'all' ? app.pendingActions : app.pendingActions.filter((a) => a.action_type === filter)

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Approvals</h1>
          <p className="page-subtitle">
            Nothing an agent proposes takes effect until you approve it. Every approval is recorded and can be reverted.
          </p>
        </div>
      </div>

      <section className="card">
        <div className="card-header">
          <h2 className="card-title">
            {app.pendingActions.length} waiting
          </h2>
          {kinds.length > 1 && (
            <div className="segmented" role="group" aria-label="Filter proposals">
              <button aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>
                All
              </button>
              {kinds.map((kind) => (
                <button key={kind} aria-pressed={filter === kind} onClick={() => setFilter(kind)}>
                  {ACTION_META[kind].label} · {app.pendingActions.filter((a) => a.action_type === kind).length}
                </button>
              ))}
            </div>
          )}
        </div>
        <ActionList app={app} actions={visible} />
      </section>
    </main>
  )
}
