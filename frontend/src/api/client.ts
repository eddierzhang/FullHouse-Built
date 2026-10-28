// Unset in local dev: call the backend directly. The container build sets
// "same-origin", where nginx proxies /api on the same host -- an explicit
// value rather than an empty one, which build tooling can silently drop.
const configuredBase = import.meta.env.VITE_API_BASE
const API_BASE = configuredBase === 'same-origin' ? '' : configuredBase || 'http://localhost:8000'

/** Fired when a data request finds the session gone, so the app can show the login page. */
export const SIGNED_OUT_EVENT = 'fullhouse:signed-out'

const FIELD_LABELS: Record<string, string> = {
  mon: 'Monday', tue: 'Tuesday', wed: 'Wednesday', thu: 'Thursday', fri: 'Friday', sat: 'Saturday', sun: 'Sunday',
  staff_roles: 'Staff roles', region: 'State / region', postal_code: 'Postal code',
}
const fieldLabel = (field: string) =>
  FIELD_LABELS[field] ?? field.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())

/** FastAPI sends a string, or for invalid input a list of per-field problems. */
function errorMessage(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail.length > 0) {
    // Name the field each problem is about: ["body", "phone"] -> "Phone: ...".
    return detail
      .map((d) => {
        const message = String(d?.msg ?? d).replace(/^Value error, /, '')
        const field = [...(Array.isArray(d?.loc) ? d.loc : [])].reverse().find((part) => typeof part === 'string' && part !== 'body')
        return field ? `${fieldLabel(field)}: ${message}` : message
      })
      .join(' ')
  }
  return fallback
}

export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

/**
 * The server couldn't be reached at all -- no connection, or it's down.
 * Distinct from ApiError (the server answered "no"), because the fix is
 * different: check the connection and try again.
 */
export class NetworkError extends Error {
  constructor(message = 'Couldn’t reach FullHouse. Check your internet connection and try again.') {
    super(message)
  }
}

/**
 * A fresh id for one thing a person is saving -- a delivery, a sale.
 *
 * Make one when the form is filled in and send it with every attempt until
 * the save is confirmed: the server records each id once, so a retry after a
 * dropped connection can't count the delivery twice.
 */
export function newRequestKey(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

/** Pauses before each automatic retry after a dropped connection. */
const RETRY_DELAYS_MS = [700, 2000]

type RequestOptions = RequestInit & { idempotencyKey?: string }

async function request(path: string, options: RequestOptions = {}): Promise<Response> {
  const { idempotencyKey, ...init } = options
  const method = (init.method ?? 'GET').toUpperCase()
  // Only what's safe to send twice is retried: reads, and writes carrying a
  // request id (the server answers a repeat with the first result).
  const retryable = method === 'GET' || Boolean(idempotencyKey)
  let res: Response | undefined
  for (let attempt = 0; ; attempt++) {
    try {
      res = await fetch(`${API_BASE}${path}`, {
        ...init,
        // The session cookie, which in local dev goes to a different port.
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          ...(idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {}),
          ...init.headers,
        },
      })
      break
    } catch {
      // fetch only throws when no response came back at all.
      if (!retryable || attempt >= RETRY_DELAYS_MS.length) {
        throw new NetworkError(
          method === 'GET'
            ? undefined
            : idempotencyKey
              ? 'Couldn’t confirm this was saved because the connection dropped. Try again: it won’t be recorded twice.'
              : 'Couldn’t reach FullHouse, so this may not have saved. Check your connection, then refresh to see.',
        )
      }
      await new Promise((resolve) => window.setTimeout(resolve, RETRY_DELAYS_MS[attempt]))
    }
  }
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith('/api/v1/auth/')) {
      window.dispatchEvent(new Event(SIGNED_OUT_EVENT))
    }
    const body = await res.json().catch(() => null)
    throw new ApiError(errorMessage(body?.detail, `${path} failed: ${res.status}`), res.status)
  }
  return res
}

const get = <T>(path: string) => request(path).then((r) => r.json() as Promise<T>)
const send = <T>(method: string, path: string, body?: unknown, idempotencyKey?: string) =>
  request(path, { method, body: body === undefined ? undefined : JSON.stringify(body), idempotencyKey }).then(
    (r) => r.json() as Promise<T>,
  )

/**
 * What an edit form started from, for the fields it changes. The server
 * refuses the save if someone else changed any of them in the meantime,
 * instead of silently overwriting their change.
 */
export type Expected = Record<string, unknown>
/** For 204 responses, which have no body to parse. */
const sendVoid = (method: string, path: string) => request(path, { method }).then(() => undefined)

// --- agents ------------------------------------------------------------------

export type AgentDefinition = {
  id: string
  key: string
  name: string
  description: string
  /** 'main' for the one agent; 'boss' | 'subagent' only on definitions from before the merge. */
  role: string
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
  /** IANA zone agent schedules run in: the restaurant's once set up, else UTC. */
  schedule_timezone?: string
  /** Why the AI can't run right now -- missing key, unknown provider; null when it can. */
  ai_problem?: string | null
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
  /** Bought in packs: one `pack_unit` (a case) holds `pack_size` of `unit`. Both null otherwise. */
  pack_unit: string | null
  pack_size: number | null
  /** When the item or its stock last changed. */
  updated_at: string
  /** At or below the reorder point. */
  low_stock: boolean
}

/** The units the server accepts, as it spells them (backend/app/restaurant/inventory.py). */
export const UNITS = [
  'each', 'dozen', 'case', 'box', 'pack', 'bag', 'bottle', 'can', 'jar', 'tray',
  'bunch', 'head', 'loaf',
  'g', 'kg', 'oz', 'lb',
  'ml', 'L', 'fl oz', 'qt', 'gal',
] as const

