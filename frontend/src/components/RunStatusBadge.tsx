import type { RunStatus } from '../api/client'

const STYLE: Record<RunStatus, string> = {
  queued: 'badge-info',
  running: 'badge-info',
  succeeded: 'badge-success',
  failed: 'badge-danger',
  cancelled: '',
}

export function RunStatusBadge({ status }: { status: RunStatus }) {
  const live = status === 'running' || status === 'queued'
  return (
    <span className={`badge ${STYLE[status] ?? ''}`}>
      {live && <span className="dot live" />}
      {status}
    </span>
  )
}
