import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  type AgentAction,
  type AgentDefinition,
  type InventoryItem,
  type Restaurant,
  type Runtime,
  type User,
  approveAction,
  canEditRestaurant,
  fetchChangeVersion,
  fetchHealth,
  fetchRuntime,
  listActions,
  listAgentDefinitions,
  listInventoryItems,
  rejectAction,
  triggerRun,
} from '../api/client'
import { ActionDrawer } from '../components/ActionDrawer'
import { Icon, type IconName } from '../components/Icon'
import { NewTaskModal } from '../components/NewTaskModal'
import { Toast, type ToastState } from '../components/Toast'
import { relativeTime } from '../lib/format'
import { announceDataChanged } from '../lib/useLoad'
import { type Route, useHashRoute } from '../lib/useHashRoute'
import { AgentsPage } from '../pages/AgentsPage'
import { HistoryPage } from '../pages/HistoryPage'
import { ApprovalsPage } from '../pages/ApprovalsPage'
import { InventoryPage } from '../pages/InventoryPage'
import { MenuPage } from '../pages/MenuPage'
import { OverviewPage } from '../pages/OverviewPage'
import { ProfitPage } from '../pages/ProfitPage'
import { RunsPage } from '../pages/RunsPage'
import { ProfilePage } from '../pages/ProfilePage'
import { StaffPage } from '../pages/StaffPage'
import { SuppliersPage } from '../pages/SuppliersPage'
import type { AppContext } from './context'

/** Health, the approvals badge and the agent's status, kept current without a reload. */
const POLL_MS = 20_000
/** How often to ask whether anything changed, so a teammate's change shows up by itself. */
const CHANGE_POLL_MS = 10_000

type NavItem = { route: Route; label: string; icon: IconName }

/** Pages only owners and admins see: the profit figures are theirs. */
const MANAGER_ROUTES = new Set<Route>(['profit'])

/**
 * Grouped by what people come to do. The first group is the day's work --
 * checking in, stock, the schedule, what the agent recommends -- and is
 * also the phone's bottom bar; the rest is setup and the longer view.
 */
const NAV: { label: string | null; items: NavItem[] }[] = [
  {
    label: null,
    items: [
      { route: 'overview', label: 'Today', icon: 'overview' },
      { route: 'inventory', label: 'Inventory', icon: 'inventory' },
      { route: 'staff', label: 'Schedule & staff', icon: 'staff' },
      { route: 'approvals', label: 'Recommendations', icon: 'approvals' },
    ],
  },
  {
    label: 'Kitchen',
    items: [
      { route: 'suppliers', label: 'Suppliers', icon: 'suppliers' },
      { route: 'menu', label: 'Menu', icon: 'menu' },
    ],
  },
  {
    label: 'Agent',
    items: [
      { route: 'agents', label: 'Assistant Manager', icon: 'agents' },
      { route: 'runs', label: 'Agent activity', icon: 'runs' },
    ],
  },
  {
    label: 'Records',
    items: [
      { route: 'profit', label: 'Profit & forecast', icon: 'profit' },
      { route: 'history', label: 'History & undo', icon: 'clock' },
    ],
  },
  { label: 'Restaurant', items: [{ route: 'profile', label: 'Profile', icon: 'store' }] },
]

/** The phone's bottom bar: the day's work, and everything else behind More. */
const TABS: NavItem[] = [
  { route: 'overview', label: 'Today', icon: 'overview' },
  { route: 'inventory', label: 'Inventory', icon: 'inventory' },
  { route: 'staff', label: 'Schedule', icon: 'staff' },
  { route: 'approvals', label: 'Recommend', icon: 'approvals' },
]

