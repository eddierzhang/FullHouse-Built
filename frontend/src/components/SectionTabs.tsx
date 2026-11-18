import type { AppContext } from '../app/context'
import { useHashRoute } from '../lib/useHashRoute'

/**
 * The pages that share one sidebar entry, as tabs under the page header: Inventory holds stock, counting and
 * variance and waste; Suppliers & ordering holds the supplier list, deliveries, ordering and prep, and the autopilot. Each tab is a link, so every page keeps
 * its own address. Nothing is drawn when a section has only one page switched on.
 */
export function SectionTabs({ app, section }: { app: AppContext; section: 'inventory' | 'suppliers' }) {
  const [route] = useHashRoute()
  const tabs =
    section === 'inventory'
      ? [
          { route: 'inventory', label: 'Stock', on: true },
          { route: 'count', label: 'Count', on: app.flags.v3_stock === true },
          { route: 'variance', label: 'Variance & waste', on: app.flags.v3_stock === true },
        ]
      : [
          { route: 'suppliers', label: 'Suppliers', on: true },
          { route: 'receive', label: 'Deliveries', on: app.flags.v3_receiving === true },
          { route: 'ordering-prep', label: 'Ordering and prep', on: app.flags.ml_ordering === true },
          { route: 'autopilot', label: 'Autopilot', on: app.flags.v3_autopilot === true && app.canManage },
        ]
  const shown = tabs.filter((tab) => tab.on)
  if (shown.length < 2) return null
  return (
    <nav className="tabs" aria-label={section === 'inventory' ? 'Inventory pages' : 'Supplier and ordering pages'}>
      {shown.map((tab) => (
        <a key={tab.route} className="tab tab-link" href={`#${tab.route}`} aria-current={route === tab.route ? 'page' : undefined}>
          {tab.label}
        </a>
      ))}
    </nav>
  )
}
