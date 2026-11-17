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
