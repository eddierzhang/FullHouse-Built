import { get, send } from './client'

const BASE = '/api/v1/receiving'

export type ExpectedLine = { key: string; description: string; expected: number; unit: string; ordered: number; received_so_far: number; inventory_item_id: string | null }
export type ExpectedOrder = {
  id: string
  supplier_name: string
  status: 'ordered' | 'confirmed' | 'shipped' | 'partial'
  summary: string
  expected_on: string | null
  ordered_at: string
  confirmed_at: string | null
  shipped_at: string | null
  autopilot: boolean
  late: boolean
  days_late: number
  estimated_cost: number | null
  note: string | null
  lines: ExpectedLine[]
  chase: { subject: string; body: string } | null
  rep_email: string | null
}
export type LineStatus = 'ok' | 'short' | 'over' | 'substituted' | 'damaged' | 'missing' | 'partial'
export type ReceiptResult = { id: string; po_id: string; lines: number; ok: number; owed: number; still_coming: number; order_status: string; claim_draft_id: string | null }
export type PastReceipt = {
  id: string
  supplier_name: string
  received_at: string
  received_by: string | null
  note: string | null
  has_photo: boolean
  lines: { description: string; expected: number | null; received: number; unit: string; status: LineStatus; note: string | null }[]
}

export const fetchExpected = () => get<ExpectedOrder[]>(`${BASE}/expected`)
export const fetchReceipts = () => get<PastReceipt[]>(`${BASE}/receipts`)
export const checkIn = (body: { po_id: string; client_key: string; lines: { key: string; received: number; status?: LineStatus; note?: string }[]; note?: string; photo_document_id?: string }) =>
  send<ReceiptResult>('POST', `${BASE}/receipts`, body)
export const supplierReplied = (orderId: string, text: string) => send<{ kind: 'confirmed' | 'shipped' | 'change' | 'other'; status: string; note: string | null }>('POST', `${BASE}/orders/${orderId}/reply`, { text })
