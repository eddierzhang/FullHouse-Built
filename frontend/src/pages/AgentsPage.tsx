import { useState } from 'react'
import { type AgentDefinition, listAgentDefinitions, updateAgentDefinition } from '../api/client'
import type { AppContext } from '../app/context'
import { Icon } from '../components/Icon'
import { Empty, Freshness, Loaded } from '../components/PageState'
import { SCHEDULE_PRESETS, dateTime, initials, relativeTime, scheduleLabel } from '../lib/format'
import { useLoad } from '../lib/useLoad'

const CUSTOM = '__custom__'

function AgentRow({ agent, app, onSaved }: { agent: AgentDefinition; app: AppContext; onSaved: () => void }) {
  const isPreset = SCHEDULE_PRESETS.some((p) => p.cron === agent.schedule_cron)
  const [mode, setMode] = useState(isPreset ? agent.schedule_cron ?? '' : CUSTOM)
  const [custom, setCustom] = useState(isPreset ? '' : agent.schedule_cron ?? '')
  const [busy, setBusy] = useState(false)
  const pending = app.pendingActions.filter((a) => a.agent_definition_id === agent.id).length

  const save = async (patch: { schedule_cron?: string | null; enabled?: boolean }, message: string) => {
    setBusy(true)
    try {
      await updateAgentDefinition(agent.key, patch)
      app.toast(message)
      onSaved()
      app.refresh()
    } catch (err) {
      app.toast('Couldn’t update the schedule', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const chooseSchedule = (value: string) => {
    setMode(value)
    if (value === CUSTOM) return
    const cron = value || null
    void save({ schedule_cron: cron }, cron ? `${agent.name}: ${scheduleLabel(cron)}` : `${agent.name} unscheduled`)
  }

  return (
    <li className="list-item" style={{ alignItems: 'flex-start', flexWrap: 'wrap' }}>
      <div className="avatar">{initials(agent.name)}</div>

      <div className="list-item-main" style={{ minWidth: 220 }}>
        <div className="row wrap">
          <span className="strong">{agent.name}</span>
          {!agent.enabled && <span className="badge badge-warning">Paused</span>}
          {pending > 0 && <span className="badge badge-danger">{pending} to review</span>}
        </div>
        <div className="muted small" style={{ marginTop: 2 }}>
          {agent.description}
        </div>
        <div className="faint small" style={{ marginTop: 6 }}>
          {agent.tool_allowlist.length ? `Covers ${agent.tool_allowlist.join(', ')}` : 'Covers inventory, suppliers, staff, the menu and profit'} ·
          Model: {app.runtime?.model ?? agent.model}
        </div>
      </div>

      <div className="stack" style={{ gap: 8, width: 280 }}>
        <label className="field">
          <span className="field-label">Schedule</span>
          <select className="select" value={mode} disabled={busy || !app.canManage} onChange={(e) => chooseSchedule(e.target.value)}>
            {SCHEDULE_PRESETS.map((p) => (
              <option key={p.label} value={p.cron ?? ''}>
                {p.label}
              </option>
            ))}
            <option value={CUSTOM}>Custom cron…</option>
          </select>
        </label>

        {mode === CUSTOM && (
          <div className="row">
            <input
              className="input mono"
              aria-label="Cron expression"
              placeholder="*/30 9-17 * * 1-5"
              value={custom}
              onChange={(e) => setCustom(e.target.value)}
            />
            <button
              className="btn btn-secondary"
              disabled={busy || !custom.trim()}
              onClick={() => save({ schedule_cron: custom.trim() }, `${agent.name} scheduled`)}
            >
              Save
            </button>
          </div>
        )}

        {mode === CUSTOM && agent.schedule_cron && !scheduleLabel(agent.schedule_cron).startsWith('Custom') && (
          <div className="small">{scheduleLabel(agent.schedule_cron)}</div>
        )}

        <div className="muted small">
          {!agent.enabled ? (
            'Paused — scheduled runs are skipped.'
          ) : agent.next_run_at ? (
            <span title={dateTime(agent.next_run_at)}>
              <Icon name="clock" size={12} /> Next run {relativeTime(agent.next_run_at)}
            </span>
          ) : (
            'Runs only when started by hand.'
          )}
        </div>
      </div>

      {app.canManage && (
        <div className="row" style={{ alignSelf: 'center' }}>
          <button
            className="btn btn-secondary btn-sm"
            disabled={busy}
            onClick={() => save({ enabled: !agent.enabled }, agent.enabled ? `${agent.name} paused` : `${agent.name} resumed`)}
          >
            {agent.enabled ? 'Pause' : 'Resume'}
          </button>
          <button className="btn btn-primary btn-sm" disabled={!agent.enabled} onClick={() => app.newTask(agent.key)}>
            <Icon name="play" size={12} /> Run now
          </button>
        </div>
      )}
    </li>
  )
}

// The main agent first, then any others alphabetically -- the order the shell uses.
const byRole = (a: AgentDefinition, b: AgentDefinition) =>
  a.role === b.role ? a.name.localeCompare(b.name) : a.role === 'boss' ? -1 : 1

export function AgentsPage({ app }: { app: AppContext }) {
  const load = useLoad(() => listAgentDefinitions().then((agents) => [...agents].sort(byRole)), [])
  const stockCheck = app.restaurant.inventory_settings?.daily_stock_check ?? null

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Assistant Manager</h1>
          <p className="page-subtitle">
            One agent runs the whole restaurant — stock, suppliers, staff, the menu and the numbers — and proposes changes
            for you to approve. Schedules run on {(app.runtime?.schedule_timezone ?? 'UTC').replace(/_/g, ' ')} time, and a
            scheduled run is skipped if the previous one is still going.
          </p>
        </div>
        <Freshness loads={[load]} />
      </div>

      {app.runtime && !app.runtime.scheduler_running && (
        <div className="callout callout-warning" style={{ marginBottom: 20 }}>
          <Icon name="alert" />
          <div>
            <div className="callout-title">The scheduler is off</div>
            <div className="callout-body">
              Schedules are saved but won’t fire until the backend runs with <code>SCHEDULER_ENABLED=true</code>.
            </div>
          </div>
        </div>
      )}

      <section className="card">
        <Loaded load={load} what="the agent" placeholderHeight={160}>
          {(agents) =>
            agents.length === 0 ? (
              <Empty title="No agent set up yet">
                The Assistant Manager is added when the restaurant is set up. If it’s missing, ask an owner or admin to
                check the setup.
              </Empty>
            ) : (
              <ul className="list">
                {agents.map((agent) => (
                  <AgentRow
                    key={`${agent.id}-${agent.schedule_cron}-${agent.enabled}`}
                    agent={agent}
                    app={app}
                    onSaved={load.reload}
                  />
                ))}
              </ul>
            )
          }
        </Loaded>
        <div className="card-footer stock-check-note">
          <Icon name="clock" size={14} />
          {stockCheck ? (
            <span>
              Also checks stock every day at <span className="strong">{stockCheck}</span>, as set in{' '}
              <a href="#profile/rules">Profile → Operating rules</a>.
            </span>
          ) : (
            <span>
              No daily stock check. Set a time under <a href="#profile/rules">Profile → Operating rules</a> to add one.
            </span>
          )}
        </div>
      </section>
    </main>
  )
}