export type InventoryItemInput = {
  name: string
  unit: string
  quantity_on_hand?: number
  reorder_threshold?: number
  reorder_qty?: number
  unit_cost?: number
  supplier_id?: string | null
  pack_unit?: string | null
  pack_size?: number | null
}

/** "case" -> "cases"; measurements (lb, g, each) read the same either way. Mirrors the server. */
export function plural(unit: string, count: number): string {
  if (count === 1 || ['g', 'kg', 'oz', 'lb', 'ml', 'L', 'fl oz', 'qt', 'gal', 'each'].includes(unit)) return unit
  if (unit === 'loaf') return 'loaves'
  return /(x|ch|sh|s)$/.test(unit) ? `${unit}es` : `${unit}s`
}

/** What staff record by hand; sales, approved reorders and undos are recorded for them. */
export type StockReason = 'delivery' | 'used' | 'waste' | 'count'

export type StockAdjustment = {
  id: number
  inventory_item_id: string
  item_name: string
  unit: string
  /** Signed: positive came in, negative went out. */
  change: number
  quantity_after: number
  reason: StockReason | 'initial' | 'sale' | 'reorder' | 'revert' | 'correction'
  reason_label: string
  note: string | null
  actor_type: 'human' | 'agent' | 'system'
  actor_id: string | null
  actor_name: string | null
  created_at: string
}

export type PurchaseOrderStatus = 'ordered' | 'shipped' | 'received' | 'cancelled'

/** An order placed with a supplier, followed from approval to receipt. Placing it moves no stock; receiving it does. */
export type PurchaseOrder = {
  id: string
  inventory_item_id: string
  item_name: string
  unit: string
  /** In the item's unit; `packs` and `pack_unit` say how it was ordered when known. */
  quantity: number
  packs: number | null
  pack_unit: string | null
  supplier_id: string | null
  supplier_name: string | null
  estimated_cost: number | null
  status: PurchaseOrderStatus
  ordered_at: string
  expected_on: string | null
  shipped_at: string | null
  received_at: string | null
  received_qty: number | null
  overdue: boolean
  /** Received by its invoice arriving rather than by hand. */
  received_by_invoice: boolean
  action_id: string | null
  note: string | null
  placed_by: string | null
  received_by: string | null
}

export type PurchaseOrderInput = {
  inventory_item_id: string
  supplier_id?: string | null
  quantity: number
  in_packs?: boolean
  expected_on?: string | null
  note?: string | null
}

const ORDERS = '/api/v1/restaurant/purchase-orders'
export const listPurchaseOrders = (status: 'open' | 'all' | PurchaseOrderStatus = 'open') =>
  get<PurchaseOrder[]>(`${ORDERS}?status=${status}`)
/** An order placed by phone, email or a portal, recorded so its arrival can be tracked and booked. */
export const placePurchaseOrder = (input: PurchaseOrderInput) => send<PurchaseOrder>('POST', ORDERS, input)
export const markOrderShipped = (id: string, expected_on?: string | null) =>
  send<PurchaseOrder>('POST', `${ORDERS}/${id}/shipped`, { expected_on: expected_on ?? null })
/** What arrived goes into stock, once, and the order closes. `quantity` unset means all of it. */
export const receiveOrder = (id: string, quantity?: number | null, note?: string | null) =>
  send<{ order: PurchaseOrder; item: InventoryItem }>('POST', `${ORDERS}/${id}/receive`, { quantity: quantity ?? null, note: note ?? null })
export const cancelOrder = (id: string) => send<PurchaseOrder>('POST', `${ORDERS}/${id}/cancel`)

export type Supplier = { id: string; name: string; contact_info: string | null; lead_time_days: number }
export type SupplierInput = { name: string; contact_info?: string | null; lead_time_days?: number }

