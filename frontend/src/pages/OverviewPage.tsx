import { type Forecast, fetchForecast, fetchProfitSummary, listRuns } from '../api/client'
import type { AppContext } from '../app/context'
import { ActionList } from '../components/ActionList'
import { ForecastCard } from '../components/ForecastCard'
import { GettingStarted } from '../components/GettingStarted'
import { Icon } from '../components/Icon'
import { Empty, Freshness, LoadError, Loaded } from '../components/PageState'
import { RunStatusBadge } from '../components/RunStatusBadge'
import { greeting, money, pct, relativeTime } from '../lib/format'
import { type Load, useLoad } from '../lib/useLoad'
import { openStatus } from '../restaurant/hours'

export function OverviewPage({ app }: { app: AppContext }) {
  // Money is for owners and admins only: staff never ask for it. Resolving
  // to undefined (not null) lets their copy count as loaded.
  const weekLoad = useLoad(() => (app.canManage ? fetchProfitSummary(7) : Promise.resolve(undefined)), [app.canManage])
  const forecastLoad = useLoad(() => (app.canManage ? fetchForecast(7) : Promise.resolve(undefined)), [app.canManage])
  const runsLoad = useLoad(() => listRuns(), [app.pendingActions.length])
  const week = weekLoad.data

  const lowStock = app.inventory.filter((i) => i.quantity_on_hand <= i.reorder_threshold)
  const today = new Date().toLocaleDateString('en-US', {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
    timeZone: app.restaurant.timezone,
  })
  const status = openStatus(app.restaurant)

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">{greeting(new Date(), app.restaurant.timezone)}</h1>
          <p className="page-subtitle">
            {today} · Here’s how {app.restaurant.name ?? 'the restaurant'} is doing.{' '}
            <span className={`open-status${status.open ? ' is-open' : ''}`}>{status.label}</span>
          </p>
        </div>
        <Freshness loads={app.canManage ? [runsLoad, weekLoad, forecastLoad] : [runsLoad]} />
      </div>

      <div className="stack">
        <GettingStarted app={app} />
        {app.canManage && weekLoad.status === 'error' && (
          <LoadError what="the last 7 days’ figures" error={weekLoad.error ?? ''} onRetry={weekLoad.reload} />
        )}
        <section className="grid grid-stats" aria-label="Key figures">
          {app.canManage && (
            <>
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
            </>
          )}
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
                  <h2 className="card-title">Recent agent activity</h2>
                  <div className="card-subtitle">What your agents did lately</div>
                </div>
                <a className="btn btn-ghost btn-sm" href="#runs">
                  All agent activity <Icon name="arrowRight" size={14} />
                </a>
              </div>
              <Loaded load={runsLoad} what="recent agent activity" placeholderHeight={200}>
                {(runs) => {
                  const recentRuns = runs.filter((r) => !r.parent_run_id).slice(0, 5)
                  return recentRuns.length === 0 ? (
                    <Empty title="No agent activity yet">
                      {app.canManage
                        ? 'Start the Assistant Manager on something with “New task”.'
                        : 'What the Assistant Manager does shows up here.'}
                    </Empty>
                  ) : (
                    <ul className="list">
                      {recentRuns.map((run) => (
                        <li key={run.id} className="list-item">
                          <RunStatusBadge status={run.status} />
                          <div className="list-item-main">
                            <div className="strong truncate">
                              {app.agentsById.get(run.agent_definition_id)?.name ?? 'Agent'}
                            </div>
                            <div className="muted small truncate">{run.output_summary || run.error || run.input}</div>
                          </div>
                          <span className="muted small">{relativeTime(run.created_at)}</span>
                        </li>
                      ))}
                    </ul>
                  )
                }}
              </Loaded>
            </section>
          </div>

          <div className="stack">
            {app.canManage && <ForecastCard load={forecastLoad as Load<Forecast>} />}

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
