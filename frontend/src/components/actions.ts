import type { AgentAction } from '../api/client'
import type { IconName } from './Icon'

export const ACTION_META: Record<string, { label: string; badge: string; icon: IconName }> = {
  inventory_reorder: { label: 'Reorder', badge: 'badge-warning', icon: 'inventory' },
  purchase_order: { label: 'Purchase order', badge: 'badge-info', icon: 'suppliers' },
  shift_change: { label: 'Schedule', badge: 'badge-info', icon: 'staff' },
  menu_change: { label: 'Menu', badge: 'badge-brand', icon: 'menu' },
  recipe: { label: 'Recipe', badge: 'badge-brand', icon: 'menu' },
  agreed_price: { label: 'Agreed price', badge: 'badge-info', icon: 'profit' },
  menu_reprice: { label: 'Reprice', badge: 'badge-warning', icon: 'menu' },
  calendar_item: { label: 'Calendar', badge: 'badge-info', icon: 'clock' },
  prep_sheet: { label: 'Prep sheet', badge: 'badge-info', icon: 'inventory' },
}

const FALLBACK = { label: 'Proposal', badge: '', icon: 'approvals' as IconName }

export const actionMeta = (action: AgentAction) => ACTION_META[action.action_type] ?? FALLBACK

const text = (value: unknown) => (value === undefined || value === null ? '' : String(value))

export function actionTitle(action: AgentAction): string {
  const p = action.payload
  switch (action.action_type) {
    case 'inventory_reorder':
      return `Reorder ${text(p.quantity)} × ${text(p.item_name) || 'an item'}`
    case 'purchase_order':
      return `Order ${text(p.quantity)} × ${text(p.item_name)} from ${text(p.supplier_name) || 'a supplier'}`
    case 'shift_change':
      return `Shift for ${text(p.staff_name) || 'a staff member'} on ${text(p.date)}`
    case 'menu_change': {
      const kind = text(p.change_type).replace('_', ' ') || 'change'
      return `${kind[0].toUpperCase()}${kind.slice(1)}: ${text(p.item_name)}`
    }
    case 'recipe':
      return `Recipe for ${text(p.name) || 'a dish'}`
    case 'agreed_price':
      return `Agreed price: ${text(p.item_name)} from ${text(p.supplier_name)} at $${Number(p.per_pack ?? p.unit_price ?? 0).toFixed(2)}`
    case 'menu_reprice':
      return `Reprice ${text(p.item_name)}${p.suggested_delta ? ` by about $${Number(p.suggested_delta).toFixed(2)}` : ''}`
    case 'calendar_item':
      return `${text(p.title) || 'A date'} due ${text(p.due_on)}`
    case 'prep_sheet':
      return `Prep sheet for ${text(p.date)}`
    default:
      return action.action_type
  }
}

export const actionDetail = (action: AgentAction) =>
  text(action.payload.note) || text(action.payload.details) || 'No notes from the agent.'

export const isPromotion = (action: AgentAction) =>
  action.action_type === 'menu_change' && action.payload.change_type === 'promotion'
