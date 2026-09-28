import { type ReactNode, useEffect, useState } from 'react'
import { relativeTime } from '../lib/format'
import type { Load } from '../lib/useLoad'
import { Icon } from './Icon'

/**
 * The states every page shares, so staff always know where they stand:
 * loading, nothing here yet, couldn't load (and try again), and how fresh
 * what they're looking at is.
 */

/** A first load: a placeholder the size of what's coming. */
export function Loading({ label = 'Loading…', height = 120 }: { label?: string; height?: number }) {
  return (
    <div className="skeleton" style={{ height }} role="status" aria-busy="true">
      <span className="visually-hidden">{label}</span>
    </div>
  )
}

/** Nothing to show yet -- and, where there is one, the first thing to do about it. */
export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <div className="empty-title">{title}</div>
      {children && <div>{children}</div>}
      {action && <div className="empty-action">{action}</div>}
    </div>
  )
}

/** A load that failed, in words, with a way to try again. */
export function LoadError({ what, error, onRetry }: { what: string; error: string; onRetry: () => void }) {
  return (
    <div className="callout callout-danger" role="alert">
      <Icon name="alert" />
      <div className="callout-grow">
        <div className="callout-title">Couldn’t load {what}</div>
        <div className="callout-body">{error}</div>
      </div>
      <button className="btn btn-secondary btn-sm" onClick={onRetry}>
        Try again
      </button>
    </div>
  )
}

/**
 * Renders `children` with the loaded data, or the right state instead: a
 * placeholder, the failure, or -- when a refresh fails but older data is
 * still on screen -- a note saying how old it is.
 */
export function Loaded<T>({
  load,
  what,
  children,
  placeholderHeight,
}: {
  load: Load<T>
  what: string
  children: (data: T) => ReactNode
  placeholderHeight?: number
}) {
  if (load.status === 'loading') return <Loading label={`Loading ${what}…`} height={placeholderHeight} />
  if (load.data === null) return <LoadError what={what} error={load.error ?? 'Something went wrong.'} onRetry={load.reload} />
  return (
    <>
      {load.error && (
        <div className="callout callout-warning stale-note" role="status">
          <Icon name="alert" />
          <div className="callout-grow">
            Couldn’t refresh {what}, so this is as of {load.loadedAt ? relativeTime(load.loadedAt.toISOString()) : 'earlier'}.
          </div>
          <button className="btn btn-secondary btn-sm" onClick={load.reload}>
            Try again
          </button>
        </div>
      )}
      {children(load.data)}
    </>
  )
}

/** "Updated 2 minutes ago · Refresh" -- how fresh the page is, and a way to make it fresher. */
export function Freshness({ loads }: { loads: Load<unknown>[] }) {
  // Re-render every half minute so "just now" moves on by itself.
  const [, tick] = useState(0)
  useEffect(() => {
    const timer = window.setInterval(() => tick((n) => n + 1), 30_000)
    return () => window.clearInterval(timer)
  }, [])

  const times = loads.map((l) => l.loadedAt).filter((t): t is Date => t !== null)
  const refreshing = loads.some((l) => l.refreshing)
  if (times.length === 0 && !refreshing) return null
  const oldest = times.length ? new Date(Math.min(...times.map((t) => t.getTime()))) : null

  return (
    <div className="freshness" aria-live="polite">
      <span className="faint">
        {refreshing ? 'Refreshing…' : oldest ? `Updated ${relativeTime(oldest.toISOString())}` : ''}
      </span>
      <button
        className="btn btn-ghost btn-sm"
        disabled={refreshing}
        onClick={() => loads.forEach((l) => void l.reload())}
      >
        Refresh
      </button>
    </div>
  )
}
