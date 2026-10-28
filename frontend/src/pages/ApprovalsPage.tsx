import { useEffect, useState } from 'react'
import { listActions } from '../api/client'
import type { AppContext } from '../app/context'
import { ActionList } from '../components/ActionList'
import { ApprovalDeck } from '../components/ApprovalDeck'
import { Empty, Freshness, Loaded } from '../components/PageState'
import { ACTION_META } from '../components/actions'
import { useLoad } from '../lib/useLoad'

type Mode = 'deck' | 'list'
const MODE_KEY = 'approvals-mode'

/** One at a time on a phone, where decisions get made standing up; the list by default on a desktop, remembered either way. */
function initialMode(): Mode {
  try {
    const saved = window.localStorage.getItem(MODE_KEY)
    if (saved === 'deck' || saved === 'list') return saved
  } catch {
    /* private window */
  }
  return window.matchMedia('(max-width: 860px)').matches ? 'deck' : 'list'
}

export function ApprovalsPage({ app }: { app: AppContext }) {
  const [filter, setFilter] = useState<string>('all')
  const [mode, setMode] = useState<Mode>(initialMode)
  useEffect(() => {
    try {
      window.localStorage.setItem(MODE_KEY, mode)
    } catch {
      /* fine */
    }
  }, [mode])
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
            One inbox for everything the agent proposes: reorders, recipes, agreed prices, dates, prices. Nothing changes until an owner or admin
            approves it, and approved changes can be undone from <a href="#history">History & undo</a>.
          </p>
        </div>
        <Freshness loads={[load]} />
        <div className="segmented" role="group" aria-label="How to review">
          <button aria-pressed={mode === 'deck'} onClick={() => setMode('deck')}>
            One at a time
          </button>
          <button aria-pressed={mode === 'list'} onClick={() => setMode('list')}>
            List
          </button>
        </div>
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
          ) : mode === 'deck' ? (
            <ApprovalDeck app={app} actions={actions} onChanged={load.reload} />
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
