import { useEffect, useState } from 'react'
import { type AgentRun, type ProfitSummary, fetchProfitSummary, listRuns } from '../api/client'
import type { AppContext } from '../app/context'
import { ActionList } from '../components/ActionList'
import { ForecastCard } from '../components/ForecastCard'
import { Icon } from '../components/Icon'
import { RunStatusBadge } from '../components/RunStatusBadge'
import { greeting, money, pct, relativeTime } from '../lib/format'

export function OverviewPage({ app }: { app: AppContext }) {
  const [week, setWeek] = useState<ProfitSummary | null>(null)
  const [runs, setRuns] = useState<AgentRun[]>([])

  useEffect(() => {
    fetchProfitSummary(7).then(setWeek).catch(() => {})
    listRuns().then(setRuns).catch(() => {})
  }, [app.pendingActions.length])

  const lowStock = app.inventory.filter((i) => i.quantity_on_hand <= i.reorder_threshold)
  const recentRuns = runs.filter((r) => !r.parent_run_id).slice(0, 5)
  const today = new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">{greeting()}</h1>
          <p className="page-subtitle">{today} · Here’s how the restaurant is doing.</p>
        </div>
      </div>

      <div className="stack">
        <section className="grid grid-stats" aria-label="Key figures">
          <div className="card stat">
            <div className="stat-label">Revenue, last 7 days</div>
            <div className="stat-value">{week ? money(week.revenue) : '—'}</div>
            <div className="stat-meta">{week ? `${week.orders} orders` : ' '}</div>
          </div>
          <div className="card stat">
            <div className="stat-label">Gross margin</div>
            <div className="stat-value">{week ? pct(week.margin_pct) : '—'}</div>
            <div className="stat-meta">{week ? `${money(week.gross_profit)} gross profit` : ' '}</div>
          </div>
          <a className={`card stat${app.pendingActions.length ? ' attention' : ''}`} href="#approvals" style={{ color: 'inherit', textDecoration: 'none' }}>
            <div className="stat-label">Needs review</div>
            <div className="stat-value">{app.pendingActions.length}</div>
            <div className="stat-meta">{app.pendingActions.length ? 'agent proposals waiting' : 'all clear'}</div>
          </a>
          <a className={`card stat${lowStock.length ? ' attention' : ''}`} href="#inventory" style={{ color: 'inherit', textDecoration: 'none' }}>
            <div className="stat-label">Low stock</div>
            <div className="stat-value">{lowStock.length}</div>
            <div className="stat-meta">
              {lowStock.length ? lowStock.map((i) => i.name).slice(0, 2).join(', ') : `of ${app.inventory.length} items`}
            </div>
          </a>
        </section>

        <div className="grid grid-main">
          <div className="stack">
            <section className="card">
              <div className="card-header">
                <div>
                  <h2 className="card-title">Needs your review</h2>
                  <div className="card-subtitle">Proposals from your agents, newest first</div>
                </div>
                {app.pendingActions.length > 0 && (
                  <a className="btn btn-ghost btn-sm" href="#approvals">
                    View all <Icon name="arrowRight" size={14} />
                  </a>
                )}
              </div>
              <ActionList app={app} actions={app.pendingActions.slice(0, 4)} compact />
            </section>

            <section className="card">
              <div className="card-header">
                <div>
                  <h2 className="card-title">Recent agent runs</h2>
                  <div className="card-subtitle">What your agents did lately</div>
                </div>
                <a className="btn btn-ghost btn-sm" href="#runs">
                  All runs <Icon name="arrowRight" size={14} />
                </a>
              </div>
              {recentRuns.length === 0 ? (
                <div className="empty">
                  <div className="empty-title">No runs yet</div>
                  <div className="small">Start one with “New task”.</div>
                </div>
              ) : (
                <ul className="list">
                  {recentRuns.map((run) => (
                    <li key={run.id} className="list-item">
                      <RunStatusBadge status={run.status} />
                      <div className="list-item-main">
                        <div className="strong truncate">{app.agentsById.get(run.agent_definition_id)?.name ?? 'Agent'}</div>
                        <div className="muted small truncate">{run.output_summary || run.error || run.input}</div>
                      </div>
                      <span className="muted small">{relativeTime(run.created_at)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>

          <div className="stack">
            <ForecastCard />

            <section className="card">
              <div className="card-header">
                <div>
                  <h2 className="card-title">Agents</h2>
                  <div className="card-subtitle">Schedules and open proposals</div>
                </div>
                <a className="btn btn-ghost btn-sm" href="#agents">
                  Manage <Icon name="arrowRight" size={14} />
                </a>
              </div>
              <ul className="list">
                {app.agents.map((agent) => {
                  const pending = app.pendingActions.filter((a) => a.agent_definition_id === agent.id).length
                  return (
                    <li key={agent.id} className="list-item">
                      <div className="list-item-main">
                        <div className="strong truncate">{agent.name}</div>
                        <div className="muted small">
                          {!agent.enabled
                            ? 'Paused'
                            : agent.next_run_at
                              ? `Next run ${relativeTime(agent.next_run_at)}`
                              : 'Runs on request'}
                        </div>
                      </div>
                      {pending > 0 && <span className="badge badge-danger">{pending} to review</span>}
                    </li>
                  )
                })}
              </ul>
            </section>
          </div>
        </div>
      </div>
    </main>
  )
}
