import { get } from './client'

const BASE = '/api/v1/insights'

export type SavingsWeek = { week_start: string; found: number; acted: number; found_to_date: number; acted_to_date: number }
export type Savings = { weeks: SavingsWeek[]; found_to_date: number; acted_to_date: number; open_this_week: number }

export type PrimeWeek = {
  week_of: string
  sales: number
  food_pct: number
  labor_pct: number
  paper_pct: number
  prime_pct: number
  food: number
  labor: number
  /** False when a day of sales or labor was missing from the week. */
  complete: boolean
}

export type SpendRow = { key: string; label: string; now: number; before: number }
export type Spend = {
  days: number
  from: string
  to: string
  total_now: number
  total_before: number
  by_supplier: SpendRow[]
  by_category: SpendRow[]
}

export type OutlookItem = {
  id: string
  name: string
  unit: string
  on_hand: number
  /** Average used a day, from sales through recipes; 0 when no recipe uses it. */
  daily_use: number
  reorder_threshold: number
  reorder_qty: number
  unit_cost: number
  pack_size: number | null
  pack_unit: string | null
  par: number | null
  lead_time_days: number
  supplier_name: string | null
  days_left: number | null
  incoming: { expected_on: string; quantity: number; late: boolean }[]
}
export type StockOutlook = {
  today: string
  safety_days: number
  /** How busy each weekday is against the average day, Monday first. */
  weekday_factors: number[]
  items: OutlookItem[]
}

export type SwitchOption = {
  item_id: string
  item_name: string
  category: string
  current_supplier_id: string
  weekly_packs: number
  /** Cheapest first. */
  suppliers: { supplier_id: string; name: string; per_pack: number; last_date: string }[]
}

export type SearchHit = { kind: 'stock' | 'price' | 'supplier' | 'invoice' | 'dish'; title: string; subtitle: string; href: string }

export const fetchSavings = () => get<Savings>(`${BASE}/savings`)
export const fetchPrimeCost = () => get<PrimeWeek[]>(`${BASE}/prime-cost`)
export const fetchSpend = (days: number) => get<Spend>(`${BASE}/spend?days=${days}`)
export const fetchStockOutlook = () => get<StockOutlook>(`${BASE}/stock-outlook`)
export const fetchSwitchOptions = () => get<SwitchOption[]>(`${BASE}/switch-options`)
export const searchEverything = (q: string) => get<SearchHit[]>(`${BASE}/search?q=${encodeURIComponent(q)}`)
