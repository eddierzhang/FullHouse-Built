import { type Digest, fetchDigest } from '../api/client'
import type { AppContext } from '../app/context'
import { money } from '../lib/format'
import { useLoad } from '../lib/useLoad'
import { type ModuleCard, fetchModuleCards } from '../ml/api'

type Finding = { key: string; kind: string; title: string; sentence: string; dollars: number; period: string; href: string }

/** The week's findings from the digest: a cheaper supplier, a price that rose, a credit owed. */
function fromDigest(digest: Digest | null | undefined): Finding[] {
  const period: Record<string, string> = { switch: 'a week', up: 'a week', owed: 'owed to you', margin: 'a week' }
  return (digest?.sections ?? [])
    .filter((section) => section.key in period)
    .flatMap((section) =>
      section.cards.map((card) => ({
        key: `${section.key}-${card.item_id}`,
        kind: section.label,
        title: card.item_name,
        sentence: card.sentence,
        dollars: card.impact,
        period: period[section.key],
        href: '#digest',
      })),
    )
}

/** The findings of the margin and labor modules that carry dollars a week: dishes over target, unexplained use. */
function fromModules(cards: ModuleCard[] | null | undefined): Finding[] {
  const out: Finding[] = []
  for (const card of cards ?? []) {
    const dollars = Number(card.weekly_dollars ?? 0)
    if (card.state === 'setup' || !(dollars > 0)) continue
    const top = card.top as { name?: string } | undefined
    if (card.module === 'menu_costs') {
      const n = Number(card.dishes_over ?? 0)
      out.push({ key: 'menu', kind: 'Menu cost', title: top?.name ?? 'Menu costs', sentence: `${n} ${n === 1 ? 'dish is' : 'dishes are'} over your food cost target.`, dollars, period: 'a week', href: '#menu-costs' })
    } else if (card.module === 'usage') {
      const n = Number(card.items ?? 0)
      out.push({ key: 'usage', kind: 'Unexplained use', title: top?.name ?? 'Usage', sentence: `${n} ${n === 1 ? 'ingredient is' : 'ingredients are'} used faster than recipes explain.`, dollars, period: 'a week', href: '#usage' })
    }
  }
  return out
}

/**
 * The three findings worth the most this week, in dollars, at the top of Today. Each opens the page it comes from.
 * Nothing is drawn when there is nothing to act on.
 */
export function TopFindings({ app }: { app: AppContext }) {
  const digest = useLoad(() => fetchDigest().catch(() => null), [])
  const modules = useLoad(() => (app.flags.ml_menu_costs || app.flags.ml_usage ? fetchModuleCards().catch(() => []) : Promise.resolve([])), [app.flags.ml_menu_costs, app.flags.ml_usage])
  const findings = [...fromDigest(digest.data), ...fromModules(modules.data)].sort((a, b) => b.dollars - a.dollars).slice(0, 3)
  if (findings.length === 0) return null
  return (
    <section className="card" aria-labelledby="top-findings-title">
      <div className="card-header">
        <div>
          <h2 className="card-title" id="top-findings-title">
            Worth the most this week
          </h2>
          <div className="card-subtitle">The {findings.length === 1 ? 'finding' : `${findings.length} findings`} with the most dollars behind {findings.length === 1 ? 'it' : 'them'}. Nothing changes until you act.</div>
        </div>
      </div>
      <ol className="top-findings">
        {findings.map((finding, index) => (
          <li key={finding.key}>
            <a href={finding.href}>
              <span className="top-findings-rank">{index + 1}</span>
              <span className="top-findings-text">
                <span className="row wrap">
                  <span className="strong">{finding.title}</span>
                  <span className="badge badge-brand">{finding.kind}</span>
                </span>
                <span className="muted small">{finding.sentence}</span>
              </span>
              <span className="top-findings-dollars">
                <span className="strong">{money(finding.dollars, true)}</span>
                <span className="muted small">{finding.period}</span>
              </span>
            </a>
          </li>
        ))}
      </ol>
    </section>
  )
}
