import { useState } from 'react'
import { type AgentDefinition, updateAgentDefinition } from '../api/client'
import type { AppContext } from '../app/context'
import { Icon } from '../components/Icon'
import { SCHEDULE_PRESETS, dateTime, initials, relativeTime, scheduleLabel } from '../lib/format'

const CUSTOM = '__custom__'

function AgentRow({ agent, app }: { agent: AgentDefinition; app: AppContext }) {
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
          {agent.role === 'boss' && <span className="badge badge-brand">Orchestrator</span>}
          {!agent.enabled && <span className="badge badge-warning">Paused</span>}
          {pending > 0 && <span className="badge badge-danger">{pending} to review</span>}
        </div>
        <div className="muted small" style={{ marginTop: 2 }}>
          {agent.description}
        </div>
        <div className="faint small" style={{ marginTop: 6 }}>
          {agent.role === 'boss'
            ? `Delegates to ${agent.tool_allowlist.length} specialists`
            : `Model: ${app.runtime?.model ?? agent.model}`}
        </div>
      </div>

      <div className="stack" style={{ gap: 8, width: 280 }}>
        <label className="field">
          <span className="field-label">Schedule</span>
          <select className="select" value={mode} disabled={busy} onChange={(e) => chooseSchedule(e.target.value)}>
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
    </li>
  )
}

export function AgentsPage({ app }: { app: AppContext }) {
  const scheduled = app.agents.filter((a) => a.enabled && a.schedule_cron).length

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Agents & schedules</h1>
          <p className="page-subtitle">
            {app.agents.length} agents · {scheduled} on a schedule. Schedules run in UTC, and a scheduled run is skipped if
            the previous one is still going.
          </p>
        </div>
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
        <ul className="list">
          {app.agents.map((agent) => (
            <AgentRow key={`${agent.id}-${agent.schedule_cron}-${agent.enabled}`} agent={agent} app={app} />
          ))}
        </ul>
      </section>
    </main>
  )
}
