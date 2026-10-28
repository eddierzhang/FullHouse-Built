import { type FormEvent, type ReactNode, useEffect, useRef, useState } from 'react'
import {
  type AgentQuestion,
  type CropLine,
  type DigestCard,
  type MessageSource,
  type QuestionAnswer,
  askAgent,
} from '../api/client'
import { shortDate } from '../lib/format'
import { Icon } from './Icon'

/** The pieces every prices screen is built from: money, the agent's words and
 * their sources, its question cards, and the one field that reaches it. */

const usdFormat = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 })

/** Dollars with cents, in tabular figures. */
export const usd = (value: number) => usdFormat.format(value)

export function Money({ value, className = '', signed = false }: { value: number; className?: string; signed?: boolean }) {
  const text = signed && value > 0 ? `+${usd(value)}` : usd(value)
  return <span className={`pi-money ${className}`.trim()}>{text}</span>
}

export const PLACEHOLDER = 'Ask about prices, invoices, or suppliers'

/** Where a source chip leads: the item, or the invoice's item lines. */
export const sourceHref = (source: MessageSource) =>
  source.kind === 'item' ? `#item/${source.id}` : source.kind === 'digest' ? '#digest' : `#invoices/${source.id}`

export function SourceChips({ sources }: { sources: MessageSource[] }) {
  if (sources.length === 0) return null
  return (
    <div className="pi-chips" aria-label="Sources">
      {sources.map((source, i) => (
        <a key={`${source.kind}-${source.id}-${i}`} className="pi-chip" href={sourceHref(source)}>
          {source.label}
        </a>
      ))}
    </div>
  )
}

/** The agent's words: a thin rule on the left, sources at the end. */
export function AgentMessage({ children, sources, pending = false, label }: { children: ReactNode; sources?: MessageSource[]; pending?: boolean; label?: string }) {
  return (
    <div className={`pi-agent${pending ? ' pi-pending' : ''}`}>
      {label && <p className="pi-label">{label}</p>}
      {children}
      {sources && <SourceChips sources={sources} />}
    </div>
  )
}

export function Crop({ lines, label }: { lines: CropLine[]; label: string }) {
  return (
    <div className="pi-crop" role="img" aria-label={label}>
      {lines.map((line, i) => (
        <div key={i} className={line.hit ? 'pi-crop-hit' : 'pi-crop-dim'}>
          {line.text}
        </div>
      ))}
    </div>
  )
}

/** Fired by "Ask about this item": the pinned field picks the item up as context and takes focus. */
export const ASK_CONTEXT_EVENT = 'fullhouse:ask-context'
export type AskContext = { id: string; name: string }

export function askAboutItem(item: AskContext) {
  window.dispatchEvent(new CustomEvent<AskContext>(ASK_CONTEXT_EVENT, { detail: item }))
}

/**
 * The only way to the agent: a text field. Sending posts to the thread and
 * goes there. An item set as context scopes the question; it's shown as a
 * chip the person can take off.
 */