/** Whether the browser thinks it's online, kept current. */
function useOnline(): boolean {
  const [online, setOnline] = useState(() => (typeof navigator === 'undefined' ? true : navigator.onLine))
  useEffect(() => {
    const up = () => setOnline(true)
    const down = () => setOnline(false)
    window.addEventListener('online', up)
    window.addEventListener('offline', down)
    return () => {
      window.removeEventListener('online', up)
      window.removeEventListener('offline', down)
    }
  }, [])
  return online
}

type Props = {
  user: User
  restaurant: Restaurant
  setRestaurant: (restaurant: Restaurant) => void
  onSignOut: () => void
}

export function AppShell({ user, restaurant, setRestaurant, onSignOut }: Props) {
  const [route, navigate] = useHashRoute()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const online = useOnline()

  const [apiStatus, setApiStatus] = useState<AppContext['apiStatus']>('checking')
  const [agents, setAgents] = useState<AgentDefinition[]>([])
  const [pendingActions, setPendingActions] = useState<AgentAction[]>([])
  const [inventory, setInventory] = useState<InventoryItem[]>([])
  const [runtime, setRuntime] = useState<Runtime | null>(null)

  const [toastState, setToastState] = useState<ToastState>(null)
  const [taskAgent, setTaskAgent] = useState<string | null | undefined>(undefined)
  const [drawerAction, setDrawerAction] = useState<AgentAction | null>(null)
  const [busyActionId, setBusyActionId] = useState<string | null>(null)
  const [focusRunId, setFocusRunId] = useState<string | null>(null)

  const refresh = useCallback(() => {
    fetchHealth()
      .then(() => setApiStatus('ok'))
      .catch(() => setApiStatus('error'))
    listAgentDefinitions().then(setAgents).catch(() => {})
    listActions('pending').then(setPendingActions).catch(() => {})
    listInventoryItems().then(setInventory).catch(() => {})
    fetchRuntime().then(setRuntime).catch(() => {})
  }, [])

  useEffect(() => {
    refresh()
    const timer = window.setInterval(refresh, POLL_MS)
    return () => window.clearInterval(timer)
  }, [refresh])

  // Live refresh: when the change history moves -- a teammate's save, an
  // approval, the agent -- every open page refetches. Cheap to ask, and
  // skipped while the tab is hidden.
  const lastVersion = useRef<number | null>(null)
  useEffect(() => {
    const check = () => {
      if (document.visibilityState === 'hidden') return
      fetchChangeVersion()
        .then(({ version }) => {
          if (lastVersion.current !== null && version !== lastVersion.current) {
            announceDataChanged()
            refresh()
          }
          lastVersion.current = version
        })
        .catch(() => {})
    }
    check()
    const timer = window.setInterval(check, CHANGE_POLL_MS)
    document.addEventListener('visibilitychange', check)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', check)
    }
  }, [refresh])

  // Back online: catch up on anything missed while away.
  useEffect(() => {
    if (online) refresh()
  }, [online, refresh])

  useEffect(() => setSidebarOpen(false), [route])

  const toast = useCallback((title: string, subtitle?: string) => {
    setToastState({ title, subtitle })
    window.setTimeout(() => setToastState(null), 3200)
  }, [])

  // The main agent first, then any others alphabetically -- the order they appear everywhere.
  const sortedAgents = useMemo(
    () => [...agents].sort((a, b) => (a.role === b.role ? a.name.localeCompare(b.name) : a.role === 'boss' ? -1 : 1)),
    [agents],
  )

  const app: AppContext = {
    agents: sortedAgents,
    agentsById: new Map(agents.map((a) => [a.id, a])),
    pendingActions,
    inventory,
    runtime,
    restaurant,
    setRestaurant,
    apiStatus,
    canManage: canEditRestaurant(user),
    navigate,
    refresh,
    toast,
    newTask: (agentKey) => setTaskAgent(agentKey ?? null),
    focusRunId,
    openAction: setDrawerAction,
    busyActionId,
    approve: async (action, overrides) => {
      setBusyActionId(action.id)
      try {
        const result = await approveAction(action.id, overrides)
        toast('Approved', result.applied_result ?? undefined)
        refresh()
        return true
      } catch (err) {
        toast('Could not approve', err instanceof Error ? err.message : String(err))
        return false
      } finally {
        setBusyActionId(null)
      }
    },
    reject: async (action) => {
      setBusyActionId(action.id)
      try {
        await rejectAction(action.id)
        toast('Rejected')
        refresh()
        return true
      } catch (err) {
        toast('Could not reject', err instanceof Error ? err.message : String(err))
        return false
      } finally {
        setBusyActionId(null)
      }
    },
  }

  const lowStock = inventory.filter((i) => i.quantity_on_hand <= i.reorder_threshold).length
  const counts: Partial<Record<Route, { value: number; attention: boolean }>> = {
    approvals: { value: pendingActions.length, attention: pendingActions.length > 0 },
    inventory: { value: lowStock, attention: lowStock > 0 },
  }

  const page = (() => {
    switch (route) {
      case 'approvals':
        return <ApprovalsPage app={app} />
      case 'runs':
        return <RunsPage app={app} />
      case 'agents':
        return <AgentsPage app={app} />
      case 'inventory':
        return <InventoryPage app={app} user={user} />
      case 'suppliers':
        return <SuppliersPage app={app} />
      case 'menu':
        return <MenuPage app={app} />
      case 'staff':
        return <StaffPage app={app} user={user} />
      case 'profit':
        return app.canManage ? <ProfitPage app={app} /> : <OverviewPage app={app} />
      case 'profile':
        return <ProfilePage app={app} user={user} />
      case 'history':
        return <HistoryPage app={app} />
      default:
        return <OverviewPage app={app} />
    }
  })()

  const nav = NAV.map((group) => ({ ...group, items: group.items.filter((item) => app.canManage || !MANAGER_ROUTES.has(item.route)) })).filter(
    (group) => group.items.length > 0,
  )

  return (
    <div className="app">
      <aside className={`sidebar${sidebarOpen ? ' open' : ''}`} aria-label="Main navigation">
        <a className="brand" href="#overview">
          <span className="brand-mark">F</span>
          <span className="brand-text">
            <span className="brand-name">{restaurant.name ?? 'FullHouse'}</span>
            <span className="brand-sub">FullHouse</span>
          </span>
        </a>

        {nav.map((group) => (
          <nav key={group.label ?? 'home'} className="nav-group" aria-label={group.label ?? 'Home'}>
            {group.label && <div className="nav-label">{group.label}</div>}
            {group.items.map((item) => {
              const count = counts[item.route]
              return (
                <a
                  key={item.route}
                  className="nav-link"
                  href={`#${item.route}`}
                  aria-current={route === item.route ? 'page' : undefined}
                >
                  <Icon name={item.icon} />
                  {item.label}
                  {count && count.value > 0 && (
                    <span className={`nav-count${count.attention ? ' attention' : ''}`}>{count.value}</span>
                  )}
                </a>
              )
            })}
          </nav>
        ))}

        <div className="sidebar-footer">
          <a className="sidebar-user" href="#profile" title="Your profile">
            <span className="avatar" aria-hidden="true">
              {user.name.trim().charAt(0).toUpperCase() || '?'}
            </span>
            <div className="sidebar-user-text">
              <div className="sidebar-user-name">{user.name}</div>
              <div className="faint sidebar-user-email">{user.email}</div>
            </div>
          </a>
          <button className="btn btn-secondary btn-sm sign-out" onClick={onSignOut}>
            Sign out
          </button>
          <div className="row">
            <span
              className="dot"
              style={{ color: !online || apiStatus === 'error' ? 'var(--danger)' : apiStatus === 'ok' ? 'var(--success)' : 'var(--text-faint)' }}
            />
            {!online ? 'Offline' : apiStatus === 'ok' ? 'Connected' : apiStatus === 'error' ? 'Can’t reach FullHouse' : 'Connecting…'}
          </div>
          {runtime &&
            (runtime.ai_problem ? (
              <div className="faint" style={{ marginTop: 4 }} title={runtime.ai_problem}>
                AI assistant unavailable
              </div>
            ) : (
              <div className="faint" style={{ marginTop: 4 }}>
                {runtime.provider} · {runtime.model}
              </div>
            ))}
        </div>
      </aside>

      {sidebarOpen && <div className="overlay" style={{ zIndex: 25 }} onClick={() => setSidebarOpen(false)} />}

      <div className="main">
        <header className="topbar">
          <button className="btn btn-ghost btn-icon menu-button" aria-label="Open navigation" onClick={() => setSidebarOpen(true)}>
            <Icon name="hamburger" />
          </button>
          {runtime?.demo_mode && (
            <div className="demo-banner" role="note">
              <span className="demo-pill">Live demo</span>
              <span className="hide-mobile">
                Change anything — it all resets{runtime.demo_resets_at ? ` ${relativeTime(runtime.demo_resets_at)}` : ' daily'}.
                {app.canManage && (
                  <>
                    {' '}Try{' '}
                    <button className="link-button" onClick={() => app.newTask('maestro')}>
                      a task for the Assistant Manager
                    </button>
                    .
                  </>
                )}
              </span>
            </div>
          )}
          <div className="topbar-spacer" />
          {pendingActions.length > 0 && route !== 'approvals' && (
            <a className="btn btn-secondary hide-mobile" href="#approvals">
              <span className="dot" style={{ color: 'var(--danger)' }} />
              {pendingActions.length} to review
            </a>
          )}
          {app.canManage && (
            <button className="btn btn-primary" onClick={() => app.newTask()}>
              <Icon name="plus" size={16} />
              New task
            </button>
          )}
        </header>

        {(!online || apiStatus === 'error') && (
          <div className="connection-banner" role="alert">
            <Icon name="alert" size={16} />
            <span>
              {!online
                ? 'You’re offline. You can look around, but changes won’t save until you’re back online.'
                : 'Can’t reach FullHouse right now. Changes won’t save until it’s back; we’ll keep trying.'}
            </span>
            {online && (
              <button className="btn btn-secondary btn-sm" onClick={refresh}>
                Try now
              </button>
            )}
          </div>
        )}

        {page}
      </div>

      <NewTaskModal
        open={taskAgent !== undefined}
        agents={sortedAgents}
        defaultAgentKey={taskAgent ?? null}
        onClose={() => setTaskAgent(undefined)}
        onSubmit={async (agentKey, task) => {
          const run = await triggerRun(agentKey, task)
          setTaskAgent(undefined)
          setFocusRunId(run.id)
          toast('Task started', 'Following it live in Agent activity.')
          navigate('runs')
        }}
      />

      <nav className="tabbar" aria-label="Daily tasks">
        {TABS.map((tab) => {
          const count = counts[tab.route]
          return (
            <a key={tab.route} className="tab" href={`#${tab.route}`} aria-current={route === tab.route ? 'page' : undefined}>
              <span className="tab-icon">
                <Icon name={tab.icon} size={20} />
                {count && count.value > 0 && (
                  <span className={`tab-count${count.attention ? ' attention' : ''}`} aria-hidden="true">
                    {count.value}
                  </span>
                )}
              </span>
              {tab.label}
              {/* Read after the name, not before it: "Recommend (1)". */}
              {count && count.value > 0 && <span className="visually-hidden"> ({count.value})</span>}
            </a>
          )
        })}
        <button className="tab" onClick={() => setSidebarOpen(true)}>
          <span className="tab-icon">
            <Icon name="hamburger" size={20} />
          </span>
          More
        </button>
      </nav>

      <ActionDrawer app={app} action={drawerAction} onClose={() => setDrawerAction(null)} />
      <Toast toast={toastState} />
    </div>
  )
}