export type SupplyChain = {
  /** Days of stock beyond lead time the owner wants held, from Restaurant Settings. */
  safety_days?: number
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
export const createInventoryItem = (input: InventoryItemInput) =>
  send<InventoryItem>('POST', '/api/v1/restaurant/inventory-items', input)
/** An item's details, for owners and admins. Stock changes only through `recordStockChange`. */
export const updateInventoryItem = (
  id: string,
  patch: Partial<Omit<InventoryItemInput, 'quantity_on_hand'>>,
  expected?: Expected,
) => send<InventoryItem>('PATCH', `/api/v1/restaurant/inventory-items/${id}`, { ...patch, expected })
/**
 * A delivery, use, waste or count. `quantity` is the amount moved -- or, for a
 * count, the level counted. `adjustment` is null when a count matched.
 */
export const recordStockChange = (
  id: string,
  reason: StockReason,
  quantity: number,
  note?: string,
  /** From `newRequestKey()`: the same one for every attempt at this change. */
  requestKey?: string,
  /** `quantity` is in the item's packs (cases) rather than its unit. */
  inPacks = false,
) =>
  send<{ item: InventoryItem; adjustment: StockAdjustment | null }>(
    'POST',
    `/api/v1/restaurant/inventory-items/${id}/adjustments`,
    { reason, quantity, note: note || null, in_packs: inPacks },
    requestKey,
  )

export type ImportRow = {
  /** The spreadsheet's own line number, headings being line 1. */
  line: number
  name: string | null
  status: 'new' | 'update' | 'skip' | 'error'
  messages: string[]
  unit: string | null
  quantity_on_hand: number | null
  supplier: string | null
}

export type ImportResult = {
  rows: ImportRow[]
  new: number
  updated: number
  skipped: number
  errors: number
  /** Suppliers named in the file that don't exist yet, and will be added. */
  new_suppliers: string[]
  /** Carried out: not a preview, and no row had a problem. */
  saved: boolean
}

/**
 * Stock from a spreadsheet, as CSV text. A dry run previews; a real run saves
 * every row, or none if any has a problem. Owners and admins.
 */
export const importInventory = (csv: string, options: { dryRun: boolean; updateExisting: boolean }) =>
  send<ImportResult>('POST', '/api/v1/restaurant/inventory-items/import', {
    csv,
    dry_run: options.dryRun,
    update_existing: options.updateExisting,
  })
/** Newest first; one item's with `itemId`. */
export const listStockChanges = (itemId?: string, limit = 25) =>
  get<StockAdjustment[]>(
    `/api/v1/restaurant/inventory-adjustments?limit=${limit}${itemId ? `&inventory_item_id=${encodeURIComponent(itemId)}` : ''}`,
  )

export const listSuppliers = () => get<Supplier[]>('/api/v1/restaurant/suppliers')
export const fetchSupplyChain = () => get<SupplyChain>('/api/v1/restaurant/supply-chain')
export const createSupplier = (input: SupplierInput) =>
  send<Supplier>('POST', '/api/v1/restaurant/suppliers', input)
export const updateSupplier = (id: string, patch: Partial<SupplierInput>, expected?: Expected) =>
  send<Supplier>('PATCH', `/api/v1/restaurant/suppliers/${id}`, { ...patch, expected })
export const deleteSupplier = (id: string) => sendVoid('DELETE', `/api/v1/restaurant/suppliers/${id}`)

// --- staff ------------------------------------------------------------------------

/**
 * An employee as the signed-in person may see them: coworkers come with a
 * name and role only; contact details are for managers and the person
 * themselves; `notes` and `has_account` are for managers.
 */
export type Staff = {
  id: string
  name: string
  role: string
  is_me: boolean
  email: string | null
  phone: string | null
  emergency_contact: string | null
  hire_date: string | null
  notes: string | null
  has_account: boolean | null
}
export type StaffInput = {
  name: string
  role: string
  email?: string | null
  phone?: string | null
  emergency_contact?: string | null
  hire_date?: string | null
  notes?: string | null
}
/** `published_at` is null while the shift is a draft, which only managers see. */
export type Shift = { id: number; staff_id: string; date: string; start_time: string; end_time: string; role: string; published_at: string | null }
export type ShiftInput = Omit<Shift, 'id' | 'published_at'>

export const listStaff = () => get<Staff[]>('/api/v1/restaurant/staff')
export const createStaff = (input: StaffInput) => send<Staff>('POST', '/api/v1/restaurant/staff', input)
export const updateStaff = (id: string, patch: Partial<StaffInput>) =>
  send<Staff>('PATCH', `/api/v1/restaurant/staff/${id}`, patch)
export const deleteStaff = (id: string) => sendVoid('DELETE', `/api/v1/restaurant/staff/${id}`)

export const listShifts = () => get<Shift[]>('/api/v1/restaurant/shifts')
export const createShift = (input: ShiftInput) => send<Shift>('POST', '/api/v1/restaurant/shifts', input)
export const deleteShift = (id: number) => sendVoid('DELETE', `/api/v1/restaurant/shifts/${id}`)
export const publishShifts = () => send<{ published: number }>('POST', '/api/v1/restaurant/shifts/publish', {})

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
export const updateMenuItem = (id: string, patch: Partial<MenuItemInput>, expected?: Expected) =>
  send<MenuItem>('PATCH', `/api/v1/restaurant/menu-items/${id}`, { ...patch, expected })
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

// --- auth ----------------------------------------------------------------------

/** A bookkeeper belongs to no restaurant: they answer the agent's questions for several clients. */
export type Role = 'owner' | 'admin' | 'member' | 'bookkeeper'

export const canEditRestaurant = (user: User) => user.role === 'owner' || user.role === 'admin'

export type User = {
  id: string
  email: string
  name: string
  /** Owners and admins may edit the restaurant; members can only see it. */
  role: Role
  /** Null only for an account from before signing up created its restaurant. */
  restaurant_id: string | null
  created_at: string
  /** Set while an owner or admin has turned the account off. */
  disabled_at?: string | null
  mfa_enabled?: boolean
  /** An owner or admin who must set up two-step sign-in before anything else. */
  mfa_setup_required?: boolean
  mfa_backup_codes_left?: number | null
}

export type AuthOptions = {
  /** Whether a new restaurant can sign up here. A restaurant's team joins by invite either way. */
  signup_enabled: boolean
  /** The public demo's shared account; null on real deployments. */
  demo: { email: string; password: string } | null
}

export const fetchAuthOptions = () => get<AuthOptions>('/api/v1/auth/options')
/** Resolves to null when nobody is signed in, rather than throwing. */
export const fetchCurrentUser = () =>
  get<User>('/api/v1/auth/me').catch((err: unknown) => {
    if (err instanceof ApiError && err.status === 401) return null
    throw err
  })
/** The password was right; a code from the authenticator app comes next. */
export type MfaRequired = { mfa_required: true }
export const login = (email: string, password: string) =>
  send<User | MfaRequired>('POST', '/api/v1/auth/login', { email, password })
export const loginSecondStep = (code: string) => send<User>('POST', '/api/v1/auth/login/mfa', { code })

export type MfaSetup = { secret: string; otpauth_uri: string; qr_svg: string }
/** Once two-step is on, changing it takes a current code. */
export const startMfaSetup = (code?: string) => send<MfaSetup>('POST', '/api/v1/auth/mfa/setup', { code: code ?? null })
/** Returns the backup codes -- shown once. */
export const enableMfa = (code: string) => send<{ codes: string[] }>('POST', '/api/v1/auth/mfa/enable', { code })
export const newBackupCodes = (code: string) => send<{ codes: string[] }>('POST', '/api/v1/auth/mfa/backup-codes', { code })
export const disableMfa = (code: string) => send<User>('POST', '/api/v1/auth/mfa/disable', { code })
/** For a lost phone: owners for admins and members, admins for members. */
export const resetTeammateMfa = (userId: string) => send<User>('POST', `/api/v1/auth/team/${userId}/reset-mfa`)
/** Opens a new restaurant, with this account as its owner. Joining an existing one takes an invite. */
export const signup = (name: string, email: string, password: string, restaurantName: string) =>
  send<User>('POST', '/api/v1/auth/signup', { name, email, password, restaurant_name: restaurantName })
export const logout = () => sendVoid('POST', '/api/v1/auth/logout')

/** Everyone who can sign in to this restaurant. Owners and admins only. */
export const listTeam = () => get<User[]>('/api/v1/auth/team')
/** Owners only; an owner's own role can't be changed. */
export const setTeamRole = (userId: string, role: Exclude<Role, 'owner'>) =>
  send<User>('PATCH', `/api/v1/auth/team/${userId}`, { role })
/** Ends every session they have, at once. Owners manage admins and members; admins manage members. */
export const disableAccess = (userId: string) => send<User>('POST', `/api/v1/auth/team/${userId}/disable`)
export const enableAccess = (userId: string) => send<User>('POST', `/api/v1/auth/team/${userId}/enable`)
/** Ends every session this account has, this browser's included. */
export const logoutEverywhere = () => sendVoid('POST', '/api/v1/auth/logout-everywhere')

/** A link someone opens to get in: `#invite/<token>` or `#reset/<token>`. */
export const linkFor = (kind: 'invite' | 'reset', token: string) => `${window.location.origin}${window.location.pathname}#${kind}/${token}`

export type Invite = { id: string; role: Exclude<Role, 'owner'>; email: string | null; created_at: string; expires_at: string }
/** The token is shown once: only its hash is kept. */
export const createInvite = (role: Exclude<Role, 'owner'>, email: string | null) =>
  send<Invite & { token: string }>('POST', '/api/v1/auth/invites', { role, email })
export const listInvites = () => get<Invite[]>('/api/v1/auth/invites')
export const revokeInvite = (id: string) => sendVoid('DELETE', `/api/v1/auth/invites/${id}`)
export const previewInvite = (token: string) =>
  get<{ restaurant_name: string | null; role: Exclude<Role, 'owner'>; email: string | null }>(`/api/v1/auth/invite/${token}`)
export const acceptInvite = (token: string, name: string, email: string, password: string) =>
  send<User>('POST', `/api/v1/auth/invite/${token}`, { name, email, password })

/** A one-time link, valid for an hour, to set a new password. */
export const createResetLink = (userId: string) =>
  send<{ token: string; expires_at: string }>('POST', `/api/v1/auth/team/${userId}/reset-link`)
export const previewReset = (token: string) => get<{ name: string; email: string }>(`/api/v1/auth/reset/${token}`)
export const resetPassword = (token: string, password: string) => send<User>('POST', `/api/v1/auth/reset/${token}`, { password })

export type SecurityEvent = {
  id: number
  ts: string
  type: string
  label: string
  email: string | null
  actor_email: string | null
  ip: string | null
  user_agent: string | null
  detail: Record<string, unknown> | null
}
export const listSecurityEvents = () => get<SecurityEvent[]>('/api/v1/auth/security-events')

// --- restaurant profile ------------------------------------------------------------

export const WEEKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const
export type Weekday = (typeof WEEKDAYS)[number]

export type DayHours = { closed: boolean; open: string | null; close: string | null }

export type RestaurantDetails = {
  name: string
  street: string
  city: string
  region: string | null
  postal_code: string | null
  country: string
  phone: string
  email: string | null
  website: string | null
}
export type RestaurantHours = { timezone: string; opening_hours: Record<Weekday, DayHours> }
export type InventorySettings = {
  safety_days: number
  default_lead_time_days: number
  /** Local "HH:MM" for the Inventory Agent's daily check; null for none. */
  daily_stock_check: string | null
}
export type StaffSettings = { staff_roles: string[] }
export type SchedulingRules = {
  max_shift_hours: number
  min_rest_hours: number
  max_weekly_hours: number
  min_staff_per_open_day: number
}

/** Setup order: each is a wizard step and a Settings card. */
export const SECTIONS = ['details', 'hours', 'inventory', 'staff', 'scheduling'] as const
export type Section = (typeof SECTIONS)[number]

export type SectionValues = {
  details: RestaurantDetails
  hours: RestaurantHours
  inventory: InventorySettings
  staff: StaffSettings
  scheduling: SchedulingRules
}

export type Restaurant = {
  id: string
  name: string | null
  street: string | null
  city: string | null
  region: string | null
  postal_code: string | null
  country: string | null
  phone: string | null
  email: string | null
  website: string | null
  timezone: string
  opening_hours: Record<Weekday, DayHours> | null
  inventory_settings: InventorySettings | null
  staff_roles: string[] | null
  scheduling_rules: SchedulingRules | null
  completed_steps: Section[]
  setup_completed_at: string | null
  /** When anything in the profile was last saved. */
  updated_at: string
  /** Who saved last; only sent to owners and admins. */
  updated_by: { id: string; name: string; email: string } | null
  /** Whether the signed-in user may change it. */
  can_edit: boolean
}

/** Null before the first setup step is saved. */
export const fetchRestaurant = () => get<Restaurant | null>('/api/v1/restaurant-profile')
export const saveSection = <S extends Section>(section: S, values: SectionValues[S]) =>
  send<Restaurant>('PUT', `/api/v1/restaurant-profile/${section}`, values)
export const completeSetup = () => send<Restaurant>('POST', '/api/v1/restaurant-profile/complete')
/**
 * The Profile page's Restaurant Details tab, saved all at once. Pass the
 * `updated_at` the form loaded: if someone saved since, this fails with 409
 * instead of overwriting their change.
 */
export const saveRestaurantDetails = (values: RestaurantDetails & RestaurantHours, expectedUpdatedAt: string) =>
  send<Restaurant>('PUT', '/api/v1/restaurant-profile/restaurant-details', {
    ...values,
    expected_updated_at: expectedUpdatedAt,
  })

// --- change history ---------------------------------------------------------------

export type Change = {
  id: number
  ts: string
  entity_type: string
  entity_id: string
  operation: 'insert' | 'update' | 'delete'
  /** For an update, only the fields that changed. */
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
  changed_fields: string[]
  actor_type: 'human' | 'agent' | 'system'
  /** An email for a person, the agent's key for the agent. */
  actor_id: string | null
  agent_run_id: string | null
  /** Set when the change carried out an approved proposal. */
  agent_action_id: string | null
  note: string | null
  /** What it was to, in words: "Tomatoes", "Sam Patel's shift on 2026-10-06". */
  entity_label: string | null
}

export type ChangePage = { total: number; limit: number; offset: number; items: Change[] }

export const listChanges = (params: { limit?: number; offset?: number; entity_type?: string; actor_type?: string } = {}) => {
  const query = new URLSearchParams(
    Object.entries(params)
      .filter(([, v]) => v !== undefined && v !== '')
      .map(([k, v]) => [k, String(v)]),
  )
  return get<ChangePage>(`/api/v1/changes?${query}`)
}

/**
 * Undo one change -- itself recorded as a change. Refused (409) if the record
 * has changed since; `force` undoes it anyway.
 */
export const revertChange = (id: number, force = false) =>
  send<{ reverted_change_id: number; description: string; resulting_change_ids: number[] }>(
    'POST',
    `/api/v1/changes/${id}/revert`,
    { force },
  )

/** The newest change this person can see; it moves whenever anything they could see changes. */
export const fetchChangeVersion = () => get<{ version: number; latest_at: string | null }>('/api/v1/changes/version')

// --- support -------------------------------------------------------------------------

export type SupportInfo = { support_email: string | null; support_url: string | null }
export type FeedbackEntry = { id: number; sent_by: string; page: string | null; message: string; created_at: string }

export const fetchSupportInfo = () => get<SupportInfo>('/api/v1/support')
export const sendFeedback = (message: string, page: string | null) =>
  send<FeedbackEntry>('POST', '/api/v1/support/feedback', { message, page })
/** What the team has sent. Owners and admins. */
export const listFeedback = () => get<FeedbackEntry[]>('/api/v1/support/feedback')

/** Everything the restaurant keeps here, as a zip of CSV files, saved by the browser. Owners and admins. */
export async function downloadExport(): Promise<void> {
  const res = await request('/api/v1/support/export')
  const blob = await res.blob()
  const name = /filename="([^"]+)"/.exec(res.headers.get('content-disposition') ?? '')?.[1] ?? 'fullhouse-export.zip'
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

