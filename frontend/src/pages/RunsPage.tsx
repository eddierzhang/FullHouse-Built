import { useCallback, useEffect, useRef, useState } from 'react'
import { type AgentRun, type RunEvent, cancelRun, getRun, listRuns, runStreamUrl } from '../api/client'
import type { AppContext } from '../app/context'
import { Icon } from '../components/Icon'
import { RunStatusBadge } from '../components/RunStatusBadge'
import { duration, relativeTime } from '../lib/format'

const TERMINAL = '__end__'
const isLive = (run: AgentRun | null) => !!run && (run.status === 'queued' || run.status === 'running')

function describe(event: RunEvent): string {
  const p = event.payload ?? {}
  switch (event.type) {
    case 'tool_call':
      return `${p.tool}(${JSON.stringify(p.input ?? {})})`
    case 'tool_result':
      return `${p.tool} → ${String(p.result ?? '')}`
    case 'delegation':
      return `delegated to ${p.agent_name}: ${p.task}`
    case 'status_change':
      return `${p.status}${p.error ? ` — ${p.error}` : ''}`
    default:
      return String(p.text ?? JSON.stringify(p))
  }
}

export function RunsPage({ app }: { app: AppContext }) {
  const [runs, setRuns] = useState<AgentRun[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(app.focusRunId)
  const [selected, setSelected] = useState<AgentRun | null>(null)
  const [events, setEvents] = useState<RunEvent[]>([])
  const [streaming, setStreaming] = useState(false)
  const logRef = useRef<HTMLDivElement>(null)

  const refreshRuns = useCallback(() => {
    listRuns()
      .then((all) => {
        setRuns(all)
        setSelectedId((current) => current ?? all.find((r) => !r.parent_run_id)?.id ?? null)
      })
      .catch(() => {})
  }, [])

  useEffect(refreshRuns, [refreshRuns])
  useEffect(() => {
    if (app.focusRunId) setSelectedId(app.focusRunId)
  }, [app.focusRunId])

  // One EventSource per selected run. The server replays recorded events
  // first, so a finished run shows everything it did.
  useEffect(() => {
    if (!selectedId) return
    setEvents([])
    setStreaming(true)
    getRun(selectedId).then(setSelected).catch(() => {})

    const source = new EventSource(runStreamUrl(selectedId))
    source.onmessage = (message) => {
      const event: RunEvent = JSON.parse(message.data)
      if (event.type === TERMINAL) {
        setStreaming(false)
        source.close()
        getRun(selectedId).then(setSelected).catch(() => {})
        refreshRuns()
        app.refresh()
        return
      }
      setEvents((previous) => [...previous, event])
      if (event.type === 'status_change') {
        getRun(selectedId).then(setSelected).catch(() => {})
        refreshRuns()
      }
    }
    source.onerror = () => {
      setStreaming(false)
      source.close()
    }
    return () => {
      source.close()
      setStreaming(false)
    }
    // Deliberately keyed on the run alone: re-subscribing whenever the parent
    // re-renders would restart the stream and replay every event.
  }, [selectedId, refreshRuns])

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight })
  }, [events])

  // Newest first, each run followed by the subagent runs it delegated.
  const roots = runs.filter((r) => !r.parent_run_id)
  const childrenOf = (id: string) =>
    runs.filter((r) => r.parent_run_id === id).sort((a, b) => a.created_at.localeCompare(b.created_at))
  const agentName = (run: AgentRun) => app.agentsById.get(run.agent_definition_id)?.name ?? 'Agent'
  const active = runs.filter(isLive).length

  const row = (run: AgentRun, child = false) => (
    <button key={run.id} className={`run-row${child ? ' child' : ''}`} aria-pressed={selectedId === run.id} onClick={() => setSelectedId(run.id)}>
      <RunStatusBadge status={run.status} />
      <span style={{ minWidth: 0 }}>
        <span className="strong truncate" style={{ display: 'block' }}>
          {child ? '↳ ' : ''}
          {agentName(run)}
          {run.trigger_type === 'scheduled' && <span className="badge" style={{ marginLeft: 8 }}>scheduled</span>}
        </span>
        <span className="muted small truncate" style={{ display: 'block' }}>
          {run.input || 'No prompt'}
        </span>
      </span>
      <span className="small muted tabular" style={{ textAlign: 'right' }}>
        {relativeTime(run.created_at)}
        <br />
        {duration(run.started_at, run.finished_at)}
        {run.tokens_used ? ` · ${run.tokens_used.toLocaleString()} tok` : ''}
      </span>
    </button>
  )

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Runs</h1>
          <p className="page-subtitle">
            {runs.length} runs{active ? ` · ${active} in progress` : ''}. Select one to watch it live.
          </p>
        </div>
        <button className="btn btn-secondary" onClick={() => app.newTask()}>
          <Icon name="play" size={14} /> Start a run
        </button>
      </div>

      <div className="grid grid-2" style={{ alignItems: 'start' }}>
        <section className="card">
          <div className="card-header">
            <h2 className="card-title">History</h2>
          </div>
          <div style={{ maxHeight: 620, overflowY: 'auto' }}>
            {roots.length === 0 && <div className="empty">No runs yet.</div>}
            {roots.map((run) => (
              <div key={run.id}>
                {row(run)}
                {childrenOf(run.id).map((child) => row(child, true))}
              </div>
            ))}
          </div>
        </section>

        <section className="card" aria-live="polite">
          <div className="card-header">
            <div style={{ minWidth: 0 }}>
              <h2 className="card-title row">
                Live log
                {streaming && (
                  <span className="badge badge-brand">
                    <span className="dot live" /> streaming
                  </span>
                )}
              </h2>
              <div className="card-subtitle truncate">{selected ? `${agentName(selected)} · ${selected.status}` : 'No run selected'}</div>
            </div>
            {isLive(selected) && (
              <button
                className="btn btn-danger btn-sm"
                onClick={async () => {
                  try {
                    await cancelRun(selected!.id)
                    app.toast('Cancelling', 'The run stops at its next checkpoint.')
                  } catch (err) {
                    app.toast('Couldn’t cancel', err instanceof Error ? err.message : String(err))
                  }
                }}
              >
                <Icon name="stop" size={12} /> Cancel
              </button>
            )}
          </div>

          <div className="run-log" ref={logRef} data-testid="run-log">
            {!selectedId && <div className="empty">Select a run.</div>}
            {selectedId && events.length === 0 && (
              <div className="empty">{streaming ? 'Waiting for the first event…' : 'No events recorded.'}</div>
            )}
            {events.map((event, index) => (
              <div className="run-log-line" key={`${event.seq ?? 'x'}-${index}`}>
                <span className="run-log-type" data-type={event.type}>
                  {event.type.replace('_', ' ')}
                </span>
                <span>{describe(event)}</span>
              </div>
            ))}
          </div>

          {selected?.output_summary && (
            <div className="card-footer" style={{ color: 'var(--text)', whiteSpace: 'pre-wrap' }}>
              <div className="field-label" style={{ marginBottom: 4 }}>
                Summary
              </div>
              {selected.output_summary}
            </div>
          )}
          {selected?.error && selected.status !== 'succeeded' && (
            <div className="card-footer negative">{selected.error}</div>
          )}
        </section>
      </div>
    </main>
  )
}
