import { type ReactNode, useEffect, useId, useState } from 'react'
import { Icon, type IconName } from '../components/Icon'
import { confidenceWords, dollars, rangeWords } from './format'

/**
 * The shared parts of every Margin & Labor page (spec section 10.12). Each page is the same shape, top to
 * bottom: a header sentence that is the answer, at most three tiles, one chart, one table. These are those
 * parts, built once. Status is always an icon and a word, never colour alone; every action is a button a
 * keyboard reaches; touch targets are at least 44 px.
 */

export type ModuleState = 'setup' | 'learning' | 'live'

/** The answer, with a number, a unit and a period. At most two short sentences. Never a description of the page. */
export function HeaderSentence({ title, sentence, updated, state, children }: { title: string; sentence: string; updated?: string; state?: ModuleState; children?: ReactNode }) {
  const label = { setup: 'Setup', learning: 'Learning', live: 'Live' }
  return (
    <header className="ml-header">
      <div className="ml-header-top">
        <h1 className="page-title">{title}</h1>
        <div className="ml-header-meta">
          {updated && <span className="muted small">Updated {updated}</span>}
          {state && (
            <span className={`ml-state ml-state-${state}`}>
              <Icon name={state === 'live' ? 'check' : state === 'learning' ? 'clock' : 'info'} size={14} />
              {label[state]}
            </span>
          )}
          {children}
        </div>
      </div>
      <p className="ml-sentence" data-testid="header-sentence">
        {sentence}
      </p>
    </header>
  )
}

/** One value, a label of at most three words, and the change against what it is compared with. Clicking the value explains it. */
export function StatTile({ value, label, compare, tone, onWhy }: { value: string; label: string; compare: string; tone?: 'good' | 'bad'; onWhy?: () => void }) {
  return (
    <div className="card ml-tile" data-testid="stat-tile">
      {onWhy ? (
        <button className="ml-tile-value ml-why-link" onClick={onWhy} aria-label={`${value}, ${label}. How this was worked out`}>
          {value}
        </button>
      ) : (
        <div className="ml-tile-value">{value}</div>
      )}
      <div className="ml-tile-label">{label}</div>
      <div className={`ml-tile-compare${tone ? ` ${tone}` : ''}`}>{compare}</div>
    </div>
  )
}

export function TileRow({ children }: { children: ReactNode }) {
  return <section className="ml-tiles" aria-label="Key figures">{children}</section>
}

/** A change in dollars with its direction in a word and an arrow: "up $0.31", "down $42". */
export function MoneyDelta({ amount, period, goodWhen = 'down' }: { amount: number | string; period?: string; goodWhen?: 'up' | 'down' }) {
  const n = Number(amount)
  if (!Number.isFinite(n) || n === 0) return <span className="ml-delta">no change{period ? ` ${period}` : ''}</span>
  const up = n > 0
  return (
    <span className={`ml-delta ${up === (goodWhen === 'up') ? 'good' : 'bad'}`}>
      <span aria-hidden="true">{up ? '▲' : '▼'}</span> {up ? 'up' : 'down'} {dollars(Math.abs(n))}
      {period ? ` ${period}` : ''}
    </span>
  )
}

/** A forecast, said as a range. */
export function RangeText({ low, expected, high, unit }: { low: number; expected: number; high: number; unit: string }) {
  return <span className="ml-range">{rangeWords(low, expected, high, unit)}</span>
}

export function ConfidenceLabel({ weeks, needed }: { weeks: number; needed: number }) {
  return <span className="ml-confidence">{confidenceWords(weeks, needed)}</span>
}

/** Learning, stale or failed: what is true, since when, and what to do. Numbers stay on the page under it. */
export function StateBanner({ kind, text, action }: { kind: 'learning' | 'stale' | 'error'; text: string; action?: { label: string; onClick?: () => void; href?: string } }) {
  const icon: IconName = kind === 'learning' ? 'clock' : 'alert'
  return (
    <div className={`ml-banner ml-banner-${kind}`} role={kind === 'error' ? 'alert' : 'status'}>
      <Icon name={icon} size={18} />
      <span className="ml-banner-text">{text}</span>
      {action &&
        (action.href ? (
          <a className="btn btn-secondary btn-sm" href={action.href}>
            {action.label}
          </a>
        ) : (
          <button className="btn btn-secondary btn-sm" onClick={action.onClick}>
            {action.label}
          </button>
        ))}
    </div>
  )
}

