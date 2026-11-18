import { get, send } from '../api/client'

const BASE = '/api/v1/margin-labor'

/** One flag per module (Profile, Features). The shared data shows when any is on. */
export const ML_FLAGS = ['ml_menu_costs', 'ml_usage', 'ml_ordering', 'ml_labor', 'ml_channels']

export type ImportKind = 'orders' | 'punches' | 'payouts'
export type ImportResult = {
  kind: ImportKind
  rows: number
  created: number
  updated: number
  unchanged: number
  orders: number
  business_dates: string[]
  problems: { line: number; problem: string }[]
  problem_count: number
}
export type SourceStatus = { rows: number; first: string | null; last: string | null; last_import_at: string | null; stale: boolean; limit_hours: number | null }
export type DataStatus = {
  orders: SourceStatus & { business_dates: number; rolled_up_dates: number }
  punches: SourceStatus
  payouts: SourceStatus
  modules: Record<string, boolean>
}

export const importData = (kind: ImportKind, text: string) => send<ImportResult>('POST', `${BASE}/data/import`, { kind, text })
export const fetchDataStatus = () => get<DataStatus>(`${BASE}/data/status`)

// --- shared ------------------------------------------------------------------------------------------

export type ModuleState = 'setup' | 'learning' | 'live'
export type RawWhy = {
  result: { value: string; kind: 'dollars' | 'percent' | 'number'; label: string }
  formula: string
  inputs: { label: string; value: string; source: { kind: string; id?: string | null; label?: string | null; date?: string | null } | null }[]
  data_basis: string
  computed_at: string
  inputs_changed_on: string | null
  fix: { label: string; route: string } | null
  series?: { label: string; unit: string; points: { date: string; value: string; marks: string[] }[] } | null
  waterfall?: { start: { label: string; amount: string }; steps: { label: string; amount: string }[]; end: { label: string; amount: string } } | null
}
export const fetchWhy = (table: string, rowId: string, figure?: string) =>
  get<RawWhy>(`${BASE}/why/${table}/${rowId}${figure ? `?figure=${figure}` : ''}`)

export type ModuleCard = {
  module: 'menu_costs' | 'usage' | 'ordering' | 'labor' | 'channels'
  state: ModuleState
  route: string
  missing?: string[]
  [key: string]: unknown
}
export const fetchModuleCards = () => get<ModuleCard[]>(`${BASE}/digest-cards`)

// --- Module A: Menu costs ----------------------------------------------------------------------------

