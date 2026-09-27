import type { AgentAction, AgentDefinition, InventoryItem, Runtime } from '../api/client'
import type { Route } from '../lib/useHashRoute'

/** What every page can read and do. Built once in AppShell. */
export type AppContext = {
  agents: AgentDefinition[]
  agentsById: Map<string, AgentDefinition>
  pendingActions: AgentAction[]
  inventory: InventoryItem[]
  runtime: Runtime | null
  apiStatus: 'checking' | 'ok' | 'error'

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