/**
 * Tell the server about a crash in this browser, so it's alerted on like a
 * server error. Deliberately quiet: its own plain request (a failure here
 * must never sign anyone out or show an error), and never throws.
 */
export function reportClientError(message: string, stack?: string): void {
  fetch(`${API_BASE}/api/v1/support/client-errors`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message: message.slice(0, 500),
      stack: stack?.slice(0, 4000) ?? null,
      page: window.location.hash.replace(/^#\/?/, '').slice(0, 255) || null,
    }),
    keepalive: true,
  }).catch(() => {})
}

// --- customer service requests ---------------------------------------------------

export const REQUEST_CHANNELS = ['in_person', 'phone', 'email', 'online_review', 'social_media', 'other'] as const
export const REQUEST_KINDS = ['complaint', 'question', 'request', 'compliment', 'other'] as const
export type RequestChannel = (typeof REQUEST_CHANNELS)[number]
export type RequestKind = (typeof REQUEST_KINDS)[number]

/** One thing a customer told the restaurant. `message` is the original, and never changes. */
export type CustomerRequest = {
  id: string
  received_at: string
  channel: RequestChannel
  kind: RequestKind
  customer_name: string | null
  customer_contact: string | null
  message: string
  logged_by: string
  created_at: string
  status: 'open' | 'resolved'
  resolved_at: string | null
  resolved_by: string | null
  resolution_note: string | null
}

