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
