import { type ReactNode, useEffect } from 'react'
import type { AppContext } from '../app/context'
import { EmbeddedSection } from '../components/PageFrame'
import { MenuCostsPage } from '../ml/MenuCostsPage'
import { UsagePage } from '../ml/UsagePage'
import { LaborPage } from '../ml/LaborPage'
import type { User } from '../api/client'
import type { Route } from '../lib/useHashRoute'
import { MenuPage } from './MenuPage'
import { PrepSheetsPage } from './PrepSheetPage'
import { RecipesPage } from './RecipesPage'
import { StaffPage } from './StaffPage'

type Part = { id: Route; label: string; body: ReactNode }

/**
 * One page made of several, one under another, with a row of links to jump between them. The older address of
 * each part (#usage, #recipes, #labor and so on) opens the page at that part.
 */
function Sections({ title, subtitle, parts, at }: { title: string; subtitle: string; parts: Part[]; at: Route }) {
  useEffect(() => {
    if (parts.length === 0 || at === parts[0].id) return
    // The parts load on their own; wait a moment for the page to take its height.
    const timer = window.setTimeout(() => document.getElementById(`sec-${at}`)?.scrollIntoView({ block: 'start' }), 400)
    return () => window.clearTimeout(timer)
  }, [at]) // eslint-disable-line react-hooks/exhaustive-deps

  // With one part switched on, that part is the page: no second heading over it.
  if (parts.length === 1) return <>{parts[0].body}</>

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">{title}</h1>
          <p className="page-subtitle">{subtitle}</p>
        </div>
      </div>
      {(
        <nav className="jump-links" aria-label="On this page">
          {parts.map((part) => (
            <a key={part.id} href={`#${part.id}`}>
              {part.label}
            </a>
          ))}
        </nav>
      )}
      {parts.map((part) => (
        <EmbeddedSection key={part.id} id={part.id}>
          {part.body}
        </EmbeddedSection>
      ))}
    </main>
  )
}

/** Menu: what each dish costs, what the kitchen used, the dishes, their recipes and the prep sheets. */
export function MenuSectionsPage({ app, at }: { app: AppContext; at: Route }) {
  const parts: Part[] = []
  if (app.flags.ml_menu_costs) parts.push({ id: 'menu-costs', label: 'Menu costs', body: <MenuCostsPage app={app} /> })
  if (app.flags.ml_usage) parts.push({ id: 'usage', label: 'Usage', body: <UsagePage app={app} /> })
  parts.push({ id: 'menu', label: 'Dishes', body: <MenuPage app={app} /> })
  if (app.flags.v2_documents) parts.push({ id: 'recipes', label: 'Recipes', body: <RecipesPage app={app} /> })
  if (app.flags.v2_orders) parts.push({ id: 'prep', label: 'Prep sheets', body: <PrepSheetsPage app={app} /> })
  return <Sections title="Menu" subtitle="What each dish costs and earns, what the kitchen used, the recipes and the prep." parts={parts} at={at} />
}

/** Schedule & staff: the labor forecast and draft schedule, then the team and their shifts. */
export function StaffSectionsPage({ app, user, at }: { app: AppContext; user: User; at: Route }) {
  const parts: Part[] = []
  if (app.flags.ml_labor) parts.push({ id: 'labor', label: 'Labor', body: <LaborPage app={app} /> })
  parts.push({ id: 'staff', label: 'Team and shifts', body: <StaffPage app={app} user={user} /> })
  return <Sections title="Schedule & staff" subtitle="The hours the week needs, the draft schedule, the team and their shifts." parts={parts} at={at} />
}