export type NewCustomerRequest = {
  message: string
  channel: RequestChannel
  kind: RequestKind
  customer_name: string
  customer_contact: string
}

export type CustomerActionItem = {
  id: string
  title: string
  details: string | null
  priority: 'high' | 'normal' | 'low'
  /** The requests it comes from. */
  request_ids: string[]
  status: 'open' | 'done'
  done_at: string | null
  done_by: string | null
}

/** The Assistant Manager's summary of the open requests from one run, and what it suggests doing. */
export type CustomerServiceReview = {
  id: string
  run_id: string | null
  summary: string
  request_ids: string[]
  created_at: string
  action_items: CustomerActionItem[]
}

/** Anyone on the team can write a request down. */
export const logCustomerRequest = (body: NewCustomerRequest) =>
  send<{ id: string; received_at: string }>('POST', '/api/v1/customer-requests', body)
/** Owners and admins, as are the rest. */
export const listCustomerRequests = () => get<CustomerRequest[]>('/api/v1/customer-requests')
export const resolveCustomerRequest = (id: string, note: string) =>
  send<CustomerRequest>('POST', `/api/v1/customer-requests/${id}/resolve`, { note })
export const reopenCustomerRequest = (id: string) => send<CustomerRequest>('POST', `/api/v1/customer-requests/${id}/reopen`)
export const listCustomerReviews = () => get<CustomerServiceReview[]>('/api/v1/customer-requests/reviews')
/** Starts the Assistant Manager summarising the open requests; the review appears when the run finishes. */
export const startCustomerReview = () => send<AgentRun>('POST', '/api/v1/customer-requests/reviews')
export const setCustomerActionItemDone = (id: string, done: boolean) =>
  send<CustomerActionItem>('PATCH', `/api/v1/customer-requests/action-items/${id}`, { done })

