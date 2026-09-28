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
) =>
  send<{ item: InventoryItem; adjustment: StockAdjustment | null }>(
    'POST',
    `/api/v1/restaurant/inventory-items/${id}/adjustments`,
    { reason, quantity, note: note || null },
    requestKey,
  )
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

export type Role = 'owner' | 'admin' | 'member'

export const canEditRestaurant = (user: User) => user.role === 'owner' || user.role === 'admin'

export type User = {
  id: string
  email: string
  name: string
  /** Owners and admins may edit the restaurant; members can only see it. */
  role: Role
  /** Null until the owner saves the first setup step. */
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
export const signup = (name: string, email: string, password: string) =>
  send<User>('POST', '/api/v1/auth/signup', { name, email, password })
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
