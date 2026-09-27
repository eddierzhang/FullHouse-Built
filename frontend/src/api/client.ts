// Unset in local dev: call the backend directly. The container build sets
// "same-origin", where nginx proxies /api on the same host -- an explicit
// value rather than an empty one, which build tooling can silently drop.
const configuredBase = import.meta.env.VITE_API_BASE
const API_BASE = configuredBase === 'same-origin' ? '' : configuredBase || 'http://localhost:8000'

async function request(path: string, options: RequestInit = {}): Promise<Response> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail ?? `${path} failed: ${res.status}`)
  }
  return res
}

const get = <T>(path: string) => request(path).then((r) => r.json() as Promise<T>)
const send = <T>(method: string, path: string, body?: unknown) =>
  request(path, { method, body: body === undefined ? undefined : JSON.stringify(body) }).then(
    (r) => r.json() as Promise<T>,
  )
/** For 204 responses, which have no body to parse. */
const sendVoid = (method: string, path: string) => request(path, { method }).then(() => undefined)

// --- agents ------------------------------------------------------------------

export type AgentDefinition = {
  id: string
  key: string
  name: string
  description: string
  role: 'boss' | 'subagent'
  model: string
  tool_allowlist: string[]
  schedule_cron: string | null
  enabled: boolean
  /** When the schedule next fires, in UTC; null when unscheduled or paused. */
  next_run_at: string | null
}

export type Runtime = {
  provider: string
  model: string
  scheduler_running: boolean
  max_concurrent_runs: number
  /** A public demo: the data is replaced on a schedule. */
  demo_mode?: boolean
  demo_resets_at?: string | null
}

export type RunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export type AgentRun = {
  id: string
  agent_definition_id: string
  parent_run_id: string | null
  depth: number
  status: RunStatus
  trigger_type: 'manual' | 'scheduled' | 'delegated'
  input: string | null
  output_summary: string | null
  error: string | null
  tokens_used: number | null
  started_at: string | null
  finished_at: string | null
  created_at: string
}

export type RunEvent = {
  type: string
  seq?: number
  run_id: string
  payload?: Record<string, unknown>
}

export type AgentAction = {
  id: string
  run_id: string
  agent_definition_id: string
  action_type: string
  payload: Record<string, unknown>
  status: 'pending' | 'approved' | 'rejected'
  created_at: string
  decided_at: string | null
  applied_result?: string | null
  /** False when no applier handles this type at all. */
  appliable?: boolean
  /** Fields the proposal left for the operator to supply. */
  needs_input?: string[]
}

export const fetchRuntime = () => get<Runtime>('/api/v1/agents/runtime')
export const listAgentDefinitions = () => get<AgentDefinition[]>('/api/v1/agents/definitions')
export const updateAgentDefinition = (
  key: string,
  patch: { schedule_cron?: string | null; enabled?: boolean },
) => send<AgentDefinition>('PATCH', `/api/v1/agents/definitions/${key}`, patch)

export const listRuns = () => get<AgentRun[]>('/api/v1/agents/runs')
export const getRun = (id: string) => get<AgentRun>(`/api/v1/agents/runs/${id}`)
export const triggerRun = (agentKey: string, input: string) =>
  send<AgentRun>('POST', '/api/v1/agents/runs', { agent_key: agentKey, input })
export const cancelRun = (id: string) => send<AgentRun>('POST', `/api/v1/agents/runs/${id}/cancel`)
/** SSE URL for one run. Consumed with EventSource, not fetch. */
export const runStreamUrl = (id: string) => `${API_BASE}/api/v1/agents/runs/${id}/stream`

export const listActions = (status?: string) =>
  get<AgentAction[]>(`/api/v1/agents/actions${status ? `?status=${status}` : ''}`)
export const approveAction = (id: string, overrides?: Record<string, unknown>) =>
  send<AgentAction>('POST', `/api/v1/agents/actions/${id}/approve`, { overrides: overrides ?? {} })
export const rejectAction = (id: string) => send<AgentAction>('POST', `/api/v1/agents/actions/${id}/reject`)

// --- inventory and suppliers ----------------------------------------------------

export type InventoryItem = {
  id: string
  name: string
  unit: string
  quantity_on_hand: number
  reorder_threshold: number
  reorder_qty: number
  unit_cost: number
  supplier_id: string | null
}

export type Supplier = { id: string; name: string; contact_info: string | null; lead_time_days: number }
export type SupplierInput = { name: string; contact_info?: string | null; lead_time_days?: number }

export type SupplyChain = {
  total_stock_value: number
  supplier_count: number
  item_count: number
  unassigned_item_count: number
  unassigned_stock_value: number
  low_stock_count: number
  restock_cost: number
  longest_lead_days: number
  at_risk_count: number
  suppliers: {
    id: string
    name: string
    contact_info: string | null
    lead_time_days: number
    item_count: number
    stock_value: number
    low_stock_count: number
    restock_cost: number
    share_pct: number
  }[]
  low_stock: {
    id: string
    name: string
    unit: string
    quantity_on_hand: number
    reorder_threshold: number
    reorder_qty: number
    unit_cost: number
    supplier_id: string | null
    supplier_name: string | null
    lead_time_days: number | null
    restock_cost: number
  }[]
  cover: {
    id: string
    name: string
    unit: string
    quantity_on_hand: number
    daily_use: number | null
    days_of_cover: number | null
    lead_time_days: number | null
    at_risk: boolean
  }[]
}