export type DishFinding = {
  id: string
  weekly_dollars: string
  opened_at: string
  proposal_id: string | null
  proposal_status: 'pending' | 'approved' | 'rejected' | null
  suggested_price: string | null
  food_cost_pct_at_suggested: string | null
  cap_applied: boolean
}
export type DishRow = {
  recipe_id: string
  name: string
  dish_cost_id: string
  price: string | null
  plate_cost: string
  partial: boolean
  unpriced: string[]
  food_cost_pct: string | null
  margin: string | null
  change_28: string | null
  sold_28: string
  trend: (string | null)[]
  target: string
  finding: DishFinding | null
}
export type SetupFact = { key: string; done: boolean; count: number }
export type MenuCosts = {
  state: ModuleState
  setup: SetupFact[]
  target: string
  today: string
  computed_at: string | null
  stale: boolean
  last_sales_date: string | null
  dishes: DishRow[]
  header: { dishes_over: number; weekly_dollars: string; dishes: number } | null
  tiles: {
    dishes_over: { value: number; then: number; of: number }
    average_food_cost: { value: string | null; then: string | null }
    largest_increase: { item_id: string; name: string; pct: string; base_unit: string; now: string; then: string; since: string; invoice_id: string } | null
  } | null
  left_out: number
}
export type DishLine = {
  line_id: string
  name: string
  qty: string
  unit: string
  kind: 'ingredient' | 'sub_recipe'
  priced: boolean
  reason?: string
  line_cost?: string
  unit_price?: string
  base_unit?: string
  yield_pct?: string
  price_date?: string | null
  supplier: string | null
  change_28: string | null
}
export type DishDetail = {
  state: ModuleState
  today: string
  computed_at: string | null
  stale: boolean
  target: string
  recipe: {
    id: string
    name: string
    kind: string
    portions: string
    yield_qty: string
    yield_unit: string
    confirmed: boolean
    lines: { id: string; name: string; qty: string; unit: string; yield_pct: string; item_id: string | null; sub_recipe_id: string | null }[]
  }
  cost: {
    id: string
    plate_cost: string
    batch_cost: string
    menu_price: string | null
    food_cost_pct: string | null
    margin: string | null
    lines_priced: number
    lines_total: number
    unpriced: string[]
    price_source: string | null
  } | null
  sold_28: string
  lines: DishLine[]
  history: { week: string; plate_cost: string | null; menu_price: string | null; changed: string[] }[]
  left_out: { item: string; unit_price: string; median: string | null; base_unit: string; day: string; invoice_id: string }[]
  finding: { id: string; weekly_dollars: string; opened_at: string; title: string } | null
  proposal: {
    id: string
    status: 'pending' | 'approved' | 'rejected'
    suggested_price: string | null
    price_at_target: string | null
    price_cap: string | null
    cap_applied: boolean | null
    food_cost_pct_at_suggested: string | null
    menu_price: string | null
    weekly_dollars: string | null
  } | null
}
export type RecipeEdit = { portions?: string; lines: { ingredient_text?: string; item_id?: string | null; sub_recipe_id?: string | null; qty: string; unit: string; yield_pct: string }[] }

export const fetchMenuCosts = () => get<MenuCosts>(`${BASE}/menu-costs`)
export const fetchDish = (recipeId: string) => get<DishDetail>(`${BASE}/menu-costs/${recipeId}`)
export const requestReprice = (recipeId: string) => send<{ proposal_id: string }>('POST', `${BASE}/menu-costs/${recipeId}/reprice`)
export const saveRecipe = (recipeId: string, body: RecipeEdit) => send<{ recipe_id: string; written: number }>('PUT', `${BASE}/recipes/${recipeId}`, body)

// --- Module B: Usage ---------------------------------------------------------------------------------

export type UsageChecks = {
  top_dish: { recipe_id: string; name: string; sold: string; per_portion: string; expected: string } | null
  last_delivery: { invoice_id: string; day: string; qty: string; supplier: string | null } | null
}
export type UsageItem = {
  item_id: string
  row_id: string
  name: string
  base_unit: string
  expected: string
  purchased: string
  unexplained: string
  pct: string | null
  weekly_dollars: string
  material: boolean
  trend: (string | null)[]
  finding: { id: string; type: 'usage_variance' | 'usage_data_gap'; checks: UsageChecks | null } | null
}
export type MissingRecipe = { pos_item_id: string; name: string; net: string; share: string | null; has_recipe: boolean }
export type Usage = {
  state: ModuleState
  coverage: string
  coverage_then: string
  coverage_learning: string
  coverage_live: string
  window_start: string
  window_end: string
  today: string
  computed_at: string | null
  stale: boolean
  last_sales_date: string | null
  has_sales: boolean
  missing_recipes: MissingRecipe[]
  recipes_needed: number
  items: UsageItem[]
  header: { weekly_dollars: string; items: number } | null
  tiles: { unexplained: { value: string; then: string }; coverage: { value: string; then: string; needed: string }; flagged: { value: number; then: number } } | null
  more_under: { count: number; threshold: string } | null
}
export type UsageWindow = { window_end: string; expected: string; purchased: string; noise: string; unexplained: string; weekly_dollars: string }
export type UsageDetail = {
  state: ModuleState
  coverage: string
  today: string
  computed_at: string | null
  stale: boolean
  item: { id: string; name: string; base_unit: string; track_usage: boolean }
  current: (UsageWindow & { row_id: string; window_start: string; pct: string | null; avg_unit_price: string; deliveries: number; prices_left_out: number }) | null
  windows: UsageWindow[]
  dishes: { recipe_id: string; name: string; sold: string; per_portion: string; expected: string }[]
  finding: { id: string; type: 'usage_variance' | 'usage_data_gap'; title: string; weekly_dollars: string; checks: UsageChecks | null } | null
  checks: UsageChecks | null
}
export const fetchUsage = () => get<Usage>(`${BASE}/usage`)
export const fetchUsageItem = (itemId: string) => get<UsageDetail>(`${BASE}/usage/${itemId}`)
export const setUsageTracking = (itemId: string, track: boolean) => send<{ track_usage: boolean }>('PUT', `${BASE}/usage/${itemId}/tracking`, { track_usage: track })
export const dismissFinding = (findingId: string) => send<{ id: string; status: string }>('POST', `${BASE}/findings/${findingId}/dismiss`)

