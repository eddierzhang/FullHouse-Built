import { useEffect, useState } from 'react'
import { type InventoryItem, type Recipe, getRecipe, setRecipe } from '../api/client'
import { byName, money, pct } from '../lib/format'
import { Icon } from './Icon'

type Props = {
  menuItemId: string
  ingredients: InventoryItem[]
  onSaved: () => void
  onClose: () => void
  toast: (title: string, subtitle?: string) => void
}

type Line = { inventory_item_id: string; quantity: string }

export function RecipeEditor({ menuItemId, ingredients, onSaved, onClose, toast }: Props) {
  const [recipe, setRecipeState] = useState<Recipe | null>(null)
  const [lines, setLines] = useState<Line[]>([])
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    getRecipe(menuItemId)
      .then((loaded) => {
        setRecipeState(loaded)
        setLines(loaded.lines.map((l) => ({ inventory_item_id: l.inventory_item_id, quantity: String(l.quantity) })))
      })
      .catch((err) => toast('Couldn’t load the recipe', String(err)))
  }, [menuItemId, toast])

  const byId = new Map(ingredients.map((i) => [i.id, i]))
  const sortedIngredients = [...ingredients].sort(byName)

  // Repriced as you type, so the margin moves with the recipe.
  const cost = lines.reduce((sum, line) => {
    const ingredient = byId.get(line.inventory_item_id)
    const quantity = Number(line.quantity)
    return sum + (ingredient && Number.isFinite(quantity) ? ingredient.unit_cost * quantity : 0)
  }, 0)
  const price = recipe?.price ?? 0
  const margin = price > 0 ? ((price - cost) / price) * 100 : 0

  const update = (index: number, patch: Partial<Line>) =>
    setLines(lines.map((line, i) => (i === index ? { ...line, ...patch } : line)))

  const save = async () => {
    const payload = lines
      .filter((l) => l.inventory_item_id && Number(l.quantity) > 0)
      .map((l) => ({ inventory_item_id: l.inventory_item_id, quantity: Number(l.quantity) }))
    if (new Set(payload.map((l) => l.inventory_item_id)).size !== payload.length) {
      toast('The same ingredient is listed twice')
      return
    }
    setBusy(true)
    try {
      await setRecipe(menuItemId, payload)
      toast(payload.length ? 'Recipe saved' : 'Recipe cleared')
      onSaved()
      onClose()
    } catch (err) {
      toast('Couldn’t save the recipe', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  if (!recipe) return <div className="skeleton" style={{ height: 120 }} />

  return (
    <div className="stack" style={{ gap: 12 }}>
      <div className="row wrap" style={{ justifyContent: 'space-between' }}>
        <div>
          <div className="strong">Recipe for {recipe.menu_item_name}</div>
          <div className="muted small">Per serving. Selling the dish draws these from stock.</div>
        </div>
        <div className="row" style={{ gap: 24 }}>
          <div>
            <div className="stat-label">Ingredient cost</div>
            <div className="strong tabular">{money(cost, true)}</div>
          </div>
          <div>
            <div className="stat-label">Margin at {money(price, true)}</div>
            <div className="strong tabular">{pct(margin)}</div>
          </div>
        </div>
      </div>

      {lines.length === 0 && (
        <div className="muted small">
          No recipe yet — the dish’s cost is whatever was typed in, and selling it uses no stock.
        </div>
      )}

      {lines.map((line, index) => {
        const ingredient = byId.get(line.inventory_item_id)
        const lineCost = ingredient && Number(line.quantity) > 0 ? ingredient.unit_cost * Number(line.quantity) : null
        return (
          <div key={index} className="row">
            <select
              className="select input-sm"
              aria-label="Ingredient"
              value={line.inventory_item_id}
              onChange={(e) => update(index, { inventory_item_id: e.target.value })}
            >
              <option value="">Choose an ingredient…</option>
              {sortedIngredients.map((i) => (
                <option key={i.id} value={i.id}>
                  {i.name} ({money(i.unit_cost, true)}/{i.unit})
                </option>
              ))}
            </select>
            <input
              className="input input-sm"
              style={{ width: 90 }}
              aria-label="Quantity"
              inputMode="decimal"
              placeholder="Qty"
              value={line.quantity}
              onChange={(e) => update(index, { quantity: e.target.value })}
            />
            <span className="muted small" style={{ width: 40 }}>
              {ingredient?.unit}
            </span>
            <span className="tabular small" style={{ width: 70, textAlign: 'right' }}>
              {lineCost === null ? '—' : money(lineCost, true)}
            </span>
            <button className="btn btn-ghost btn-sm btn-icon" aria-label="Remove ingredient" onClick={() => setLines(lines.filter((_, i) => i !== index))}>
              <Icon name="close" size={14} />
            </button>
          </div>
        )
      })}

      <div className="row" style={{ justifyContent: 'flex-end' }}>
        <button className="btn btn-secondary btn-sm" onClick={() => setLines([...lines, { inventory_item_id: '', quantity: '' }])}>
          <Icon name="plus" size={14} /> Ingredient
        </button>
        <button className="btn btn-ghost btn-sm" onClick={onClose}>
          Cancel
        </button>
        <button className="btn btn-primary btn-sm" disabled={busy} onClick={save}>
          {busy ? 'Saving…' : 'Save recipe'}
        </button>
      </div>
    </div>
  )
}
