import { useEffect, useState } from 'react'
import type { AgentDefinition } from '../api/client'

type Props = {
  open: boolean
  agents: AgentDefinition[]
  defaultAgentKey: string | null
  onClose: () => void
  onSubmit: (agentKey: string, task: string) => Promise<void>
}

export function NewTaskModal({ open, agents, defaultAgentKey, onClose, onSubmit }: Props) {
  const [task, setTask] = useState('')
  const [agentKey, setAgentKey] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setAgentKey(defaultAgentKey ?? agents[0]?.key ?? '')
    setTask('')
    setError(null)
  }, [open, defaultAgentKey, agents])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await onSubmit(agentKey, task.trim())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      <div className="overlay" onClick={onClose} />
      <div className="modal-wrap" onClick={(e) => e.target === e.currentTarget && onClose()}>
        <form className="modal" onSubmit={submit} role="dialog" aria-modal="true" aria-labelledby="new-task-title">
          <div className="modal-header">
            <h2 id="new-task-title" className="card-title">
              New agent task
            </h2>
            <p className="card-subtitle">The run starts straight away; you can watch it live.</p>
          </div>
          <div className="modal-body">
            <label className="field">
              <span className="field-label">Agent</span>
              <select className="select" value={agentKey} onChange={(e) => setAgentKey(e.target.value)}>
                {agents.map((a) => (
                  <option key={a.key} value={a.key}>
                    {a.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="field-label">What should it do?</span>
              <textarea
                className="textarea"
                required
                autoFocus
                placeholder="e.g. Check what is running low and propose reorders"
                value={task}
                onChange={(e) => setTask(e.target.value)}
              />
            </label>
            {error && <p className="negative small">{error}</p>}
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-secondary" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={submitting || !task.trim()}>
              {submitting ? 'Starting…' : 'Start task'}
            </button>
          </div>
        </form>
      </div>
    </>
  )
}