// --- prices: invoices, the digest, the agent's questions, drafts ------------------------
// The shapes mirror backend/app/prices/schemas.py; dates are ISO strings.

const PRICES = '/api/v1/prices'

export type SupplierRef = { id: string; name: string }
export type PriceCategory = 'food' | 'dry' | 'paper' | 'beer' | 'wine' | 'liquor'
export type InvoiceSource = 'billcom' | 'email' | 'photo' | 'pdf' | 'typed'
export type DigestSectionKey = 'switch' | 'up' | 'owed'
export type DraftKind = 'price_challenge' | 'credit_claim' | 'short_delivery'

export type Reader = 'claude' | 'openai' | 'text'

/** Whether this site reads a photo of an invoice, or only a PDF. */
export const readsPhotos = (reader: Reader | null | undefined) => reader === 'claude' || reader === 'openai'

export type PriceStatus = {
  invoice_count: number
  suppliers: SupplierRef[]
  first_digest: string | null
  questions_open: number
  progress: string
  forward_address: string | null
  /** What reads a photo or PDF into lines here. Only Claude and a model on the OpenAI-compatible API read
   * photos; 'text' (no model) reads PDFs that carry text. */
  reader: Reader
  billcom_configured: boolean
  digest_email_to: string | null
}

/** A chip under an agent message: the invoice or item it used. */
export type MessageSource = { kind: 'invoice' | 'item' | 'digest'; id: string; label: string }

export type CardAction = { kind: 'move' | 'draft'; label: string; draft_kind: DraftKind | null; supplier_id: string | null }

export type DigestCard = {
  section: DigestSectionKey
  item_id: string
  item_name: string
  impact: number
  sentence: string
  action: CardAction
  sources: MessageSource[]
}

export type DigestSection = { key: DigestSectionKey; label: string; total: number; cards: DigestCard[] }

/** The week's prime cost at the top of the digest, or what to connect to see it. */
export type PrimeLine = {
  week_of: string
  complete: boolean
  prime_pct: number | null
  food_pct: number | null
  paper_pct: number | null
  labor_pct: number | null
  sales_net: number | null
  labor_dollars: number | null
  purchases: number
  prompt: string | null
}

export type Digest = {
  id: string
  week_start: string
  /** Present when connections are switched on for the restaurant. */
  prime: PrimeLine | null
  total: number
  item_count: number
  supplier_count: number
  sections: DigestSection[]
  hidden_count: number
  questions_open: number
  built_at: string
  invoices_through: string | null
  supplier_names: string[]
}

export type TalkingPoints = { lines: string[]; sources: MessageSource[] }

export type ItemState = 'ok' | 'insufficient' | 'awaiting' | 'hidden'

export type PriceItem = {
  id: string
  name: string
  category: PriceCategory | string
  pack_size: number
  pack_unit: string
  unit: string
  state: ItemState
  last_price: number | null
  last_supplier: SupplierRef | null
  last_date: string | null
  change_8w: number | null
  hidden_until: string | null
  questions_open: number
}

export type SeriesPoint = { week_start: string; price: number | null }
export type PriceSeries = { supplier: SupplierRef; current: boolean; points: SeriesPoint[] }

export type InvoiceLine = {
  id: string
  invoice_id: string
  invoice_date: string
  supplier: SupplierRef
  description: string
  qty: number
  pack_text: string | null
  unit_price: number
  per_unit: number | null
  pack_size: number | null
  extended: number
  delivered_qty: number | null
  credited: number
}

/** An open purchase order, as the price pages mention it. */
export type OrderBrief = {
  id: string
  quantity: number
  unit: string
  supplier_name: string | null
  status: 'ordered' | 'shipped'
  expected_on: string | null
  overdue: boolean
}

/** The inventory item a price item is counted as, and how it stands. */
export type StockLink = {
  inventory_item_id: string
  name: string
  unit: string
  quantity_on_hand: number
  reorder_threshold: number
  low_stock: boolean
  /** False when the invoice's unit can't be read in the stock unit (lb against L): nothing is booked then. */
  converts: boolean
  /** A reorder waiting under Recommendations for this stock item. */
  pending_action_id: string | null
  /** An order already placed and not yet received. */
  on_order: OrderBrief | null
}

export type PriceItemDetail = PriceItem & {
  pack_sentence: string
  card: DigestCard | null
  note: string | null
  series: PriceSeries[]
  lines: InvoiceLine[]
  moved_to: SupplierRef | null
  stock: StockLink | null
  /** "owner" once the owner chose what it is counted as; "auto" while the name match stands. */
  inventory_link: 'auto' | 'owner'
}

/** For the Inventory page: what the invoices say about one stock item. */
export type StockRow = {
  inventory_item_id: string
  item_id: string
  item_name: string
  unit: string
  converts: boolean
  /** The last price paid, per unit of stock. */
  per_unit: number | null
  last_supplier: SupplierRef | null
  last_date: string | null
  pending_action_id: string | null
  on_order: OrderBrief | null
}

export type QuestionOption = { key: string; label: string }
export type CropLine = { text: string; hit: boolean }

export type AgentQuestion = {
  id: string
  kind: 'match' | 'pack' | 'repack'
  text: string
  options: QuestionOption[]
  crop: CropLine[]
  has_image: boolean
  supplier: SupplierRef
  invoice_id: string
  invoice_date: string
  line_id: string
  dollar_impact: number
  status: 'open' | 'answered' | 'held'
  answer: string | null
  created_at: string
}

