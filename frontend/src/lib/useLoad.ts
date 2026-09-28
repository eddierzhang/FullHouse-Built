import { type DependencyList, useCallback, useEffect, useRef, useState } from 'react'

/**
 * Fired when the server's change history moves -- a teammate (or the agent)
 * changed something. Every page loading through `useLoad` refetches quietly.
 */
export const DATA_CHANGED_EVENT = 'fullhouse:data-changed'

export type Load<T> = {
  /** The last data that loaded. Kept through a failed refresh, so the page doesn't go blank. */
  data: T | null
  /** 'loading' only for the first load; later refreshes keep showing the data. */
  status: 'loading' | 'ready' | 'error'
  /** Why the last attempt failed; null once one succeeds. */
  error: string | null
  /** When `data` was fetched. */
  loadedAt: Date | null
  refreshing: boolean
  /** Fetch again. Resolves whether or not it worked; read `error` for the outcome. */
  reload: () => Promise<void>
}

/**
 * Load something for a page, with the states every page needs: a first
 * load, a failure that says why (and can be retried), a quiet refresh that
 * keeps the old data on screen, and the time it was last fetched.
 *
 * Refetches when `deps` change, when the server says data changed
 * (DATA_CHANGED_EVENT), and when the browser comes back online.
 */
export function useLoad<T>(fetcher: () => Promise<T>, deps: DependencyList): Load<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loadedAt, setLoadedAt] = useState<Date | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher
  // Ignore answers to anything but the latest request: a slow first load
  // mustn't overwrite a faster refresh that came after it.
  const latest = useRef(0)

  const reload = useCallback(async () => {
    const ticket = ++latest.current
    setRefreshing(true)
    try {
      const next = await fetcherRef.current()
      if (ticket !== latest.current) return
      setData(next)
      setError(null)
      setLoadedAt(new Date())
    } catch (err) {
      if (ticket !== latest.current) return
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      if (ticket === latest.current) setRefreshing(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  useEffect(() => {
    void reload()
  }, [reload])

  useEffect(() => {
    const onChange = () => void reload()
    window.addEventListener(DATA_CHANGED_EVENT, onChange)
    window.addEventListener('online', onChange)
    return () => {
      window.removeEventListener(DATA_CHANGED_EVENT, onChange)
      window.removeEventListener('online', onChange)
    }
  }, [reload])

  const status = data !== null ? 'ready' : error ? 'error' : 'loading'
  return { data, status, error, loadedAt, refreshing, reload }
}

/** Tell every open page to refetch -- after a save that changes what others show. */
export function announceDataChanged(): void {
  window.dispatchEvent(new Event(DATA_CHANGED_EVENT))
}