// --- Module E: Channels ------------------------------------------------------------------------------

export type ChannelItem = {
  row_id: string
  pos_item_id: string
  name: string
  platform: string
  price: string
  fees: string
  commission: string
  processing: string
  fixed: string
  plate_cost: string | null
  kept: string | null
  kept_pct: string | null
  units: string
  match_rate: string | null
  recipe_id: string | null
  finding: {
    id: string
    weekly_dollars: string
    target: string | null
    proposal_id: string | null
    proposal_status: 'pending' | 'approved' | 'rejected' | null
    suggested_price: string | null
    kept_at_suggested: string | null
  } | null
}
export type ChannelTermsRow = { platform: string; commission_pct: string; processing_pct: string; fixed_fee_per_order: string; packaging_cost_per_order: string; effective_from?: string }
export type Channels = {
  state: ModuleState
  setup: { key: string; done: boolean; count: number; of?: number; missing?: string[] }[]
  window_start: string
  window_end: string
  today: string
  computed_at: string | null
  stale: boolean
  last_order_date: string | null
  match_rate: string | null
  contract_rates: boolean
  platforms: string[]
  terms: ChannelTermsRow[]
  header: { items: number; weekly_dollars: string; platform: string | null; delivery_items: number; losing: boolean } | null
  tiles: { channel: string; platform: string | null; label: string; units: string; kept_pct: string | null; kept: string | null; price: string | null }[]
  chart: { series: string[]; items: { pos_item_id: string; name: string; values: Record<string, string> }[] } | null
  items: ChannelItem[]
}
export const fetchChannels = () => get<Channels>(`${BASE}/channels`)
export const saveChannelTerms = (body: ChannelTermsRow) => send<{ terms: ChannelTermsRow[] }>('PUT', `${BASE}/channels/terms`, body)
export const requestChannelReprice = (rowId: string) => send<{ proposal_id: string }>('POST', `${BASE}/channels/${rowId}/reprice`)

// --- Module C: Ordering and prep ---------------------------------------------------------------------

export type OrderLine = {
  line_ref: string
  item_id: string
  name: string
  suggested: number
  qty: number
  pack_unit: string
  pack_base_qty: string
  base_unit: string
  expected_use: string
  est_on_hand: string
  est_on_hand_at_delivery: string
  need: string
  pack_price: string | null
  line_total: string | null
  correction: 'out' | 'plenty' | 'edit' | null
}
export type OrderDraft = {
  proposal_id: string
  supplier_id: string
  supplier: string
  delivery_date: string
  cutoff_at: string
  period_start: string
  period_end: string
  check_quantities: boolean
  minimum_order_amount: string | null
  under_minimum: boolean
  total: string | null
  lines: OrderLine[]
}
export type DeliveryDay = { id: string; supplier_id: string; supplier: string; order_cutoff_weekday: number; order_cutoff_time: string; delivery_weekday: number; minimum_order_amount: string | null }
export type NextCutoff = { supplier_id: string; supplier: string; at: string; delivery_date: string; hours_away: number }
export type Ordering = {
  state: ModuleState
  setup: { key: string; done: boolean; count: number; of?: number }[]
  today: string
  computed_at: string | null
  stale: boolean
  last_sales_date: string | null
  weeks_of_data: number
  schedules: DeliveryDay[]
  suppliers: { id: string; name: string }[]
  header: { orders: number; next_cutoff: NextCutoff | null } | null
  tiles: { waiting: { value: number; lines: number }; next_cutoff: NextCutoff | null; average_miss: { value: number | null; then: number | null; stage: string | null } } | null
  chart: { points: { date: string; low: number; expected: number; high: number; actual: number | null; drivers: string[] }[]; today_index: number; coverage: number | null } | null
  accuracy: { week: string; wape: number | null }[]
  drafts: OrderDraft[]
  can_draft?: { supplier_id: string; supplier: string; delivery_date: string; cutoff_at: string }[]
}
export type PrepLine = { item_id: string; item_name: string; unit: string; station: string; par: string; expected: string; batch_size: string; batches_to_par: number; shelf_life_days: number | null; used_by: string[] }
export type PrepSheet = { state: ModuleState; date: string; today: string; lines: PrepLine[]; proposal: { id: string; status: string } | null }

