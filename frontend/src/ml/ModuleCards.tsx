import type { AppContext } from '../app/context'
import { useLoad } from '../lib/useLoad'
import { fetchModuleCards, ML_FLAGS, type ModuleCard } from './api'
import { Sparkline } from './charts'
import { dayLabel, dollars, percent, useRestaurantClock } from './format'

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
  if (card.module === 'usage') {
    if (card.state === 'setup') {
      const n = Number(card.recipes_needed)
      const sentence = card.has_sales ? `Add ${n} ${n === 1 ? 'recipe' : 'recipes'} to turn on Usage.` : 'Import 28 days of sales to turn on Usage.'
      return { key: card.module, title: 'Usage', sentence, href: '#usage', button: 'Open' }
    }
    const n = Number(card.items)
    const top = card.top as { item_id: string; name: string; trend: (string | null)[] }
    return {
      key: card.module,
      title: 'Usage',
      sentence: `About ${dollars(String(card.weekly_dollars))} a week of ingredients is unexplained, across ${n} ${n === 1 ? 'item' : 'items'}.`,
      number: `${dollars(String(card.weekly_dollars))} a week`,
      trend: top.trend.map((v) => (v == null ? null : Math.max(0, Number(v)))),
      trendLabel: `${top.name}: unexplained dollars a week over 8 weeks`,
      href: `#usage?checks=${top.item_id}`,
      button: 'See checks',
    }
  }
  if (card.module === 'ordering') {
    if (card.state === 'setup') {
      const missing = card.missing ?? []
      return { key: card.module, title: 'Ordering and prep', sentence: `${missing.length} ${missing.length === 1 ? 'step is' : 'steps are'} left to turn on Ordering and prep.`, href: '#ordering-prep', button: 'Open' }
    }
    const n = Number(card.orders)
    return { key: card.module, title: 'Ordering and prep', sentence: `${n} ${n === 1 ? 'order' : 'orders'} to review. First: ${String(card.supplier)}, by ${dayLabel(String(card.cutoff_at))}.`, number: card.total ? dollars(String(card.total)) : undefined, href: '#ordering-prep', button: 'Review order' }
  }
  if (card.module === 'channels') {
    if (card.state === 'setup') {
      const missing = card.missing ?? []
      return { key: card.module, title: 'Channels', sentence: `${missing.length} ${missing.length === 1 ? 'step is' : 'steps are'} left to turn on Channels.`, href: '#channels', button: 'Open' }
    }
    const n = Number(card.items)
    const where = (card.platform as string | null) ?? 'delivery platforms'
    const what = card.losing ? (n === 1 ? 'item loses money' : 'items lose money') : n === 1 ? 'item keeps too little' : 'items keep too little'
    const top = card.top as { row_id: string }
    return { key: card.module, title: 'Channels', sentence: `${n} ${what} on ${where}.`, number: `${dollars(String(card.weekly_dollars))} a week`, href: `#channels?review=${top.row_id}`, button: 'Review price' }
  }
  return null
}

/** The Margin & Labor cards on the Digest: at most one per module that is on. Renders nothing when none is. */
export function ModuleCards({ app }: { app: AppContext }) {
  useRestaurantClock(app.restaurant.timezone)
  const on = ML_FLAGS.some((flag) => app.flags[flag])
  const load = useLoad(() => (on ? fetchModuleCards() : Promise.resolve([] as ModuleCard[])), [on])
  // Channels is taken out for now: its card is not shown.
  const cards = (load.data ?? []).filter((card) => card.module !== 'channels').map(words).filter((c): c is Shown => c !== null)
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