export type QuestionAnswer = { answer: 'yes' | 'no' | 'other'; text?: string | null; pack_size?: number | null }

export type DraftSummary = { id: string; status: DraftStatus; subject: string; to_name: string | null; supplier: SupplierRef }
export type DraftStatus = 'draft' | 'sent' | 'discarded'

export type ThreadMessage = {
  id: string
  role: 'you' | 'agent'
  text: string
  sources: MessageSource[]
  question: AgentQuestion | null
  draft: DraftSummary | null
  status: 'ok' | 'pending' | 'failed'
  created_at: string
}

export type DraftLine = { line_id: string; label: string }

export type OutreachDraft = {
  id: string
  status: DraftStatus
  kind: DraftKind
  supplier: SupplierRef
  item_id: string
  item_name: string
  to_name: string | null
  to_email: string | null
  remembered_from: string | null
  subject: string
  body: string
  lines: DraftLine[]
  created_at: string
  sent_at: string | null
}

export type DraftPatch = { to_name?: string | null; to_email?: string | null; subject?: string; body?: string }

export type NewInvoiceLine = {
  description: string
  qty: number
  pack_text?: string | null
  unit_price: number
  extended?: number | null
  delivered_qty?: number | null
  /** How sure the reader was of this line, 0-1. Absent for a line a person typed. */
  confidence?: number | null
}

/** A photo or PDF read into lines. Nothing is saved until the person checks and saves. */
export type ReadInvoice = {
  supplier_guess: string | null
  date_guess: string | null
  reference: string | null
  total: number | null
  lines: NewInvoiceLine[]
  needs_review: boolean
  reasons: string[]
  reader: Reader
}

export type ParsedInvoice = { lines: NewInvoiceLine[]; skipped: number[]; supplier_guess: string | null; date_guess: string | null }

export type NewInvoice = {
  supplier_id?: string | null
  supplier_name?: string | null
  invoice_date: string
  reference?: string | null
  source: InvoiceSource
  lines: NewInvoiceLine[]
  /** The total printed on the invoice, when known. */
  total?: number | null
}

export type InvoiceSaved = { id: string; line_count: number; matched: number; questions_asked: number; first_question: AgentQuestion | null }

export type InvoiceRow = {
  id: string
  supplier: SupplierRef
  invoice_date: string
  reference: string | null
  source: string
  line_count: number
  total: number
  printed_total: number | null
  unmatched: number
  status: 'complete' | 'needs_review'
  review_reasons: string[]
}

export type EmailOut = { subject: string; sent_to: string[]; delivered: boolean }
export type BillcomStatus = { configured: boolean; environment: 'sandbox' | 'production' | null; last_synced_at: string | null; webhook_path: string }
export type BillcomSync = { pulled: number; added: number; skipped: number; questions_asked: number }

export type BookkeeperClient = { restaurant_id: string; name: string; questions_open: number; last_digest: string | null; flagged_month: number }

export const fetchPriceStatus = () => get<PriceStatus>(`${PRICES}/status`)
export const fetchDigest = () => get<Digest>(`${PRICES}/digest`)
export const rebuildDigest = () => send<Digest>('POST', `${PRICES}/digest/rebuild`)
export const fetchTalkingPoints = () => get<TalkingPoints>(`${PRICES}/digest/talking-points`)

export const listPriceItems = () => get<PriceItem[]>(`${PRICES}/items`)
export const getPriceItem = (id: string) => get<PriceItemDetail>(`${PRICES}/items/${id}`)
export const setPackSize = (id: string, pack_size: number, pack_unit?: string | null) =>
  send<PriceItemDetail>('PATCH', `${PRICES}/items/${id}`, { pack_size, pack_unit: pack_unit ?? null })
/** What the item is counted as in Inventory; null for "not counted". The owner's word over the name match. */
export const setInventoryLink = (id: string, inventory_item_id: string | null) =>
  send<PriceItemDetail>('PATCH', `${PRICES}/items/${id}`, { inventory_item_id })
export const listStockLinks = () => get<StockRow[]>(`${PRICES}/stock`)
/** "Not useful": out of the digest for four weeks. The only feedback there is. */
export const hidePriceItem = (id: string) => send<PriceItem>('POST', `${PRICES}/items/${id}/hide`)
export const unhidePriceItem = (id: string) => send<PriceItem>('POST', `${PRICES}/items/${id}/unhide`)
/** Records the owner's decision to move an item. Nothing is ordered. */
export const movePriceItem = (id: string, supplier_id: string) => send<PriceItem>('POST', `${PRICES}/items/${id}/move`, { supplier_id })

export const listQuestions = () => get<AgentQuestion[]>(`${PRICES}/questions`)
export const answerQuestion = (id: string, body: QuestionAnswer) => send<AgentQuestion>('POST', `${PRICES}/questions/${id}/answer`, body)

export const fetchThread = () => get<ThreadMessage[]>(`${PRICES}/thread`)
export const askAgent = (text: string, item_id?: string | null) =>
  send<ThreadMessage[]>('POST', `${PRICES}/thread`, { text, item_id: item_id ?? null })

export const createDraft = (item_id: string, kind?: DraftKind | null, supplier_id?: string | null) =>
  send<OutreachDraft>('POST', `${PRICES}/drafts`, { item_id, kind: kind ?? null, supplier_id: supplier_id ?? null })
