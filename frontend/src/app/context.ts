import type { AgentAction, AgentDefinition, InventoryItem, Restaurant, Runtime } from '../api/client'
import type { Route } from '../lib/useHashRoute'

/** What every page can read and do. Built once in AppShell. */
export type AppContext = {
  agents: AgentDefinition[]
  agentsById: Map<string, AgentDefinition>
  pendingActions: AgentAction[]
  inventory: InventoryItem[]
  runtime: Runtime | null
  /** The set-up restaurant; the dashboard is only shown once setup is finished. */
  restaurant: Restaurant
  setRestaurant: (restaurant: Restaurant) => void
  apiStatus: 'checking' | 'ok' | 'error'
  /** Owner or admin: may run and approve the agent, edit the menu and suppliers, and see profit. */
  canManage: boolean
  /** The v2 features switched on for this restaurant (Profile, Features). */
  flags: Record<string, boolean>

  navigate: (route: Route) => void
  refresh: () => void
  toast: (title: string, subtitle?: string) => void

  /** Opens the new-task dialog, optionally with an agent preselected. */
  newTask: (agentKey?: string) => void
  /** Run to open when arriving at the Runs page. */
  focusRunId: string | null

  openAction: (action: AgentAction) => void
  approve: (action: AgentAction, overrides?: Record<string, unknown>) => Promise<boolean>
  reject: (action: AgentAction) => Promise<boolean>
  busyActionId: string | null
}