export function AskField({ id, className = '', onError }: { id: string; className?: string; onError: (message: string) => void }) {
  const [text, setText] = useState('')
  const [context, setContext] = useState<AskContext | null>(null)
  const [busy, setBusy] = useState(false)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const onContext = (event: Event) => {
      setContext((event as CustomEvent<AskContext>).detail)
      input.current?.focus()
    }
    window.addEventListener(ASK_CONTEXT_EVENT, onContext)
    return () => window.removeEventListener(ASK_CONTEXT_EVENT, onContext)
  }, [])

  // Context belongs to the item page it came from.
  useEffect(() => {
    const onChange = () => {
      if (!window.location.hash.startsWith('#item/')) setContext(null)
    }
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const question = text.trim()
    if (!question || busy) return
    setBusy(true)
    try {
      await askAgent(question, context?.id)
      setText('')
      setContext(null)
      window.location.hash = '#thread'
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className={`pi-ask-form ${className}`.trim()} onSubmit={submit}>
      {context && (
        <div className="pi-ask-context">
          About
          <span className="pi-chip">
            {context.name}
            <button type="button" className="pi-link pi-link-inline" aria-label={`Stop asking about ${context.name}`} style={{ marginLeft: 6, textDecoration: 'none' }} onClick={() => setContext(null)}>
              <Icon name="close" size={14} />
            </button>
          </span>
        </div>
      )}
      <label className="visually-hidden" htmlFor={id}>
        Ask the agent
      </label>
      <input
        ref={input}
        id={id}
        className="pi-ask"
        type="text"
        value={text}
        placeholder={PLACEHOLDER}
        disabled={busy}
        onChange={(e) => setText(e.target.value)}
        enterKeyHint="send"
      />
    </form>
  )
}

/** "Sysco Foods, Oct 21. $312.00 on this invoice" */
export const questionLabel = (q: AgentQuestion) => (
  <>
    {q.supplier.name}, {shortDate(q.invoice_date)}. <Money value={q.dollar_impact} /> on this invoice
  </>
)

/**
 * One of the agent's questions: the invoice crop, the question, two tap
 * answers and "Something else". Answering fades the card out; the parent
 * removes it once `onAnswered` resolves.
 */
export function QuestionCard({
  question,
  onAnswer,
  compact = false,
  pressOption = null,
  onPressed,
}: {
  question: AgentQuestion
  onAnswer: (body: QuestionAnswer) => Promise<void>
  compact?: boolean
  /** An option the keyboard chose (index into `options`), answered as if tapped. */
  pressOption?: number | null
  onPressed?: () => void
}) {
  const [other, setOther] = useState(false)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const answer = async (body: QuestionAnswer) => {
    setBusy(true)
    setError(null)
    try {
      await onAnswer(body)
      setDone(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    if (pressOption === null || pressOption === undefined || busy || done) return
    const option = question.options[pressOption]
    onPressed?.()
    if (option) void answer({ answer: option.key as QuestionAnswer['answer'] })
  }, [pressOption]) // the card answers once per press; `answer` and `question` are stable for its life

  const submitOther = (event: FormEvent) => {
    event.preventDefault()
    const value = text.trim()
    if (!value) return
    if (question.kind === 'pack') {
      const n = Number(value)
      if (!Number.isFinite(n) || n <= 0) {
        setError('Enter how many come in one pack, as a number.')
        return
      }
      void answer({ answer: 'other', pack_size: n })
    } else {
      void answer({ answer: 'other', text: value })
    }
  }

  const [first, second] = question.options
  return (
    <article className={`pi-q${done ? ' pi-answered' : ''}`} aria-label={question.text}>
      {!compact && <Crop lines={question.crop} label={`Invoice crop, ${question.supplier.name}, ${shortDate(question.invoice_date)}`} />}
      <div className="pi-q-body">
        <p className="pi-label">{questionLabel(question)}</p>
        <p>{question.text}</p>
        <div className="pi-answers">
          {first && (
            <button type="button" className="pi-btn-o" disabled={busy || done} onClick={() => void answer({ answer: first.key as QuestionAnswer['answer'] })}>
              {first.label}
            </button>
          )}
          {second && (
            <button type="button" className="pi-btn-o" disabled={busy || done} onClick={() => void answer({ answer: second.key as QuestionAnswer['answer'] })}>
              {second.label}
            </button>
          )}
        </div>
        {!other ? (
          <button type="button" className="pi-link" style={{ alignSelf: 'flex-start' }} disabled={busy || done} onClick={() => setOther(true)}>
            Something else
          </button>
        ) : (
          <form className="pi-answers" onSubmit={submitOther}>
            <label className="visually-hidden" htmlFor={`other-${question.id}`}>
              {question.kind === 'pack' ? 'How many per pack' : 'What this item is'}
            </label>
            <input
              id={`other-${question.id}`}
              className="pi-input pi-input-sm"
              type={question.kind === 'pack' ? 'number' : 'text'}
              inputMode={question.kind === 'pack' ? 'decimal' : 'text'}
              min={question.kind === 'pack' ? 0.01 : undefined}
              step={question.kind === 'pack' ? 'any' : undefined}
              placeholder={question.kind === 'pack' ? 'How many per pack' : 'Name the item'}
              value={text}
              autoFocus
              onChange={(e) => setText(e.target.value)}
            />
            <button type="submit" className="pi-btn" disabled={busy || done}>
              Save
            </button>
          </form>
        )}
        {error && (
          <p className="pi-secondary" role="alert">
            {error}
          </p>
        )}
      </div>
    </article>
  )
}

/** The figure's colour follows the section: red-orange when it costs money, green when it's owed. */
export const sectionClass = (section: DigestCard['section']) => (section === 'up' ? 'pi-up' : section === 'owed' ? 'pi-owed' : '')

/**
 * A recommendation card: the item and the dollars, the comparison in plain
 * words, one primary action and "Not useful". The card itself opens the item.
 */
export function DigestCardView({
  card,
  onAction,
  onHide,
  busy = false,
}: {
  card: DigestCard
  onAction: (card: DigestCard) => void
  onHide: (card: DigestCard) => void
  busy?: boolean
}) {
  return (
    <article className="pi-card" aria-label={card.item_name}>
      <a className="pi-card-link" href={card.recipe_id ? `#recipe/${card.recipe_id}` : card.calendar_item_id ? '#calendar' : `#item/${card.item_id}`}>
        <div className="pi-card-l1">
          <span>{card.item_name}</span>
          <Money value={card.impact} className={sectionClass(card.section)} />
        </div>
        <p className="pi-card-l2">{card.sentence}</p>
      </a>
      <div className="pi-actions">
        <button type="button" className="pi-btn" disabled={busy} onClick={() => onAction(card)}>
          {card.action.label}
        </button>
        <button type="button" className="pi-link" disabled={busy} onClick={() => onHide(card)}>
          Not useful
        </button>
      </div>
    </article>
  )
}

/** "Mon, Oct 27, 9:12 AM" for thread messages. */
export const messageTime = (iso: string) =>
  new Date(iso).toLocaleString('en-US', { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