export type SetupStep = { label: string; done: boolean; detail?: string; action?: { label: string; href?: string; onClick?: () => void } }

/** What is missing before the page can show a number. No charts and no placeholder figures in this state. */
export function SetupChecklist({ title, steps }: { title: string; steps: SetupStep[] }) {
  const done = steps.filter((s) => s.done).length
  return (
    <section className="card ml-setup" aria-labelledby="ml-setup-title">
      <div className="card-header">
        <h2 className="card-title" id="ml-setup-title">
          {title}
        </h2>
        <span className="muted small">
          {done} of {steps.length} done
        </span>
      </div>
      <ul className="ml-setup-list">
        {steps.map((step) => (
          <li key={step.label} className={step.done ? 'done' : ''}>
            <Icon name={step.done ? 'check' : 'info'} size={18} />
            <span className="ml-setup-text">
              <span className="strong">{step.label}</span>
              {step.detail && <span className="muted small">{step.detail}</span>}
            </span>
            <span className="ml-setup-status">{step.done ? 'Done' : 'To do'}</span>
            {!step.done &&
              step.action &&
              (step.action.href ? (
                <a className="btn btn-secondary btn-sm" href={step.action.href}>
                  {step.action.label}
                </a>
              ) : (
                <button className="btn btn-secondary btn-sm" onClick={step.action.onClick}>
                  {step.action.label}
                </button>
              ))}
          </li>
        ))}
      </ul>
    </section>
  )
}

export type WhyInput = { label: string; value: string; source?: { label: string; href: string } }
export type Why = {
  /** The number, its unit and its period. */
  result: string
  /** One line in plain words. */
  formula: string
  inputs: WhyInput[]
  /** How much data it rests on: "9 weeks of sales, 14 invoices". */
  dataBasis: string
  /** When it was computed and when its inputs last changed. */
  freshness: string
  /** A link to correct an input. */
  fix?: { label: string; href: string }
}

/**
 * The side panel behind every derived number: the result, the formula in plain words, each input with where
 * it came from, how much data it rests on, how fresh it is, and where to fix it. It shows the stored basis of
 * the row; it never recomputes.
 */
