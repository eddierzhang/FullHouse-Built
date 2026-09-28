import { useState } from 'react'
import { listActions } from '../api/client'
import type { AppContext } from '../app/context'
import { ActionList } from '../components/ActionList'
import { Empty, Freshness, Loaded } from '../components/PageState'
import { ACTION_META } from '../components/actions'
import { useLoad } from '../lib/useLoad'

export function ApprovalsPage({ app }: { app: AppContext }) {
  const [filter, setFilter] = useState<string>('all')
  // Loaded here rather than read from the shell, so a failed load says so
  // instead of looking like "nothing to review". Refetches whenever the
  // shell's own poll sees the waiting list change.
  const pendingKey = app.pendingActions.map((a) => a.id).join(',')
  const load = useLoad(() => listActions('pending'), [pendingKey])
  const pending = load.data ?? []

  // Only offer filters for kinds of proposal that are actually waiting.
  const kinds = Object.keys(ACTION_META).filter((type) => pending.some((a) => a.action_type === type))
  const visible = filter === 'all' ? pending : pending.filter((a) => a.action_type === filter)

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Recommendations</h1>
          <p className="page-subtitle">
            What the Assistant Manager suggests. Nothing changes until an owner or admin approves it, and approved changes
            can be undone from <a href="#history">History & undo</a>.
          </p>
        </div>
        <Freshness loads={[load]} />
      </div>

      <Loaded load={load} what="recommendations" placeholderHeight={240}>
        {(actions) =>
          actions.length === 0 ? (
            <Empty
              title="Nothing to review"
              action={
                app.canManage && (
                  <button className="btn btn-primary" onClick={() => app.newTask()}>
                    Ask the Assistant Manager
                  </button>
                )
              }
            >
              When the Assistant Manager suggests a reorder, a price change or a schedule fix, it waits here for approval.
            </Empty>
          ) : (
            <section className="card">
              <div className="card-header">
                <h2 className="card-title">{actions.length} waiting</h2>
                {kinds.length > 1 && (
                  <div className="segmented" role="group" aria-label="Filter proposals">
                    <button aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>
                      All
                    </button>
                    {kinds.map((kind) => (
                      <button key={kind} aria-pressed={filter === kind} onClick={() => setFilter(kind)}>
                        {ACTION_META[kind].label} · {actions.filter((a) => a.action_type === kind).length}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <ActionList app={app} actions={visible} onChanged={load.reload} />
            </section>
          )
        }
      </Loaded>
    </main>
  )
}
