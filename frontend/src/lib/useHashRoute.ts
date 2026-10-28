import { useEffect, useState } from 'react'

export const ROUTES = [
  'overview',
  // Invoices & prices: the agent's reading of the supplier invoices.
  'digest',
  'items',
  'item',
  'questions',
  'thread',
  'draft',
  'invoices',
  'bookkeeper',
  // v2: connected systems (behind the v2_connect flag), documents and recipes (v2_documents).
  'connect',
  'documents',
  'recipes',
  'recipe',
  'approvals',
  'runs',
  'agents',
  'inventory',
  'suppliers',
  'menu',
  'staff',
  'customers',
  'profit',
  'profile',
  'history',
  'help',
] as const

export type Route = (typeof ROUTES)[number]

/** The price pages: where the agent's question field replaces the Assistant Manager's controls. */
export const PRICE_ROUTES: ReadonlySet<Route> = new Set<Route>(['digest', 'items', 'item', 'questions', 'thread', 'draft', 'invoices', 'documents', 'recipes', 'recipe'])

/** Earlier URLs, kept working so old links and bookmarks still land. */
const ALIASES: Record<string, Route> = {
  tasks: 'approvals',
  supply: 'suppliers',
  marketing: 'menu',
  employees: 'staff',
  settings: 'profile',
  // The names the navigation uses for them.
  today: 'overview',
  recommendations: 'approvals',
  schedule: 'staff',
  activity: 'runs',
  undo: 'history',
  requests: 'customers',
  'customer-requests': 'customers',
  prices: 'digest',
  conversation: 'thread',
  drafts: 'draft',
  archive: 'overview',
}

function readRoute(): Route {
  const raw = window.location.hash.replace(/^#\/?/, '').split(/[/?]/)[0]
  if ((ROUTES as readonly string[]).includes(raw)) return raw as Route
  return ALIASES[raw] ?? 'overview'
}

/** The part after the route's slash: "#item/abc" -> "abc"; "" when there is none. */
export function readHashParam(): string {
  const rest = window.location.hash.replace(/^#\/?/, '').split('?')[0]
  const slash = rest.indexOf('/')
  return slash === -1 ? '' : decodeURIComponent(rest.slice(slash + 1))
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

/** The id in the address for pages that have one ("#item/<id>", "#draft/<id>"), kept current. */
export function useHashParam(): string {
  const [param, setParam] = useState<string>(readHashParam)
  useEffect(() => {
    const onChange = () => setParam(readHashParam())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return param
}