export function WhyPanel({ why, onClose }: { why: Why | null; onClose: () => void }) {
  const titleId = useId()
  useEffect(() => {
    if (!why) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [why, onClose])
  if (!why) return null
  return (
    <>
      <div className="overlay" onClick={onClose} />
      <aside className="ml-why" role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <div className="ml-why-head">
          <h2 className="card-title" id={titleId}>
            How this was worked out
          </h2>
          <button className="btn btn-ghost btn-icon" aria-label="Close" onClick={onClose} autoFocus>
            <Icon name="close" size={16} />
          </button>
        </div>
        <div className="ml-why-body">
          <div className="ml-why-result">{why.result}</div>
          <dl className="ml-why-list">
            <dt>Formula</dt>
            <dd>{why.formula}</dd>
            <dt>Inputs</dt>
            <dd>
              <table className="table small">
                <tbody>
                  {why.inputs.map((input) => (
                    <tr key={input.label}>
                      <td>{input.label}</td>
                      <td className="num strong">{input.value}</td>
                      <td>{input.source && <a href={input.source.href}>{input.source.label}</a>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </dd>
            <dt>Data basis</dt>
            <dd>{why.dataBasis}</dd>
            <dt>Freshness</dt>
            <dd>{why.freshness}</dd>
          </dl>
          {why.fix && (
            <a className="btn btn-secondary" href={why.fix.href}>
              {why.fix.label}
            </a>
          )}
        </div>
      </aside>
    </>
  )
}

/** Undo, offered for ten seconds after approve, dismiss or publish. */
export function useUndo() {
  const [pending, setPending] = useState<{ label: string; undo: () => void } | null>(null)
  useEffect(() => {
    if (!pending) return
    const timer = window.setTimeout(() => setPending(null), 10_000)
    return () => window.clearTimeout(timer)
  }, [pending])
  const bar = pending ? (
    <div className="ml-undo" role="status">
      <span>{pending.label}</span>
      <button
        className="btn btn-secondary btn-sm"
        onClick={() => {
          pending.undo()
          setPending(null)
        }}
      >
        Undo
      </button>
    </div>
  ) : null
  return { offer: (label: string, undo: () => void) => setPending({ label, undo }), bar }
}

/** A finding and its one recommended action. Approved or dismissed in place, with the list still in view. */
export function ProposalCard({ title, sentence, impact, note, primary, onDismiss, onWhy, busy }: {
  title: string
  sentence: string
  /** Dollars a week. */
  impact: number | string
  note?: string
  primary: { label: string; onClick: () => void }
  onDismiss?: () => void
  onWhy?: () => void
  busy?: boolean
}) {
  return (
    <article className="card ml-proposal" aria-label={title}>
      <div className="ml-proposal-main">
        <h3 className="ml-proposal-title">{title}</h3>
        <p className="ml-proposal-sentence">{sentence}</p>
        {note && <p className="muted small">{note}</p>}
      </div>
      <div className="ml-proposal-side">
        <div className="ml-proposal-impact">
          {dollars(impact)} <span className="muted small">a week</span>
        </div>
        <div className="row">
          <button className="btn btn-primary" disabled={busy} onClick={primary.onClick}>
            {primary.label}
          </button>
          {onDismiss && (
            <button className="btn btn-secondary" disabled={busy} onClick={onDismiss}>
              Dismiss
            </button>
          )}
          {onWhy && (
            <button className="btn btn-ghost" onClick={onWhy}>
              Why
            </button>
          )}
        </div>
      </div>
    </article>
  )
}

/** A suggestion during service: one sentence, the saving, two buttons, and when it expires. Built for one hand. */
export function SuggestionCard({ sentence, saving, reason, expires, onAccept, onDismiss, onWhy, busy }: {
  sentence: string
  saving: number | string
  reason?: string
  /** "7:35 pm" */
  expires: string
  onAccept: () => void
  onDismiss: () => void
  onWhy?: () => void
  busy?: boolean
}) {
  return (
    <article className="card ml-suggestion" aria-label="Suggestion">
      <p className="ml-suggestion-sentence">{sentence}</p>
      <p className="ml-suggestion-detail">
        Saves about {dollars(saving)}.{reason ? ` ${reason}` : ''}
        <span className="ml-suggestion-expires">Expires {expires}</span>
      </p>
      <div className="ml-suggestion-actions">
        <button className="btn btn-primary" disabled={busy} onClick={onAccept}>
          Accept
        </button>
        <button className="btn btn-secondary" disabled={busy} onClick={onDismiss}>
          Dismiss
        </button>
        {onWhy && (
          <button className="btn btn-ghost" onClick={onWhy}>
            Why
          </button>
        )}
      </div>
    </article>
  )
}

/**
 * One chart, and the same figures as a table for anyone who wants them or can't read the chart. The title
 * states the finding; the summary is one sentence a screen reader says in place of the picture.
 */
export function ViewAsTable({ title, summary, chart, columns, rows }: { title: string; summary: string; chart: ReactNode; columns: string[]; rows: (string | number)[][] }) {
  const [asTable, setAsTable] = useState(false)
  return (
    <section className="card ml-figure" data-testid="primary-chart">
      <div className="card-header">
        <h2 className="card-title">{title}</h2>
        <button className="btn btn-ghost btn-sm" aria-pressed={asTable} onClick={() => setAsTable(!asTable)}>
          {asTable ? 'View as chart' : 'View as table'}
        </button>
      </div>
      <p className="visually-hidden">{summary}</p>
      {asTable ? (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                {columns.map((c, i) => (
                  <th key={c} className={i ? 'num' : ''}>
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i}>
                  {row.map((cell, j) => (
                    <td key={j} className={j ? 'num' : ''}>
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="card-body" aria-hidden="true">
          {chart}
        </div>
      )}
    </section>
  )
}

/** Ten rows, then "Show all". Rows under the materiality threshold are one line, not many. */
export function useRowLimit<T>(rows: T[], limit = 10) {
  const [all, setAll] = useState(false)
  const shown = all ? rows : rows.slice(0, limit)
  const more = rows.length > limit ? (
    <button className="btn btn-ghost btn-sm" onClick={() => setAll(!all)}>
      {all ? 'Show fewer' : `Show all ${rows.length}`}
    </button>
  ) : null
  return { shown, more }
}
