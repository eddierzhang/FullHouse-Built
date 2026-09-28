import { useState } from 'react'
import { ApiError, type Change, listChanges, revertChange } from '../api/client'
import type { AppContext } from '../app/context'
import { Empty, Freshness, Loaded } from '../components/PageState'
import { dateTime, relativeTime } from '../lib/format'
import { announceDataChanged, useLoad } from '../lib/useLoad'

const PAGE = 50

const KINDS: { value: string; label: string; noun: string }[] = [
  { value: '', label: 'Everything', noun: '' },
  { value: 'inventory_items', label: 'Stock & items', noun: 'item' },
  { value: 'menu_items', label: 'Menu', noun: 'dish' },
  { value: 'suppliers', label: 'Suppliers', noun: 'supplier' },
  { value: 'staff', label: 'Staff', noun: 'team member' },
  { value: 'shifts', label: 'Schedule', noun: 'shift' },
  { value: 'agent_actions', label: 'Recommendations', noun: 'recommendation' },
]

const WHO: { value: string; label: string }[] = [
  { value: '', label: 'Anyone' },
  { value: 'human', label: 'People' },
  { value: 'agent', label: 'Assistant Manager' },
]

/** What can be undone from here. The rest -- runs, the restaurant's details -- is changed elsewhere. */
const UNDOABLE = new Set(['inventory_items', 'menu_items', 'suppliers', 'staff', 'shifts', 'recipe_items'])

/** Bookkeeping columns nobody needs to read. */
const HIDDEN_FIELDS = new Set(['id', 'restaurant_id', 'updated_at', 'created_at', 'decided_at', 'payload', 'applied_result'])

const FIELD_NAMES: Record<string, string> = {
  quantity_on_hand: 'on hand',
  reorder_threshold: 'reorder at',
  reorder_qty: 'reorder quantity',
  unit_cost: 'unit cost',
  lead_time_days: 'lead time (days)',
  contact_info: 'contact',
  is_available: 'on the menu',
  start_time: 'start',
  end_time: 'end',
  published_at: 'published',
  status: 'status',
}

function fieldName(field: string) {
  return FIELD_NAMES[field] ?? field.replace(/_id$/, '').replace(/_/g, ' ')
}

function show(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'blank'
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(2).replace(/\.?0+$/, '')
  const text = String(value)
  return /^\d{4}-\d{2}-\d{2}T/.test(text) ? dateTime(text) : text
}

function who(change: Change): string {
  if (change.actor_type === 'agent') return 'Assistant Manager'
  if (change.actor_type === 'system') return 'FullHouse'
  return change.actor_id ?? 'Someone'
}

function what(change: Change): string {
  const label = change.entity_label ?? change.entity_type.replace(/_/g, ' ')
  if (change.operation === 'insert') return `added ${label}`
  if (change.operation === 'delete') return `removed ${label}`
  return `changed ${label}`
}

function details(change: Change): string[] {
  if (change.operation !== 'update') return []
  return change.changed_fields
    .filter((field) => !HIDDEN_FIELDS.has(field))
    .map((field) => `${fieldName(field)}: ${show(change.before?.[field])} → ${show(change.after?.[field])}`)
}

export function HistoryPage({ app }: { app: AppContext }) {
  const [kind, setKind] = useState('')
  const [actor, setActor] = useState('')
  const [shown, setShown] = useState(PAGE)
  const load = useLoad(() => listChanges({ limit: shown, entity_type: kind, actor_type: actor }), [kind, actor, shown])

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">History & undo</h1>
          <p className="page-subtitle">
            Every change to stock, the menu, suppliers, the team and the schedule — made by people or by the Assistant
            Manager.{' '}
            {app.canManage ? 'Made a mistake? Undo it here.' : 'Owners and admins can undo a change.'}
          </p>
        </div>
        <Freshness loads={[load]} />
      </div>

      <div className="filters">
        <label className="field">
          <span className="field-label">Showing</span>
          <select className="select" value={kind} onChange={(e) => (setKind(e.target.value), setShown(PAGE))}>
            {KINDS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Changed by</span>
          <select className="select" value={actor} onChange={(e) => (setActor(e.target.value), setShown(PAGE))}>
            {WHO.map((w) => (
              <option key={w.value} value={w.value}>
                {w.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <section className="card">
        <Loaded load={load} what="the history" placeholderHeight={240}>
          {(page) =>
            page.items.length === 0 ? (
              <Empty title="Nothing here yet">Changes show up here as soon as anyone makes one.</Empty>
            ) : (
              <>
                <ul className="list" aria-label="Changes">
                  {page.items.map((change) => (
                    <ChangeRow key={change.id} change={change} app={app} onUndone={load.reload} />
                  ))}
                </ul>
                {page.total > page.items.length && (
                  <div className="card-footer" style={{ textAlign: 'center' }}>
                    <button className="btn btn-secondary" disabled={load.refreshing} onClick={() => setShown((n) => n + PAGE)}>
                      {load.refreshing ? 'Loading…' : `Show more (${page.total - page.items.length} older)`}
                    </button>
                  </div>
                )}
              </>
            )
          }
        </Loaded>
      </section>
    </main>
  )
}

function ChangeRow({ change, app, onUndone }: { change: Change; app: AppContext; onUndone: () => Promise<void> }) {
  const [busy, setBusy] = useState(false)
  const [conflict, setConflict] = useState<string | null>(null)
  const lines = details(change)
  const undoable = app.canManage && UNDOABLE.has(change.entity_type) && !change.note?.startsWith('Revert of')

  const undo = async (force: boolean) => {
    if (!force && !window.confirm(`Undo this change to ${change.entity_label ?? 'this record'}?`)) return
    setBusy(true)
    try {
      const result = await revertChange(change.id, force)
      setConflict(null)
      app.toast('Undone', result.description)
      announceDataChanged()
      app.refresh()
      await onUndone()
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      if (err instanceof ApiError && err.status === 409 && !force) setConflict(message)
      else app.toast('Couldn’t undo that', message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="list-item change-row">
      <div className="list-item-main">
        <div className="row wrap">
          <span className="strong">{who(change)}</span>
          <span>{what(change)}</span>
          {change.agent_action_id && <span className="badge badge-brand">approved recommendation</span>}
          {change.note?.startsWith('Revert of') && <span className="badge">undo</span>}
        </div>
        {lines.length > 0 && (
          <ul className="change-details">
            {lines.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        )}
        <div className="faint small" title={dateTime(change.ts)}>
          {relativeTime(change.ts)}
        </div>
        {conflict && (
          <div className="callout callout-warning change-conflict" role="alert">
            <div className="callout-grow">
              <div className="callout-body">{conflict}</div>
            </div>
            <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => undo(true)}>
              Undo anyway
            </button>
          </div>
        )}
      </div>
      {undoable && !conflict && (
        <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => undo(false)}>
          {busy ? 'Undoing…' : 'Undo'}
        </button>
      )}
    </li>
  )
}