export const listInventoryItems = () => get<InventoryItem[]>('/api/v1/restaurant/inventory-items')
export const updateInventoryItem = (
  id: string,
  patch: Partial<Omit<InventoryItem, 'id'>>,
) => send<InventoryItem>('PATCH', `/api/v1/restaurant/inventory-items/${id}`, patch)

export const listSuppliers = () => get<Supplier[]>('/api/v1/restaurant/suppliers')
export const fetchSupplyChain = () => get<SupplyChain>('/api/v1/restaurant/supply-chain')
export const createSupplier = (input: SupplierInput) =>
  send<Supplier>('POST', '/api/v1/restaurant/suppliers', input)
export const updateSupplier = (id: string, patch: Partial<SupplierInput>) =>
  send<Supplier>('PATCH', `/api/v1/restaurant/suppliers/${id}`, patch)
export const deleteSupplier = (id: string) => sendVoid('DELETE', `/api/v1/restaurant/suppliers/${id}`)

// --- staff ------------------------------------------------------------------------

export type Staff = { id: string; name: string; role: string; email: string | null }
export type StaffInput = { name: string; role: string; email?: string | null }
export type Shift = { id: number; staff_id: string; date: string; start_time: string; end_time: string; role: string }
export type ShiftInput = Omit<Shift, 'id'>

export const listStaff = () => get<Staff[]>('/api/v1/restaurant/staff')
export const createStaff = (input: StaffInput) => send<Staff>('POST', '/api/v1/restaurant/staff', input)
export const updateStaff = (id: string, patch: Partial<StaffInput>) =>
  send<Staff>('PATCH', `/api/v1/restaurant/staff/${id}`, patch)
export const deleteStaff = (id: string) => sendVoid('DELETE', `/api/v1/restaurant/staff/${id}`)

export const listShifts = () => get<Shift[]>('/api/v1/restaurant/shifts')
export const createShift = (input: ShiftInput) => send<Shift>('POST', '/api/v1/restaurant/shifts', input)
export const deleteShift = (id: number) => sendVoid('DELETE', `/api/v1/restaurant/shifts/${id}`)

// --- menu -------------------------------------------------------------------------

export type MenuItem = {
  id: string
  name: string
  category: string
  price: number
  cost: number
  is_available: boolean
  description: string | null
  regular_price: number | null
  promo_ends_at: string | null
}
export type MenuItemInput = {
  name: string
  category: string
  price: number
  cost: number
  description?: string | null
  is_available?: boolean
}

export type MenuPerformance = MenuItem & {
  /** "recipe" when computed from ingredients, "manual" when typed in. */
  cost_source: 'recipe' | 'manual'
  units_sold: number
  revenue: number
  margin: number
  margin_pct: number
  profit: number
}

export type Recipe = {
  menu_item_id: string
  menu_item_name: string
  price: number
  ingredient_cost: number
  margin_pct: number
  lines: { inventory_item_id: string; name: string; unit: string; quantity: number; unit_cost: number; line_cost: number }[]
}

export const listMenuPerformance = () => get<MenuPerformance[]>('/api/v1/restaurant/menu-performance')
export const createMenuItem = (input: MenuItemInput) => send<MenuItem>('POST', '/api/v1/restaurant/menu-items', input)
export const updateMenuItem = (id: string, patch: Partial<MenuItemInput>) =>
  send<MenuItem>('PATCH', `/api/v1/restaurant/menu-items/${id}`, patch)
export const deleteMenuItem = (id: string) => sendVoid('DELETE', `/api/v1/restaurant/menu-items/${id}`)
export const getRecipe = (menuItemId: string) => get<Recipe>(`/api/v1/restaurant/menu-items/${menuItemId}/recipe`)
export const setRecipe = (menuItemId: string, lines: { inventory_item_id: string; quantity: number }[]) =>
  send<Recipe>('PUT', `/api/v1/restaurant/menu-items/${menuItemId}/recipe`, { lines })

// --- money ------------------------------------------------------------------------

export type DailyFigures = { date: string; revenue: number; cogs: number; profit: number; orders: number }

export type ProfitSummary = {
  days: number
  start: string
  end: string
  revenue: number
  cogs: number
  gross_profit: number
  margin_pct: number
  orders: number
  average_order_value: number
  inventory_value: number
  daily: DailyFigures[]
  by_category: { category: string; revenue: number; profit: number; units: number }[]
  by_channel: { channel: string; revenue: number; orders: number }[]
}

export type Forecast = {
  method: 'weekday' | 'average' | 'none'
  basis_days: number
  confidence: 'high' | 'medium' | 'low' | 'none'
  message: string
  history: DailyFigures[]
  days: { date: string; revenue: number; profit: number; low: number; high: number }[]
  total_revenue: number
  total_profit: number
}

export const fetchProfitSummary = (days = 30) =>
  get<ProfitSummary>(`/api/v1/restaurant/profit-summary?days=${days}`)
export const fetchForecast = (days = 7) => get<Forecast>(`/api/v1/restaurant/forecast?days=${days}`)

export const fetchHealth = () => get<{ status: string }>('/health')
