import type { AgentAction } from '../api/client'

/** The detail a proposal carries, by kind: order lines, recipe lines, prep quantities, a plate cost before and after. */
export function ActionBody({ action }: { action: AgentAction }) {
  const p = action.payload
  if (action.action_type === 'recipe' && Array.isArray(p.lines)) {
    return (
      <ul className="small" style={{ margin: '8px 0 0 16px', padding: 0 }} aria-label="Recipe lines">
        {(p.lines as { ingredient_text: string; item_name: string | null; qty: number; unit: string; matched: boolean }[]).map((l, i) => (
          <li key={i} className={l.matched ? undefined : 'muted'}>
            {l.qty} {l.unit} {l.item_name ?? l.ingredient_text}
            {!l.matched && ' (not on the invoices; left out unless you add it later)'}
          </li>
        ))}
      </ul>
    )
  }
  if (action.action_type === 'purchase_order' && Array.isArray(p.lines)) {
    return (
      <table className="table small" style={{ marginTop: 8 }} aria-label="Order lines">
        <tbody>
          {(p.lines as { description: string; qty: number; unit: string; unit_price_expected: number | null }[]).map((l, i) => (
            <tr key={i}>
              <td>{l.description}</td>
              <td className="num">
                {l.qty} {l.unit}
              </td>
              <td className="num">{l.unit_price_expected != null ? `$${l.unit_price_expected.toFixed(2)}` : ''}</td>
            </tr>
          ))}
          {p.expected_total != null && (
            <tr>
              <td className="strong">Expected total</td>
              <td />
              <td className="num strong">${Number(p.expected_total).toFixed(2)}</td>
            </tr>
          )}
        </tbody>
      </table>
    )
  }
  if (action.action_type === 'prep_sheet' && Array.isArray(p.lines)) {
    return (
      <ul className="small" style={{ margin: '8px 0 0 16px', padding: 0 }} aria-label="Prep lines">
        {(p.lines as { item_name: string; qty: number; unit: string }[]).slice(0, 12).map((l, i) => (
          <li key={i}>
            {l.item_name}: {l.qty} {l.unit}
          </li>
        ))}
      </ul>
    )
  }
  if (action.action_type === 'menu_reprice') {
    return (
      <div className="small" style={{ marginTop: 6 }}>
        Plate cost {p.plate_cost_before != null ? `$${Number(p.plate_cost_before).toFixed(2)} → ` : ''}${Number(p.plate_cost_after ?? 0).toFixed(2)}
        {p.current_price != null && ` · sells at $${Number(p.current_price).toFixed(2)}`}
      </div>
    )
  }
  return null
}
