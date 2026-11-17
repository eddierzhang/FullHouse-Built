import type { AppContext } from '../app/context'
import { useLoad } from '../lib/useLoad'
import { fetchModuleCards, ML_FLAGS, type ModuleCard } from './api'
import { Sparkline } from './charts'
import { dollars, percent } from './format'

type Shown = { key: string; title: string; sentence: string; number?: string; trend?: (number | null)[]; trendLabel?: string; href: string; button: string }

const SETUP: Record<string, string> = { invoices: 'Add invoices', recipes: 'Confirm recipes', costed: 'Price recipe lines', prices: 'Add menu prices' }

/** One card's words, by module. One sentence, one number, at most one sparkline, one button. */
function words(card: ModuleCard): Shown | null {
  if (card.module === 'menu_costs') {
    if (card.state === 'setup') {
      const missing = card.missing ?? []
      return { key: card.module, title: 'Menu costs', sentence: `${missing.length} ${missing.length === 1 ? 'step is' : 'steps are'} left to turn on Menu costs: ${missing.map((m) => SETUP[m] ?? m).join(', ')}.`, href: '#menu-costs', button: 'Open' }
    }
    const n = Number(card.dishes_over)
    const top = card.top as { recipe_id: string; name: string; trend: (string | null)[] }
    return {
      key: card.module,
      title: 'Menu costs',
      sentence: `${n} ${n === 1 ? 'dish is' : 'dishes are'} over your ${percent(String(card.target))} food cost target.`,
      number: `${dollars(String(card.weekly_dollars))} a week`,
      trend: top.trend.map((v) => (v == null ? null : Number(v))),
      trendLabel: `${top.name}: cost per plate over 12 weeks`,
      href: `#menu-costs?review=${top.recipe_id}`,
      button: 'Review price',
    }
  }
  return null
}

/** The Margin & Labor cards on the Digest: at most one per module that is on. Renders nothing when none is. */
export function ModuleCards({ app }: { app: AppContext }) {
  const on = ML_FLAGS.some((flag) => app.flags[flag])
  const load = useLoad(() => (on ? fetchModuleCards() : Promise.resolve([] as ModuleCard[])), [on])
  const cards = (load.data ?? []).map(words).filter((c): c is Shown => c !== null)
  if (!on || cards.length === 0) return null
  return (
    <section className="ml-cards" aria-label="Margin and labor">
      {cards.map((card) => (
        <article key={card.key} className="card ml-card" data-testid={`module-card-${card.key}`}>
          <div className="ml-card-main">
            <h2 className="ml-card-title">{card.title}</h2>
            <p className="ml-card-sentence">{card.sentence}</p>
          </div>
          {card.number && <div className="ml-card-number">{card.number}</div>}
          {card.trend && <Sparkline values={card.trend} label={card.trendLabel ?? ''} />}
          <a className="btn btn-secondary" href={card.href}>
            {card.button}
          </a>
        </article>
      ))}
    </section>
  )
}
