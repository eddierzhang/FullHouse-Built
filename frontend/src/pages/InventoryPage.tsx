import { type ChangeEvent, type FormEvent, Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import {
  ApiError,
  type InventoryItem,
  type StockAdjustment,
  type StockReason,
  type Supplier,
  type User,
  UNITS,
  canEditRestaurant,
  createInventoryItem,
  fetchSupplyChain,
  listInventoryItems,
  listStockChanges,
  listSuppliers,
  newRequestKey,
  recordStockChange,
  updateInventoryItem,
} from '../api/client'
import type { AppContext } from '../app/context'
import { Icon } from '../components/Icon'
import { Empty, Freshness, LoadError, Loaded } from '../components/PageState'
import { dateTime, money, qty, relativeTime } from '../lib/format'
import { useLoad } from '../lib/useLoad'

/** Fill scaled so the reorder point sits at the midpoint of the bar. */
const fill = (onHand: number, threshold: number) =>
  threshold <= 0 ? (onHand > 0 ? 100 : 0) : Math.min(100, (onHand / (threshold * 2)) * 100)

/** Same ceiling as the server: far past any kitchen, short of float trouble. */
const MAX_QUANTITY = 1_000_000

const REASONS: { id: StockReason; label: string; amountLabel: string }[] = [
  { id: 'delivery', label: 'Delivery received', amountLabel: 'Amount received' },
  { id: 'used', label: 'Used', amountLabel: 'Amount used' },
  { id: 'waste', label: 'Wasted', amountLabel: 'Amount wasted' },
  { id: 'count', label: 'Stock count', amountLabel: 'Counted on hand' },
]

/** A typed number, or why it isn't one we can take. */
function parseAmount(raw: string, { allowZero }: { allowZero: boolean }): number | string {
  if (!raw.trim()) return 'Enter an amount.'
  const n = Number(raw)
  if (!Number.isFinite(n)) return 'Enter a number, like 2 or 0.5.'
  if (n < 0) return 'Enter an amount of zero or more.'
  if (n === 0 && !allowZero) return 'Enter an amount greater than zero.'
  if (n > MAX_QUANTITY) return 'That’s more than we can track — check the amount.'
  return n
}

type Panel = { itemId: string; kind: 'change' | 'edit' } | null

export function InventoryPage({ app, user }: { app: AppContext; user: User }) {
  const canEditDetails = canEditRestaurant(user)
  const [filter, setFilter] = useState<'all' | 'low'>('all')
  const [historyItem, setHistoryItem] = useState<string | null>(null)
  const [panel, setPanel] = useState<Panel>(null)
  const [adding, setAdding] = useState(false)

  // Each refetches on its own when a coworker changes something, so their
  // changes show up without a reload.
  const itemsLoad = useLoad(listInventoryItems, [])
  const suppliersLoad = useLoad(listSuppliers, [])
  const chainLoad = useLoad(fetchSupplyChain, [])
  const historyLoad = useLoad(() => listStockChanges(historyItem ?? undefined), [historyItem])
  const items = itemsLoad.data
  const suppliers = suppliersLoad.data ?? []
  const chain = chainLoad.data

  /** After any save: the database's numbers, here and in the sidebar and dashboard. */
  const saved = useCallback(async () => {
    await Promise.all([itemsLoad.reload(), chainLoad.reload(), historyLoad.reload()])
    app.refresh()
  }, [itemsLoad.reload, chainLoad.reload, historyLoad.reload, app])

  // Blank rather than a wrong "No supplier" while the list hasn't loaded.
  const supplierName = (id: string | null) =>
    suppliersLoad.data ? (suppliers.find((s) => s.id === id)?.name ?? 'No supplier') : ''
  const cover = useMemo(() => new Map((chain?.cover ?? []).map((c) => [c.id, c])), [chain])
  const all = items ?? []

  // Most urgent first: how far above its reorder point each item sits, relative
  // to that point. Ties break alphabetically.
  const rows = useMemo(() => {
    const urgency = (i: InventoryItem) =>
      i.reorder_threshold > 0 ? i.quantity_on_hand / i.reorder_threshold : Number.POSITIVE_INFINITY
    return all
      .filter((i) => filter === 'all' || i.low_stock)
      .sort((a, b) => urgency(a) - urgency(b) || a.name.localeCompare(b.name))
  }, [all, filter])

  const lowCount = all.filter((i) => i.low_stock).length
  const value = all.reduce((sum, i) => sum + i.quantity_on_hand * i.unit_cost, 0)
  const who = (a: StockAdjustment) =>
    a.actor_type === 'human'
      ? (a.actor_name ?? a.actor_id ?? 'Someone')
      : a.actor_type === 'agent'
        ? (app.agents.find((agent) => agent.key === a.actor_id)?.name ?? 'The agent')
        : 'System'

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Inventory</h1>
          <p className="page-subtitle">Stock on hand, what’s running low, and every change to it.</p>
        </div>
        <Freshness loads={[itemsLoad, chainLoad, historyLoad, suppliersLoad]} />
        <button className="btn btn-primary" onClick={() => setAdding((v) => !v)}>
          <Icon name={adding ? 'close' : 'plus'} size={16} /> {adding ? 'Cancel' : 'Add item'}
        </button>
      </div>

      <div className="stack">
        {suppliersLoad.status === 'error' && (
          <LoadError what="suppliers" error={suppliersLoad.error ?? ''} onRetry={suppliersLoad.reload} />
        )}
        {chainLoad.status === 'error' && (
          <LoadError what="days of cover" error={chainLoad.error ?? ''} onRetry={chainLoad.reload} />
        )}
        {adding && (
          <AddItemForm
            suppliers={suppliers}
            onCancel={() => setAdding(false)}
            onSaved={async (item) => {
              setAdding(false)
              app.toast('Item added', `${item.name}: ${qty(item.quantity_on_hand)} ${item.unit} on hand`)
              await saved()
            }}
          />
        )}

        <section className="grid grid-stats">
          <div className="card stat">
            <div className="stat-label">Items tracked</div>
            <div className="stat-value">{items ? all.length : '—'}</div>
          </div>
          <div className="card stat">
            <div className="stat-label">Stock value</div>
            <div className="stat-value">{items ? money(value) : '—'}</div>
          </div>
          <div className={`card stat${lowCount ? ' attention' : ''}`}>
            <div className="stat-label">Below reorder point</div>
            <div className="stat-value">{items ? lowCount : '—'}</div>
          </div>
          <div className={`card stat${chain?.at_risk_count ? ' attention' : ''}`}>
            <div className="stat-label">Runs out before resupply</div>
            <div className="stat-value">{chain ? chain.at_risk_count : '—'}</div>
            <div className="stat-meta">
              cover shorter than lead time{chain?.safety_days ? ` + ${chain.safety_days}d safety stock` : ''}
            </div>
          </div>
        </section>

        <section className="card">
          <div className="card-header">
            <h2 className="card-title">Stock levels</h2>
            <div className="segmented" role="group" aria-label="Filter stock">
              <button aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>
                All · {all.length}
              </button>
              <button aria-pressed={filter === 'low'} onClick={() => setFilter('low')}>
                Low · {lowCount}
              </button>
            </div>
          </div>

          <Loaded load={itemsLoad} what="inventory" placeholderHeight={120}>
            {() =>
              all.length === 0 ? (
                <Empty
                  title="No inventory yet"
                  action={
                    !adding && (
                      <button className="btn btn-secondary" onClick={() => setAdding(true)}>
                        Add your first item
                      </button>
                    )
                  }
                >
                  Add what you keep in stock, and the agent can warn you before it runs out.
                </Empty>
              ) : (
                <div className="table-wrap">
                  <table className="table table-cards">
                    <thead>
                      <tr>
                        <th>Item</th>
                        <th style={{ width: '20%' }}>Level</th>
                        <th className="num">On hand</th>
                        <th className="num">Reorder at</th>
                        <th className="num">Days of cover</th>
                        <th className="num">Value</th>
                        <th className="actions" aria-label="Actions" />
                      </tr>
                    </thead>
                    <tbody>
                      {rows.length === 0 && (
                        <tr>
                          <td colSpan={7} className="empty">
                            Nothing is below its reorder point.
                          </td>
                        </tr>
                      )}
                      {rows.map((item) => {
                        const c = cover.get(item.id)
                        const open = panel?.itemId === item.id ? panel.kind : null
                        return (
                          <Fragment key={item.id}>
                            <tr className={item.low_stock ? 'highlight' : undefined}>
                              <td data-label="Item">
                                <div className="cell-title">{item.name}</div>
                                <div className="cell-sub">{supplierName(item.supplier_id)}</div>
                              </td>
                              <td data-label="Level">
                                <div className="row">
                                  <div
                                    className="meter"
                                    style={{ flex: 1 }}
                                    title={`Reorder point at ${qty(item.reorder_threshold)} ${item.unit}`}
                                  >
                                    <div
                                      className={`meter-fill${item.low_stock ? ' danger' : ''}`}
                                      style={{ width: `${fill(item.quantity_on_hand, item.reorder_threshold)}%` }}
                                    />
                                    <span className="meter-mark" style={{ left: '50%' }} />
                                  </div>
                                  {item.low_stock && <span className="badge badge-danger">Low</span>}
                                </div>
                              </td>
                              <td className="num" data-label="On hand" title={`Updated ${dateTime(item.updated_at)}`}>
                                <span className="strong">{qty(item.quantity_on_hand)}</span>{' '}
                                <span className="muted">{item.unit}</span>
                              </td>
                              <td className="num muted" data-label="Reorder at">{qty(item.reorder_threshold)}</td>
                              <td className="num" data-label="Days of cover">
                                {c?.days_of_cover == null ? (
                                  <span className="faint" title="No recipe links this item to sales yet">
                                    —
                                  </span>
                                ) : (
                                  <span className={c.at_risk ? 'negative strong' : undefined}>
                                    {qty(c.days_of_cover)}d
                                    {c.lead_time_days != null && <span className="muted"> / {c.lead_time_days}d lead</span>}
                                  </span>
                                )}
                              </td>
                              <td className="num" data-label="Value">{money(item.quantity_on_hand * item.unit_cost)}</td>
                              <td className="actions" data-label="Actions">
                                <div className="row" style={{ justifyContent: 'flex-end' }}>
                                  <button
                                    className="btn btn-secondary btn-sm"
                                    aria-expanded={open === 'change'}
                                    aria-label={`Record a stock change for ${item.name}`}
                                    onClick={() => setPanel(open === 'change' ? null : { itemId: item.id, kind: 'change' })}
                                  >
                                    Record change
                                  </button>
                                  <button
                                    className="btn btn-ghost btn-sm"
                                    aria-label={`History of ${item.name}`}
                                    onClick={() => {
                                      setHistoryItem(item.id)
                                      document.getElementById('stock-history')?.scrollIntoView({ behavior: 'smooth' })
                                    }}
                                  >
                                    History
                                  </button>
                                  {canEditDetails && (
                                    <button
                                      className="btn btn-ghost btn-sm"
                                      aria-expanded={open === 'edit'}
                                      aria-label={`Edit ${item.name}`}
                                      onClick={() => setPanel(open === 'edit' ? null : { itemId: item.id, kind: 'edit' })}
                                    >
                                      Edit
                                    </button>
                                  )}
                                </div>
                              </td>
                            </tr>
                            {open && (
                              <tr className="panel-row">
                                <td colSpan={7}>
                                  {open === 'change' ? (
                                    <StockChangeForm
                                      item={item}
                                      onCancel={() => setPanel(null)}
                                      onSaved={async (message) => {
                                        setPanel(null)
                                        app.toast(message)
                                        await saved()
                                      }}
                                    />
                                  ) : (
                                    <EditItemForm
                                      item={item}
                                      suppliers={suppliers}
                                      onCancel={() => setPanel(null)}
                                      onConflict={saved}
                                      onSaved={async () => {
                                        setPanel(null)
                                        app.toast('Item updated', item.name)
                                        await saved()
                                      }}
                                    />
                                  )}
                                </td>
                              </tr>
                            )}
                          </Fragment>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )
            }
          </Loaded>
          <div className="card-footer">
            The tick on each bar marks the reorder point. Days of cover comes from real sales and recipes, so it’s blank
            for items no recipe uses.
            {!canEditDetails && ' Only the owner or an admin can edit an item’s details; anyone can record stock changes.'}
          </div>
        </section>

        <section className="card" id="stock-history">
          <div className="card-header">
            <div>
              <h2 className="card-title">
                {historyItem ? `History of ${all.find((i) => i.id === historyItem)?.name ?? 'item'}` : 'Recent stock changes'}
              </h2>
              <div className="card-subtitle">Every change to stock: how much, why, who, and when</div>
            </div>
            {historyItem && (
              <button className="btn btn-ghost btn-sm" onClick={() => setHistoryItem(null)}>
                Show all items
              </button>
            )}
          </div>
          <Loaded load={historyLoad} what="stock changes" placeholderHeight={160}>
            {(history) =>
              history.length === 0 ? (
                <Empty title="No stock changes yet">Deliveries, usage, waste and counts show up here as they’re recorded.</Empty>
              ) : (
                <ul className="list" aria-label="Stock changes">
                  {history.map((a) => (
                    <li key={a.id} className="list-item">
                      <span className={`stock-change ${a.change > 0 ? 'positive' : 'negative'}`}>
                        {a.change > 0 ? '+' : '−'}
                        {qty(Math.abs(a.change))} {a.unit}
                      </span>
                      <div className="list-item-main">
                        <div className="strong truncate">
                          {a.item_name} · {a.reason_label}
                        </div>
                        <div className="muted small truncate">
                          {who(a)} · now {qty(a.quantity_after)} {a.unit}
                          {a.note && ` · “${a.note}”`}
                        </div>
                      </div>
                      <span className="muted small" title={dateTime(a.created_at)}>
                        {relativeTime(a.created_at)}
                      </span>
                    </li>
                  ))}
                </ul>
              )
            }
          </Loaded>
        </section>
      </div>
    </main>
  )
}

function FormError({ message }: { message: string | null }) {
  return message ? (
    <div className="auth-error" role="alert">
      {message}
    </div>
  ) : null
}

function StockChangeForm({
  item,
  onCancel,
  onSaved,
}: {
  item: InventoryItem
  onCancel: () => void
  onSaved: (message: string) => Promise<void>
}) {
  const [reason, setReason] = useState<StockReason>('delivery')
  const [amount, setAmount] = useState('')
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const meta = REASONS.find((r) => r.id === reason)!
  // One id per entry, kept across retries so a resend after a dropped
  // connection can't record it twice; editing the entry makes it a new one.
  const requestKey = useMemo(() => newRequestKey(), [reason, amount, note])

  const parsed = parseAmount(amount, { allowZero: reason === 'count' })
  const after =
    typeof parsed === 'number'
      ? reason === 'count'
        ? parsed
        : reason === 'delivery'
          ? item.quantity_on_hand + parsed
          : item.quantity_on_hand - parsed
      : null

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (typeof parsed === 'string') return setError(parsed)
    if (after !== null && after < 0) {
      return setError(`Only ${qty(item.quantity_on_hand)} ${item.unit} is on hand. Check the amount, or record a stock count.`)
    }
    setBusy(true)
    setError(null)
    try {
      const result = await recordStockChange(item.id, reason, parsed, note.trim() || undefined, requestKey)
      await onSaved(
        result.adjustment
          ? `${item.name}: ${qty(result.item.quantity_on_hand)} ${item.unit} on hand`
          : `${item.name}: the count matches — nothing changed`,
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

  return (
    <form className="inline-form stack-sm" onSubmit={onSubmit} aria-label={`Stock change for ${item.name}`} noValidate>
      <div className="segmented" role="group" aria-label="What happened">
        {REASONS.map((r) => (
          <button key={r.id} type="button" aria-pressed={reason === r.id} onClick={() => (setReason(r.id), setError(null))}>
            {r.label}
          </button>
        ))}
      </div>
      <div className="form-row">
        <label className="field">
          <span className="field-label">
            {meta.amountLabel} ({item.unit})
          </span>
          <input
            className="input"
            inputMode="decimal"
            autoFocus
            aria-invalid={error && typeof parsed === 'string' ? true : undefined}
            value={amount}
            onChange={(e) => (setAmount(e.target.value), setError(null))}
          />
        </label>
        <label className="field">
          <span className="field-label">
            Note <span className="field-optional">(optional)</span>
          </span>
          <input
            className="input"
            maxLength={255}
            placeholder={reason === 'delivery' ? 'Invoice number, supplier…' : 'What for?'}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </label>
      </div>
      <div className="muted small" aria-live="polite">
        {qty(item.quantity_on_hand)} {item.unit} on hand now
        {after !== null && after >= 0 && ` → ${qty(Math.round(after * 1e6) / 1e6)} ${item.unit} after this`}
      </div>
      <FormError message={error} />
      <div className="row">
        <button type="submit" className="btn btn-primary btn-sm" disabled={busy}>
          {busy ? 'Saving…' : 'Save change'}
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </form>
  )
}

type ItemDraft = {
  name: string
  unit: string
  quantity_on_hand: string
  reorder_threshold: string
  reorder_qty: string
  unit_cost: string
  supplier_id: string
}

/** Checks a draft the way the server will; returns the values to send, or the first problem. */
function checkDraft(draft: ItemDraft, withStock: boolean) {
  if (!draft.name.trim()) return 'Enter the item’s name.'
  if (!(UNITS as readonly string[]).includes(draft.unit)) return 'Choose a unit from the list.'
  const numbers: Record<string, number> = {}
  const fields: [keyof ItemDraft, string][] = [
    ...(withStock ? [['quantity_on_hand', 'On hand'] as [keyof ItemDraft, string]] : []),
    ['reorder_threshold', 'Reorder at'],
    ['reorder_qty', 'Reorder quantity'],
    ['unit_cost', 'Unit cost'],
  ]
  for (const [field, label] of fields) {
    const parsed = parseAmount(draft[field] || '0', { allowZero: true })
    if (typeof parsed === 'string') return `${label}: ${parsed}`
    numbers[field] = parsed
  }
  return {
    name: draft.name.trim(),
    unit: draft.unit,
    ...numbers,
    supplier_id: draft.supplier_id || null,
  }
}

function ItemFields({
  draft,
  setDraft,
  suppliers,
  withStock,
}: {
  draft: ItemDraft
  setDraft: (d: ItemDraft) => void
  suppliers: Supplier[]
  withStock: boolean
}) {
  const set = (field: keyof ItemDraft) => (e: ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setDraft({ ...draft, [field]: e.target.value })
  return (
    <div className="form-row">
      <label className="field">
        <span className="field-label">Name</span>
        <input className="input" maxLength={255} value={draft.name} onChange={set('name')} />
      </label>
      <label className="field">
        <span className="field-label">Unit</span>
        <select className="select" value={draft.unit} onChange={set('unit')}>
          {!(UNITS as readonly string[]).includes(draft.unit) && <option value={draft.unit}>{draft.unit || 'Choose…'}</option>}
          {UNITS.map((u) => (
            <option key={u} value={u}>
              {u}
            </option>
          ))}
        </select>
      </label>
      {withStock && (
        <label className="field">
          <span className="field-label">On hand now</span>
          <input className="input" inputMode="decimal" value={draft.quantity_on_hand} onChange={set('quantity_on_hand')} />
        </label>
      )}
      <label className="field">
        <span className="field-label">Reorder at</span>
        <input className="input" inputMode="decimal" value={draft.reorder_threshold} onChange={set('reorder_threshold')} />
      </label>
      <label className="field">
        <span className="field-label">Reorder quantity</span>
        <input className="input" inputMode="decimal" value={draft.reorder_qty} onChange={set('reorder_qty')} />
      </label>
      <label className="field">
        <span className="field-label">Unit cost ($)</span>
        <input className="input" inputMode="decimal" value={draft.unit_cost} onChange={set('unit_cost')} />
      </label>
      <label className="field">
        <span className="field-label">Supplier</span>
        <select className="select" value={draft.supplier_id} onChange={set('supplier_id')}>
          <option value="">No supplier</option>
          {suppliers.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </label>
    </div>
  )
}

function AddItemForm({
  suppliers,
  onCancel,
  onSaved,
}: {
  suppliers: Supplier[]
  onCancel: () => void
  onSaved: (item: InventoryItem) => Promise<void>
}) {
  const [draft, setDraft] = useState<ItemDraft>({
    name: '',
    unit: 'each',
    quantity_on_hand: '0',
    reorder_threshold: '0',
    reorder_qty: '0',
    unit_cost: '0',
    supplier_id: '',
  })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    const checked = checkDraft(draft, true)
    if (typeof checked === 'string') return setError(checked)
    setBusy(true)
    setError(null)
    try {
      await onSaved(await createInventoryItem(checked))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

  return (
    <form className="card" onSubmit={onSubmit} aria-labelledby="add-item-title" noValidate>
      <div className="card-header">
        <div>
          <h2 className="card-title" id="add-item-title">
            Add an item
          </h2>
          <div className="card-subtitle">Its stock on hand now is the first entry in its history.</div>
        </div>
      </div>
      <div className="card-body stack-sm">
        <ItemFields draft={draft} setDraft={(d) => (setDraft(d), setError(null))} suppliers={suppliers} withStock />
        <FormError message={error} />
        <div className="row">
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? 'Adding…' : 'Add item'}
          </button>
          <button type="button" className="btn btn-ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
        </div>
      </div>
    </form>
  )
}

const draftOf = (item: InventoryItem): ItemDraft => ({
  name: item.name,
  unit: item.unit,
  quantity_on_hand: String(item.quantity_on_hand),
  reorder_threshold: String(item.reorder_threshold),
  reorder_qty: String(item.reorder_qty),
  unit_cost: String(item.unit_cost),
  supplier_id: item.supplier_id ?? '',
})

function EditItemForm({
  item,
  suppliers,
  onCancel,
  onSaved,
  onConflict,
}: {
  item: InventoryItem
  suppliers: Supplier[]
  onCancel: () => void
  onSaved: () => Promise<void>
  /** Someone else saved first: fetch their version. */
  onConflict: () => Promise<void>
}) {
  // The item as it was when editing started. `item` refreshes live while the
  // form is open, so it can't stand in for what this person was editing.
  const [base, setBase] = useState(item)
  const [draft, setDraft] = useState<ItemDraft>(() => draftOf(item))
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [rebase, setRebase] = useState(false)

  // After a conflict, start again from the version now saved, so the form
  // shows what the other person changed.
  useEffect(() => {
    if (rebase && item !== base) {
      setBase(item)
      setDraft(draftOf(item))
      setRebase(false)
    }
  }, [rebase, item, base])

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    const checked = checkDraft(draft, false)
    if (typeof checked === 'string') return setError(checked)
    setBusy(true)
    setError(null)
    try {
      // What this form started from, so someone else's change in the meantime isn't overwritten.
      const expected = Object.fromEntries(Object.keys(checked).map((field) => [field, base[field as keyof InventoryItem]]))
      await updateInventoryItem(item.id, checked, expected)
      await onSaved()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
      if (err instanceof ApiError && err.status === 409) {
        await onConflict()
        setRebase(true)
      }
    }
  }
  return (
    <form className="inline-form stack-sm" onSubmit={onSubmit} aria-label={`Edit ${item.name}`} noValidate>
      <ItemFields draft={draft} setDraft={(d) => (setDraft(d), setError(null))} suppliers={suppliers} withStock={false} />
      <div className="muted small">To change the amount on hand, record a stock change so it shows in the history.</div>
      <FormError message={error} />
      <div className="row">
        <button type="submit" className="btn btn-primary btn-sm" disabled={busy}>
          {busy ? 'Saving…' : 'Save details'}
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </form>
  )
}