export const getDraft = (id: string) => get<OutreachDraft>(`${PRICES}/drafts/${id}`)
export const updateDraft = (id: string, patch: DraftPatch) => send<OutreachDraft>('PATCH', `${PRICES}/drafts/${id}`, patch)
/** Marks it sent and remembers the rep. The mail itself goes from the owner's mail app. */
export const sendDraft = (id: string) => send<OutreachDraft>('POST', `${PRICES}/drafts/${id}/send`)
export const discardDraft = (id: string) => send<OutreachDraft>('POST', `${PRICES}/drafts/${id}/discard`)

export const parseInvoiceText = (text: string) => send<ParsedInvoice>('POST', `${PRICES}/invoices/parse`, { text })
export const saveInvoice = (body: NewInvoice) => send<InvoiceSaved>('POST', `${PRICES}/invoices`, body)
export const listInvoices = () => get<InvoiceRow[]>(`${PRICES}/invoices`)
/** "Looks right": an invoice that needed a check now counts. */
export const confirmInvoice = (id: string) => send<InvoiceRow>('POST', `${PRICES}/invoices/${id}/confirm`)

const READ_TYPES: Record<string, string> = { pdf: 'application/pdf', jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', webp: 'image/webp', gif: 'image/gif' }

/** What the server's reader accepts, from the file's own type or its extension. */
export function readableMediaType(file: File): string | null {
  const own = (file.type || '').toLowerCase()
  if (Object.values(READ_TYPES).includes(own)) return own
  const ext = (file.name.split('.').pop() ?? '').toLowerCase()
  return READ_TYPES[ext] ?? null
}

function fileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error('Couldn’t read that file.'))
    reader.onload = () => {
      const url = String(reader.result ?? '')
      resolve(url.slice(url.indexOf(',') + 1))
    }
    reader.readAsDataURL(file)
  })
}

/** A photo or PDF of one invoice, read into lines on the server. Nothing is saved. */
export async function readInvoiceFile(file: File): Promise<ReadInvoice> {
  const media_type = readableMediaType(file)
  if (!media_type) throw new Error('That isn’t a PDF or a photo. Use a PDF, JPG, PNG, WebP or GIF.')
  const data_base64 = await fileAsBase64(file)
  return send<ReadInvoice>('POST', `${PRICES}/invoices/read`, { filename: file.name || null, media_type, data_base64 })
}

/** This week's digest, mailed to the signed-in person now. */
export const emailDigest = () => send<EmailOut>('POST', `${PRICES}/digest/email`)

export const fetchBillcomStatus = () => get<BillcomStatus>(`${PRICES}/billcom/status`)
/** Pulls bills from Bill.com. Idempotent: a bill already in is skipped. */
export const syncBillcom = () => send<BillcomSync>('POST', `${PRICES}/billcom/sync`)

export const listBookkeeperClients = () => get<BookkeeperClient[]>(`${PRICES}/bookkeeper/clients`)
export const listClientQuestions = (restaurantId: string) => get<AgentQuestion[]>(`${PRICES}/bookkeeper/clients/${restaurantId}/questions`)
export const answerClientQuestion = (restaurantId: string, id: string, body: QuestionAnswer) =>
  send<AgentQuestion>('POST', `${PRICES}/bookkeeper/clients/${restaurantId}/questions/${id}/answer`, body)


// --- v2: feature flags and connections --------------------------------------------------------

export type Flags = { flags: Record<string, boolean>; descriptions: Record<string, string> }
export const fetchFlags = () => get<Flags>('/api/v1/platform/flags')
export const setFlag = (flag: string, enabled: boolean) => send<Flags>('PUT', '/api/v1/platform/flags', { flag, enabled })

export type Provider = {
  provider: string
  kind: 'pos' | 'accounting' | 'payroll' | 'ap' | 'email'
  label: string
  auth: 'oauth' | 'api_key' | 'none' | 'partner'
  capabilities: string[]
  available: boolean
  notes: string | null
}
export type Connection = {
  id: string
  provider: string
  kind: string
  status: 'active' | 'error' | 'revoked'
  last_sync_at: string | null
  error: string | null
  settings: Record<string, unknown>
}
export type Inbound = { address: string | null; allowed_senders: string[]; last_refused_sender: string | null }
export type ConnectOverview = { providers: Provider[]; connections: Connection[]; inbound: Inbound; csv_profiles: Record<string, unknown>[] }
export type CsvPlan = {
  format: string
  capability: 'sales_days' | 'sales_items' | 'labor_days'
  mapping: Record<string, string>
  headers: string[]
  row_count: number
  days: number
  first_date: string | null
  last_date: string | null
  preview: Record<string, unknown>[]
  problems: { line: number; message: string }[]
  ok: boolean
  fields: { field: string; required: boolean }[]
}
export type CsvIn = { text: string; capability?: string | null; mapping?: Record<string, string> | null; save_profile?: boolean }

const CONNECT = '/api/v1/connect'
export const fetchConnect = () => get<ConnectOverview>(CONNECT)
export const csvPreview = (input: CsvIn) => send<CsvPlan>('POST', `${CONNECT}/csv/preview`, input)
export const csvImport = (input: CsvIn) => send<{ written: Record<string, number>; plan: CsvPlan }>('POST', `${CONNECT}/csv/import`, input)
export const setInbound = (allowed_senders: string[]) => send<Inbound>('PUT', `${CONNECT}/inbound`, { allowed_senders })
export const oauthStart = (provider: string) => get<{ url: string }>(`${CONNECT}/oauth/${provider}/start`)
export const syncNow = (provider: string) => send<{ queued: boolean }>('POST', `${CONNECT}/${provider}/sync`)
export const disconnect = (connectionId: string) => send<{ ok: boolean }>('DELETE', `${CONNECT}/connections/${connectionId}`)
export const pushInvoiceToAccounting = (invoiceId: string) => send<{ queued: boolean; already_pushed: boolean }>('POST', `${CONNECT}/accounting/push/${invoiceId}`)