export const fetchOrdering = () => get<Ordering>(`${BASE}/ordering/drafts`)
export const draftOrder = (supplierId: string) => send<{ proposal_id: string; made: boolean }>('POST', `${BASE}/ordering/drafts`, { supplier_id: supplierId })
export const correctLine = (lineRef: string, kind: 'out' | 'plenty') => send<{ total: string }>('POST', `${BASE}/ordering/lines/${lineRef}/correction`, { kind })
export const setLineQty = (lineRef: string, qty: number) => send<{ total: string }>('PUT', `${BASE}/ordering/lines/${lineRef}`, { qty })
export const saveDeliveryDay = (body: { supplier_id: string; order_cutoff_weekday: number; order_cutoff_time: string; delivery_weekday: number; minimum_order_amount?: string | null }) =>
  send<{ schedules: DeliveryDay[] }>('PUT', `${BASE}/ordering/schedules`, body)
export const removeDeliveryDay = (id: string) => send<{ schedules: DeliveryDay[] }>('DELETE', `${BASE}/ordering/schedules/${id}`)
export const fetchPrep = (day: string) => get<PrepSheet>(`${BASE}/prep/${day}`)
export const proposePrep = (day: string) => send<{ proposal_id: string; made: boolean }>('POST', `${BASE}/prep/${day}/propose`)

// --- Module D: Labor ---------------------------------------------------------------------------------

