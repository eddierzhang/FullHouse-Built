import { useEffect, useState } from 'react'

export const ROUTES = [
  'overview',
  'approvals',
  'runs',
  'agents',
  'inventory',
  'suppliers',
  'menu',
  'staff',
  'profit',
] as const

export type Route = (typeof ROUTES)[number]

/** Earlier URLs, kept working so old links and bookmarks still land. */
const ALIASES: Record<string, Route> = {
  tasks: 'approvals',
  supply: 'suppliers',
  marketing: 'menu',
  employees: 'staff',
}

function readRoute(): Route {
  const raw = window.location.hash.replace(/^#\/?/, '').split(/[/?]/)[0]
  if ((ROUTES as readonly string[]).includes(raw)) return raw as Route
  return ALIASES[raw] ?? 'overview'
}

/**
 * Hash routing, without a router dependency: the app is a handful of
 * top-level pages with no nested routes or loaders.
 */
export function useHashRoute(): [Route, (route: Route) => void] {
  const [route, setRoute] = useState<Route>(readRoute)

  useEffect(() => {
    const onChange = () => {
      setRoute(readRoute())
      window.scrollTo({ top: 0 })
    }
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  const navigate = (next: Route) => {
    window.location.hash = `#${next}`
  }
  return [route, navigate]
}
