import type { AgentAction } from '../api/client'
import type { IconName } from './Icon'

export const ACTION_META: Record<string, { label: string; badge: string; icon: IconName }> = {
  inventory_reorder: { label: 'Reorder', badge: 'badge-warning', icon: 'inventory' },
  purchase_order: { label: 'Purchase order', badge: 'badge-info', icon: 'suppliers' },
  shift_change: { label: 'Schedule', badge: 'badge-info', icon: 'staff' },
  menu_change: { label: 'Menu', badge: 'badge-brand', icon: 'menu' },
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
    default:
      return action.action_type
  }
}

export const actionDetail = (action: AgentAction) =>
  text(action.payload.note) || text(action.payload.details) || 'No notes from the agent.'

export const isPromotion = (action: AgentAction) =>
  action.action_type === 'menu_change' && action.payload.change_type === 'promotion'