export type LaborDay = { date: string; low: number | null; expected: number | null; high: number | null; last_week: number | null; drivers: string[]; net_expected: number | null }
export type LaborStep = { start: string; label: string; need: number; need_if_busy: number; scheduled: number; units: number }
export type LaborRoleWeek = { role_key: string; name: string; hours_p50: number; hours_p90: number; peak: number; peak_at: string | null; by_day: { date: string; hours_p50: number; hours_p90: number }[] }
export type LaborWeek = {
  state: ModuleState
  setup: { key: string; done: boolean; count: number; of?: number }[]
  weeks_of_data: number
  primary: 'covers' | 'orders'
  week_of: string
  today: string
  computed_at: string | null
  stale: boolean
  last_order_date: string | null
  target_labor_pct: string | null
  days: LaborDay[]
  roles: LaborRoleWeek[]
  totals: { hours_p50: number; hours_p90: number; expected: number; low: number; high: number; net_expected: number; days_forecast: number } | null
  intervals: Record<string, Record<string, LaborStep[]>>
  horizon_end: string
  schedule: Schedule | null
  wage_access?: boolean
}
export type StandardRow = {
  role_key: string
  name: string
  driver_metric: string
  units_per_labor_hour: string | null
  min_on_floor: number
  open_lead_minutes: number
  close_lag_minutes: number
  source: 'learned' | 'manual' | null
  learned: { units_per_labor_hour: string; sample_size: number; effective_from: string } | null
  observed_intervals: number
  punch_weeks: number
  needed_intervals: number
  histogram: { from: number; to: number; count: number }[]
}
export type Standards = {
  state: ModuleState
  weeks_of_data: number
  today: string
  computed_at: string | null
  primary: string
  roles: StandardRow[]
  accuracy: { dayparts: { daypart: string; wape: number }[]; coverage: number | null; target: number; calibration_factor: number | null; stage: string | null }
}
export type StandardBody = { role_key: string; units_per_labor_hour: string; min_on_floor: number; open_lead_minutes: number; close_lag_minutes: number }
export type StandardPreview = { role_key: string; days: { date: string; before: number; after: number }[]; week_before: number; week_after: number; top: { date: string; before: number; after: number } }
export const fetchLaborWeek = (weekOf: string) => get<LaborWeek>(`${BASE}/labor/weeks/${weekOf}`)
export const fetchStandards = () => get<Standards>(`${BASE}/labor/standards`)
export const previewStandard = (body: StandardBody) => send<StandardPreview>('POST', `${BASE}/labor/standards/preview`, body)
export const saveStandard = (body: StandardBody) => send<Standards>('PUT', `${BASE}/labor/standards`, body)
export const resetStandard = (roleKey: string) => send<Standards>('DELETE', `${BASE}/labor/standards/${roleKey}`)
export type ScheduleShift = { id: string; staff_id: string | null; role_key: string; date: string; start: number; end: number; kind: 'regular' | 'flex'; locked: boolean; note: string; paid_minutes: number }
export type Schedule = {
  id: string
  week_of: string
  status: 'draft' | 'published'
  version: number
  generated_at: string | null
  published_at: string | null
  proposal_id: string | null
  hours_total: string
  open_shifts: number
  understaffed_intervals: number
  overtime: string[]
  hours_by_person: Record<string, number>
  violations: { shift_id: string; person: string; rule: string }[]
  unset_rules: string[]
  people: { id: string; name: string; roles: string[] }[]
  shifts: ScheduleShift[]
  wage_access: boolean
  /** Only with wage access. */
  cost_total?: string | null
  net_expected?: string
  uncosted_shifts?: number
  labor_pct?: string | null
  cost_by_day?: { date: string; cost: string; net_low: number | null; net_expected: number | null; net_high: number | null }[]
}
export type PublishWarning = { kind: 'notice'; days: number; rule: number } | { kind: 'open_shifts'; count: number } | { kind: 'rule'; shift_id: string; person: string; rule: string }
export type LaborRulesBody = Record<string, number | boolean | string | null>
export type ShiftBody = { schedule_id: string; date?: string; start?: number; end?: number; role_key?: string; staff_id?: string | null; unassign?: boolean; locked?: boolean; note?: string }
export const generateSchedule = (weekOf: string) => send<{ job_id: string }>('POST', `${BASE}/labor/weeks/${weekOf}/generate`)
export const fetchJob = (jobId: string) => get<{ status: 'queued' | 'running' | 'done' | 'failed'; error: string | null }>(`${BASE}/jobs/${jobId}`)
export const addShift = (body: ShiftBody) => send<Schedule>('POST', `${BASE}/labor/shifts`, body)
export const editShift = (shiftId: string, body: ShiftBody) => send<Schedule>('PATCH', `${BASE}/labor/shifts/${shiftId}`, body)
export const removeShift = (shiftId: string, scheduleId: string) => send<Schedule>('DELETE', `${BASE}/labor/shifts/${shiftId}?schedule_id=${scheduleId}`)
export const publishSchedule = (scheduleId: string, confirm: boolean) => send<{ published: boolean; warnings: PublishWarning[]; schedule: Schedule }>('POST', `${BASE}/labor/schedules/${scheduleId}/publish`, { confirm })
export const scheduleCsvUrl = (scheduleId: string) => `${BASE}/labor/schedules/${scheduleId}/export.csv`
export const fetchLaborRules = () => get<LaborRulesBody>(`${BASE}/labor/rules`)
export const saveLaborRules = (body: LaborRulesBody) => send<LaborRulesBody>('PUT', `${BASE}/labor/rules`, body)
