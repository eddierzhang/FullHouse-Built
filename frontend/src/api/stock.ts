import { get, send } from './client'

const BASE = '/api/v1/stock'

export type StorageArea = { id: string; name: string; position: number; items: number }

export type CountLine = {
  inventory_item_id: string
  item_name: string
  unit: string
  expected_qty: number
  counted_qty: number | null
  unit_cost: number
  pack_size: number | null
  pack_unit: string | null
}
export type CountSummary = { lines: number; counted: number; difference_dollars: number; short: number; over: number }
export type StockCount = {
  id: string
  storage_area_id: string | null
  area_name: string
  status: 'open' | 'posted'
  started_by: string | null
  started_at: string
  posted_at: string | null
  lines: CountLine[]
  summary: CountSummary
}

export type WasteEntry = { id: number; item_name: string; unit: string; quantity: number; dollars: number; reason: string; reason_label: string; note: string | null; by: string | null; at: string }
export type WasteLog = { reasons: Record<string, string>; entries: WasteEntry[] }
export type WasteResult = { inventory_item_id: string; name: string; unit: string; taken: number; on_hand: number; dollars: number }

export type VarianceRow = {
  inventory_item_id: string
  name: string
  unit: string
  unit_cost: number
  on_hand: number
  received: number
  used: number
  wasted: number
  wasted_dollars: number
  /** What counts found beyond sales, waste and deliveries: negative is missing. Null when nobody counted it. */
  unexplained: number | null
  unexplained_dollars: number | null
  pct_of_use: number | null
  last_counted_at: string | null
}
export type Variance = {
  days: number
  from: string
  to: string
  items: VarianceRow[]
  missing_dollars: number
  extra_dollars: number
  wasted_dollars: number
  used_dollars: number
  counted: number
  not_counted: number
}

export type StockAlert = { kind: 'runs_out' | 'eighty_six'; title: string; detail: string; days_left: number }
export type StockStatus = {
  tracking_since: string | null
  last_depleted_at: string | null
  last_sales_day: string | null
  out_of_balance: { name: string; on_hand: number; ledger: number }[]
  alerts: StockAlert[]
  unmapped: { pos_item_name: string; qty: number; net: number; why: string }[]
}

export const listAreas = () => get<StorageArea[]>(`${BASE}/areas`)
export const addArea = (name: string) => send<StorageArea[]>('POST', `${BASE}/areas`, { name })
export const removeArea = (id: string) => send<StorageArea[]>('DELETE', `${BASE}/areas/${id}`)
export const placeItem = (itemId: string, body: { storage_area_id?: string | null; shelf_order?: number; yield_pct?: number | null }) =>
  send<{ id: string; storage_area_id: string | null; shelf_order: number; yield_pct: number | null }>('PUT', `${BASE}/items/${itemId}/place`, body)

export const listCounts = () => get<StockCount[]>(`${BASE}/counts`)
export const startCount = (storage_area_id: string | null) => send<StockCount>('POST', `${BASE}/counts`, { storage_area_id })
export const getCount = (id: string) => get<StockCount>(`${BASE}/counts/${id}`)
export const saveCountLines = (id: string, lines: { inventory_item_id: string; counted_qty: number | null }[]) =>
  send<StockCount>('PUT', `${BASE}/counts/${id}/lines`, { lines })
export const postCount = (id: string) => send<StockCount>('POST', `${BASE}/counts/${id}/post`)
export const discardCount = (id: string) => send<{ discarded: string }>('DELETE', `${BASE}/counts/${id}`)

export const recordWaste = (body: { inventory_item_id: string; quantity: number; reason: string; note?: string }, key: string) =>
  send<WasteResult>('POST', `${BASE}/waste`, body, key)
export const fetchWaste = () => get<WasteLog>(`${BASE}/waste`)
export const fetchVariance = (days: number) => get<Variance>(`${BASE}/variance?days=${days}`)
export const fetchStockStatus = () => get<StockStatus>(`${BASE}/status`)
export const runStockNow = () => send<{ moved: number; sales: number; told: number }>('POST', `${BASE}/run`)
