import { send } from './client'

const BASE = '/api/v1/voice'

export type VoiceLine = { inventory_item_id: string; name: string; quantity: number; unit: string; said: string; sure: boolean }
export type VoiceUnmatched = { said: string; quantity: number; unit: string | null; choices: { id: string; name: string }[] }
export type VoicePreview = {
  id: string
  intent: 'count' | 'waste' | 'receive' | 'ask' | 'navigate' | 'unknown'
  status: 'previewed' | 'confirmed' | 'discarded'
  transcript: string
  lines: VoiceLine[]
  unmatched: VoiceUnmatched[]
  area: string | null
  reason: string | null
  supplier: string | null
  all_arrived: boolean
  question: string | null
  route: string | null
  route_label: string | null
  /** The command said back in a sentence. */
  speak: string
  /** Why it can't be carried out here, when it can't. */
  blocked: string | null
  order_id?: string | null
  claim_draft_id?: string | null
  done?: string
}

export const parseSpeech = (transcript: string, source: 'browser' | 'typed') => send<VoicePreview>('POST', `${BASE}/parse`, { transcript, source })
export const confirmSpeech = (id: string, lines?: { inventory_item_id: string; quantity: number }[], reason?: string | null) =>
  send<VoicePreview & { done: string }>('POST', `${BASE}/commands/${id}/confirm`, { lines: lines ?? null, reason: reason ?? null })
export const discardSpeech = (id: string) => send<{ id: string; status: string }>('POST', `${BASE}/commands/${id}/discard`)
export const learnAlias = (phrase: string, inventory_item_id: string) => send<{ phrase: string; name: string }>('POST', `${BASE}/aliases`, { phrase, inventory_item_id })
