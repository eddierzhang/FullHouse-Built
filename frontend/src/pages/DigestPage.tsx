import { useState } from 'react'
import {
  type DigestCard,
  type DigestSection,
  type DigestSectionKey,
  createDraft,
  emailDigest,
  fetchDigest,
  fetchPriceStatus,
  hidePriceItem,
  movePriceItem,
  repriceDishRecipe,
  unhidePriceItem,
} from '../api/client'
import type { AppContext } from '../app/context'
import { Loaded } from '../components/PageState'
import { DigestCardView, Money, sectionClass, usd } from '../components/prices'
import { shortDate } from '../lib/format'
import { useLoad } from '../lib/useLoad'

/** What an empty section says: one sentence, never hidden. */
const EMPTY: Record<DigestSectionKey, string> = {
  switch: 'Nothing to move this week.',
  up: 'No price went up.',
  owed: 'Nothing owed to you.',
  margin: 'No dish lost margin this week.',
  due: 'Nothing coming due.',
}

type Collapsed = { kind: 'hidden'; until: string } | { kind: 'moved' }

/**
 * The week in one number, then three sections in a fixed order: what to
 * move, what went up, what's owed. Cards are ranked by dollars; nothing
 * sorts or filters. "Not useful" is the only feedback.
 */
export function DigestPage({ app }: { app: AppContext }) {
  const digest = useLoad(fetchDigest, [])
  const status = useLoad(fetchPriceStatus, [])
  const [collapsed, setCollapsed] = useState<Record<string, Collapsed>>({})
  const [busy, setBusy] = useState<string | null>(null)

  const act = async (card: DigestCard) => {
    setBusy(card.item_id)
    try {
      if (card.action.kind === 'move' && card.action.supplier_id) {
        await movePriceItem(card.item_id, card.action.supplier_id)
        setCollapsed((c) => ({ ...c, [card.item_id]: { kind: 'moved' } }))
        app.refresh()
      } else if (card.action.kind === 'reprice' && card.recipe_id) {
        const out = await repriceDishRecipe(card.recipe_id)
        app.toast('Reprice suggested', 'Under Recommendations: ' + out.note)
        app.refresh()
      } else if (card.action.kind === 'calendar') {
        window.location.hash = '#calendar'
      } else {
        const draft = await createDraft(card.item_id, card.action.draft_kind, card.action.supplier_id)
        window.location.hash = `#draft/${draft.id}`
      }
    } catch (err) {
      app.toast('Couldn’t do that', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  const hide = async (card: DigestCard) => {
    setBusy(card.item_id)
    try {
      const item = await hidePriceItem(card.item_id)
      setCollapsed((c) => ({ ...c, [card.item_id]: { kind: 'hidden', until: item.hidden_until ?? '' } }))
    } catch (err) {
      app.toast('Couldn’t hide it', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  const email = async () => {
    setBusy('email')
    try {
      const out = await emailDigest()
      app.toast(out.delivered ? `Sent to ${out.sent_to.join(', ')}` : 'No mailbox is set up on this site. The digest was printed to the server log.')
    } catch (err) {
      app.toast('Couldn’t send it', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  const undo = async (card: DigestCard) => {
    try {
      await unhidePriceItem(card.item_id)
      setCollapsed((c) => {
        const next = { ...c }
        delete next[card.item_id]
        return next
      })
    } catch (err) {
      app.toast('Couldn’t undo', err instanceof Error ? err.message : String(err))
    }
  }

  const section = (s: DigestSection) => (
    <section key={s.key} className="pi-stack" aria-labelledby={`section-${s.key}`}>
      <h2 id={`section-${s.key}`} className={`pi-section-head ${sectionClass(s.key)}`.trim()}>
        <span>{s.label}</span>
        {s.cards.length > 0 && <Money value={s.total} />}
      </h2>
      {s.cards.length === 0 && <p className="pi-secondary">{EMPTY[s.key]}</p>}
      {s.cards.map((card) => {
        const state = collapsed[card.item_id]
        if (state?.kind === 'hidden') {
          return (
            <div key={card.item_id} className="pi-muted-line" role="status">
              <span>
                {card.item_name} hidden{state.until ? ` until ${shortDate(state.until)}` : ' for 4 weeks'}.
              </span>
              <button type="button" className="pi-link" onClick={() => void undo(card)}>
                Undo
              </button>
            </div>
          )
        }
        if (state?.kind === 'moved') {
          return (
            <div key={card.item_id} className="pi-muted-line" role="status">
              <span>
                {card.item_name}: moving. The next digest checks the invoice.
              </span>
            </div>
          )
        }
        return <DigestCardView key={card.item_id} card={card} busy={busy === card.item_id} onAction={(c) => void act(c)} onHide={(c) => void hide(c)} />
      })}
    </section>
  )

  return (
    <main className="pi">
      <div className="pi-page">
        <Loaded load={digest} what="the digest" placeholderHeight={160}>
          {(d) => (
            <>
              <header className="pi-stack-sm">
                {d.prime && (
                  <p className="pi-label-strong" data-testid="prime-line">
                    {d.prime.complete
                      ? `Prime cost ${d.prime.prime_pct}% last week: food ${d.prime.food_pct}%, labor ${d.prime.labor_pct}%, on ${usd(d.prime.sales_net ?? 0)} in sales.`
                      : d.prime.prompt}
                    {!d.prime.complete && app.flags.v2_connect && (
                      <>
                        {' '}
                        <a className="pi-link pi-link-inline" href="#connect">
                          Connections
                        </a>
                      </>
                    )}
                  </p>
                )}
                <h1 className="pi-headline">
                  <Money value={d.total} />
                </h1>
                <p className="pi-sub">
                  at stake this week, across {d.item_count} {d.item_count === 1 ? 'item' : 'items'}, {d.supplier_count}{' '}
                  {d.supplier_count === 1 ? 'supplier' : 'suppliers'}
                </p>
                {d.questions_open > 0 ? (
                  <a className="pi-back" href="#questions" style={{ color: '#111' }}>
                    {d.questions_open} {d.questions_open === 1 ? 'question' : 'questions'} waiting. Answer {d.questions_open === 1 ? 'it' : 'them'}
                  </a>
                ) : status.data && status.data.invoice_count === 0 ? (
                  <a className="pi-back" href="#invoices" style={{ color: '#111' }}>
                    No invoices yet. Add the first one
                  </a>
                ) : null}
                {app.canManage && (
                  <button type="button" className="pi-link" style={{ alignSelf: 'flex-start' }} disabled={busy === 'email'} onClick={() => void email()}>
                    Email me this digest
                  </button>
                )}
              </header>

              {d.sections.map(section)}

              {d.hidden_count > 0 && (
                <a className="pi-muted-line" href="#items">
                  {d.hidden_count} smaller {d.hidden_count === 1 ? 'item' : 'items'}
                </a>
              )}

              {(d.forecast_accuracy !== null && d.forecast_accuracy !== undefined) && (
                <p className="pi-secondary" data-testid="forecast-accuracy">
                  Forecast accuracy last week: {d.forecast_accuracy}%
                </p>
              )}
              {!!d.recovered_to_date && (
                <p className="pi-secondary" data-testid="recovered">
                  Recovered to date: <Money value={d.recovered_to_date} />
                </p>
              )}
              <p className="pi-label">
                Week of {shortDate(d.week_start)}.
                {d.invoices_through && ` Invoices through ${shortDate(d.invoices_through)}`}
                {d.supplier_names.length > 0 && ` from ${d.supplier_names.join(', ')}`}
                {(d.invoices_through || d.supplier_names.length > 0) && '.'}
              </p>
            </>
          )}
        </Loaded>
      </div>
    </main>
  )
}
