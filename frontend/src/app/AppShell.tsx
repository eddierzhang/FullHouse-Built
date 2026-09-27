import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  type AgentAction,
  type AgentDefinition,
  type InventoryItem,
  type Runtime,
  approveAction,
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
import { type Route, useHashRoute } from '../lib/useHashRoute'
import { AgentsPage } from '../pages/AgentsPage'
import { ApprovalsPage } from '../pages/ApprovalsPage'
import { InventoryPage } from '../pages/InventoryPage'
import { MenuPage } from '../pages/MenuPage'
import { OverviewPage } from '../pages/OverviewPage'
import { ProfitPage } from '../pages/ProfitPage'
import { RunsPage } from '../pages/RunsPage'
import { StaffPage } from '../pages/StaffPage'
import { SuppliersPage } from '../pages/SuppliersPage'
import type { AppContext } from './context'

/** Keeps the approvals badge and schedules current without a reload. */
const POLL_MS = 20_000

type NavItem = { route: Route; label: string; icon: IconName }

const NAV: { label: string | null; items: NavItem[] }[] = [
  { label: null, items: [{ route: 'overview', label: 'Overview', icon: 'overview' }] },
  {
    label: 'Agents',
    items: [
      { route: 'approvals', label: 'Approvals', icon: 'approvals' },
      { route: 'runs', label: 'Runs', icon: 'runs' },
      { route: 'agents', label: 'Agents & schedules', icon: 'agents' },
    ],
  },
  {
    label: 'Operations',
    items: [
      { route: 'inventory', label: 'Inventory', icon: 'inventory' },
      { route: 'suppliers', label: 'Suppliers', icon: 'suppliers' },
      { route: 'menu', label: 'Menu', icon: 'menu' },
      { route: 'staff', label: 'Staff', icon: 'staff' },
    ],
  },
  { label: 'Insights', items: [{ route: 'profit', label: 'Profit & forecast', icon: 'profit' }] },
]

export function AppShell() {
  const [route, navigate] = useHashRoute()
  const [sidebarOpen, setSidebarOpen] = useState(false)

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

  useEffect(() => setSidebarOpen(false), [route])

  const toast = useCallback((title: string, subtitle?: string) => {
    setToastState({ title, subtitle })
    window.setTimeout(() => setToastState(null), 3200)
  }, [])

  // Maestro first, then everyone else alphabetically -- the order they appear everywhere.
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
    apiStatus,
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
        return <InventoryPage app={app} />
      case 'suppliers':
        return <SuppliersPage app={app} />
      case 'menu':
        return <MenuPage app={app} />
      case 'staff':
        return <StaffPage app={app} />
      case 'profit':
        return <ProfitPage app={app} />
      default:
        return <OverviewPage app={app} />
    }
  })()

  return (
    <div className="app">
      <aside className={`sidebar${sidebarOpen ? ' open' : ''}`} aria-label="Main navigation">
        <a className="brand" href="#overview">
          <span className="brand-mark">F</span>
          FullHouse
        </a>

        {NAV.map((group) => (
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
          <div className="row">
            <span
              className="dot"
              style={{ color: apiStatus === 'ok' ? 'var(--success)' : apiStatus === 'error' ? 'var(--danger)' : 'var(--text-faint)' }}
            />
            {apiStatus === 'ok' ? 'Connected' : apiStatus === 'error' ? 'Backend unreachable' : 'Connecting…'}
          </div>
          {runtime && (
            <div className="faint" style={{ marginTop: 4 }}>
              {runtime.provider} · {runtime.model}
            </div>
          )}
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
                Change anything — it all resets{runtime.demo_resets_at ? ` ${relativeTime(runtime.demo_resets_at)}` : ' daily'}. Try{' '}
                <button className="link-button" onClick={() => app.newTask('boss')}>
                  a task for Maestro
                </button>
                .
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
          <button className="btn btn-primary" onClick={() => app.newTask()}>
            <Icon name="plus" size={16} />
            New task
          </button>
        </header>

        {apiStatus === 'error' && (
          <div className="page" style={{ paddingBottom: 0 }}>
            <div className="callout callout-danger">
              <Icon name="alert" />
              <div>
                <div className="callout-title">Can’t reach the backend</div>
                <div className="callout-body">Check the API server is running, then refresh.</div>
              </div>
            </div>
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
          toast('Task started', 'Following it live in Runs.')
          navigate('runs')
        }}
      />

      <ActionDrawer app={app} action={drawerAction} onClose={() => setDrawerAction(null)} />
      <Toast toast={toastState} />
    </div>
  )
}
