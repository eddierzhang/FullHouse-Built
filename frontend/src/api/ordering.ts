import { get, send } from './client'

const BASE = '/api/v1/ordering'

export type SupplierCalendar = {
  /** Weekdays, Monday = 0. */
  order_days: number[]
  cutoff_time: string
  delivery_days: number[]
  lead_time_days: number | null
  minimum_dollars: number | null
  channel: 'email' | 'portal' | 'phone'
  order_email: string | null
}
export type AutopilotLimits = {
  enabled: boolean
  per_order_cap: number
  weekly_cap: number
  max_price_rise_pct: number
  max_qty_multiple: number
  min_history: number
  cancel_window_minutes: number
}
export type AutopilotPolicy = AutopilotLimits & { consecutive_failures: number; paused_reason: string | null }
export type Rule = { rule: string; ok: boolean; detail: string }
export type Decision = {
  id: string
  supplier_id: string | null
  supplier_name: string
  outcome: 'passed' | 'held' | 'sent' | 'cancelled' | 'failed'
  rules: Rule[]
  total: number
  send_after: string | null
  decided_at: string
  sent_at: string | null
  cancelled_by: string | null
  order_id: string | null
  expected_on: string | null
  lines: number | null
}
export type OrderingSupplier = {
  id: string
  name: string
  lead_time_days: number | null
  calendar: SupplierCalendar
  policy: AutopilotPolicy
  order_email: string | null
  sent_this_week: number
}
export type OrderingOverview = { paused: boolean; can_send: boolean; is_owner: boolean; now: string; suppliers: OrderingSupplier[]; waiting: Decision[]; recent: Decision[] }
export type Preview = {
  supplier_name: string
  weeks: number
  orders: { date: string; reference: string | null; total: number; would_send: boolean; held_for: string[] }[]
  would_send: number
  would_hold: number
  sent_total: number
}
export type ParRow = {
  inventory_item_id: string
  name: string
  unit: string
  on_hand: number
  daily_use: number
  lead_time_days: number
  cover_days: number
  safety_days: number
  suggested: number | null
  par: number | null
  pinned: boolean
  supplier_name: string | null
  unit_cost: number
}

export const fetchOrdering = () => get<OrderingOverview>(`${BASE}/overview`)
export const saveCalendar = (supplierId: string, body: SupplierCalendar) => send<SupplierCalendar>('PUT', `${BASE}/suppliers/${supplierId}/calendar`, body)
export const savePolicy = (supplierId: string, body: AutopilotLimits) => send<AutopilotPolicy>('PUT', `${BASE}/suppliers/${supplierId}/policy`, body)
export const previewPolicy = (supplierId: string, body: AutopilotLimits) => send<Preview>('POST', `${BASE}/suppliers/${supplierId}/preview`, body)
export const pauseAutopilot = (paused: boolean) => send<{ paused: boolean }>('POST', `${BASE}/pause`, { paused })
export const cancelSend = (decisionId: string) => send<{ id: string; outcome: string }>('POST', `${BASE}/decisions/${decisionId}/cancel`)
export const fetchPars = () => get<ParRow[]>(`${BASE}/pars`)
export const setPar = (itemId: string, par: number | null) => send<ParRow>('PUT', `${BASE}/pars/${itemId}`, { par })
export const runAutopilot = () => send<{ pars: number; drafted: number; passed: number; held: number; sent: number }>('POST', `${BASE}/run`)
