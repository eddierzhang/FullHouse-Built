import { type FormEvent, useEffect, useRef, useState } from 'react'
import {
  type CustomerActionItem,
  type CustomerRequest,
  type CustomerServiceReview,
  type NewCustomerRequest,
  REQUEST_CHANNELS,
  REQUEST_KINDS,
  type RequestChannel,
  type RequestKind,
  getRun,
  listCustomerRequests,
  listCustomerReviews,
  logCustomerRequest,
  reopenCustomerRequest,
  resolveCustomerRequest,
  setCustomerActionItemDone,
  startCustomerReview,
} from '../api/client'
import type { AppContext } from '../app/context'
import { Icon } from '../components/Icon'
import { Empty, Freshness, Loaded } from '../components/PageState'
import { dateTime, relativeTime } from '../lib/format'
import { useLoad } from '../lib/useLoad'

const CHANNEL_LABEL: Record<RequestChannel, string> = {
  in_person: 'In person',
  phone: 'Phone',
  email: 'Email',
  online_review: 'Online review',
  social_media: 'Social media',
  other: 'Other',
}
const KIND_LABEL: Record<RequestKind, string> = {
  complaint: 'Complaint',
  question: 'Question',
  request: 'Request',
  compliment: 'Compliment',
  other: 'Other',
}
const KIND_BADGE: Record<RequestKind, string> = {
  complaint: 'badge-danger',
  question: 'badge-info',
  request: 'badge-warning',
  compliment: 'badge-success',
  other: '',
}
const PRIORITY_BADGE = { high: 'badge-danger', normal: '', low: '' } as const

const BLANK: NewCustomerRequest = { message: '', channel: 'in_person', kind: 'complaint', customer_name: '', customer_contact: '' }
/** How often to check on a summary being written. */
const REVIEW_POLL_MS = 1500

const who = (request: CustomerRequest) => request.customer_name ?? 'A customer'
const message = (err: unknown) => (err instanceof Error ? err.message : String(err))

/** Write down what a customer said. The whole team can; it's saved exactly as typed. */
function LogRequestForm({ onLogged, onCancel }: { onLogged: () => void; onCancel?: () => void }) {
  const [values, setValues] = useState<NewCustomerRequest>(BLANK)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const set = <K extends keyof NewCustomerRequest>(key: K, value: NewCustomerRequest[K]) => setValues((v) => ({ ...v, [key]: value }))

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await logCustomerRequest(values)
      setValues(BLANK)
      onLogged()
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="card-body stack-sm" onSubmit={submit} aria-label="Log a customer request">
      <label className="field">
        <span className="field-label">What the customer said</span>
        <textarea
          className="textarea"
          rows={4}
          required
          maxLength={10000}
          value={values.message}
          onChange={(e) => set('message', e.target.value)}
        />
        <span className="field-hint">In their words, as near as you can. It’s kept exactly as you write it.</span>
      </label>
      <div className="form-grid">
        <label className="field">
          <span className="field-label">Type</span>
          <select className="select" value={values.kind} onChange={(e) => set('kind', e.target.value as RequestKind)}>
            {REQUEST_KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {KIND_LABEL[kind]}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">How it reached you</span>
          <select className="select" value={values.channel} onChange={(e) => set('channel', e.target.value as RequestChannel)}>
            {REQUEST_CHANNELS.map((channel) => (
              <option key={channel} value={channel}>
                {CHANNEL_LABEL[channel]}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Customer’s name (optional)</span>
          <input className="input" maxLength={120} value={values.customer_name} onChange={(e) => set('customer_name', e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Phone or email (optional)</span>
          <input
            className="input"
            maxLength={254}
            value={values.customer_contact}
            onChange={(e) => set('customer_contact', e.target.value)}
          />
        </label>
      </div>
      {error && (
        <div className="auth-error" role="alert">
          {error}
        </div>
      )}
      <div className="row">
        <button className="btn btn-primary" type="submit" disabled={busy || !values.message.trim()}>
          {busy ? 'Saving…' : 'Save request'}
        </button>
        {onCancel && (
          <button className="btn btn-ghost" type="button" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </form>
  )
}

/** One request: the customer's words as taken down, and what was done about it beside them. */
function RequestItem({
  request,
  highlighted,
  onChanged,
  toast,
}: {
  request: CustomerRequest
  highlighted: boolean
  onChanged: () => Promise<void>
  toast: AppContext['toast']
}) {
  const [resolving, setResolving] = useState(false)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const act = async (action: () => Promise<unknown>, done: string) => {
    setBusy(true)
    setError(null)
    try {
      await action()
      await onChanged()
      setResolving(false)
      setNote('')
      toast(done, who(request))
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className={`request${highlighted ? ' request-highlight' : ''}`} id={`request-${request.id}`}>
      <div className="request-head">
        <span className={`badge ${KIND_BADGE[request.kind]}`}>{KIND_LABEL[request.kind]}</span>
        <strong>{who(request)}</strong>
        <span className="muted small" title={dateTime(request.received_at)}>
          {CHANNEL_LABEL[request.channel]} · {relativeTime(request.received_at)}
        </span>
        {request.status === 'resolved' && <span className="badge badge-success">Resolved</span>}
      </div>

      <blockquote className="request-message" aria-label={`What ${who(request)} said`}>
        {request.message}
      </blockquote>

      <div className="faint small">
        {request.customer_contact && <>Contact: {request.customer_contact} · </>}
        Written down by {request.logged_by} on {dateTime(request.created_at)}
      </div>

      {request.status === 'resolved' && (
        <div className="request-resolution small">
          <strong>What was done:</strong> {request.resolution_note ?? 'No note was left.'}
          <div className="faint">
            {request.resolved_by}
            {request.resolved_at && ` · ${dateTime(request.resolved_at)}`}
          </div>
        </div>
      )}

      {error && (
        <div className="auth-error" role="alert">
          {error}
        </div>
      )}

      {request.status === 'resolved' ? (
        <div>
          <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => act(() => reopenCustomerRequest(request.id), 'Request reopened')}>
            Reopen
          </button>
        </div>
      ) : resolving ? (
        <form
          className="stack-sm"
          aria-label={`Resolve the request from ${who(request)}`}
          onSubmit={(event) => {
            event.preventDefault()
            void act(() => resolveCustomerRequest(request.id, note), 'Request resolved')
          }}
        >
          <label className="field">
            <span className="field-label">What was done about it (optional)</span>
            <textarea className="textarea" rows={2} maxLength={2000} autoFocus value={note} onChange={(e) => setNote(e.target.value)} />
            <span className="field-hint">Kept beside the customer’s words, which stay as they are.</span>
          </label>
          <div className="row">
            <button className="btn btn-primary btn-sm" type="submit" disabled={busy}>
              {busy ? 'Saving…' : 'Mark resolved'}
            </button>
            <button className="btn btn-ghost btn-sm" type="button" onClick={() => setResolving(false)}>
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <div>
          <button className="btn btn-secondary btn-sm" onClick={() => setResolving(true)}>
            Resolve
          </button>
        </div>
      )}
    </li>
  )
}

/** The Assistant Manager's summary, and the action items it drew from the requests. */
function Review({
  review,
  requests,
  onOpenRequest,
  onChanged,
}: {
  review: CustomerServiceReview
  requests: Map<string, CustomerRequest>
  onOpenRequest: (id: string) => void
  onChanged: () => Promise<void>
}) {
  // Ticks being saved: shown at once, and taken back if the save fails.
  const [saving, setSaving] = useState<Record<string, boolean>>({})
  const [error, setError] = useState<string | null>(null)
  const isDone = (item: CustomerActionItem) => saving[item.id] ?? item.status === 'done'

  const toggle = async (item: CustomerActionItem) => {
    const done = !isDone(item)
    setSaving((s) => ({ ...s, [item.id]: done }))
    setError(null)
    try {
      await setCustomerActionItemDone(item.id, done)
      await onChanged()
    } catch (err) {
      setError(message(err))
    } finally {
      setSaving(({ [item.id]: _, ...rest }) => rest)
    }
  }

  const sources = (ids: string[]) =>
    ids.map((id) => {
      const request = requests.get(id)
      return request ? (
        <button key={id} type="button" className="link-button small" onClick={() => onOpenRequest(id)}>
          {who(request)}, {relativeTime(request.received_at)}
        </button>
      ) : null
    })

  return (
    <div className="card-body stack-sm">
      <p className="review-summary">{review.summary || 'No summary was written for this review.'}</p>
      <div className="faint small">
        Written by the Assistant Manager on {dateTime(review.created_at)}, from {review.request_ids.length} request
        {review.request_ids.length === 1 ? '' : 's'}. It’s a reading of them, not a replacement: the originals are below.
      </div>

      {error && (
        <div className="auth-error" role="alert">
          {error}
        </div>
      )}

      {review.action_items.length === 0 ? (
        <div className="muted small">It suggested nothing to do.</div>
      ) : (
        <ul className="action-items" aria-label="Action items">
          {review.action_items.map((item) => (
            <li key={item.id} className={`action-item${isDone(item) ? ' action-item-done' : ''}`}>
              <input
                type="checkbox"
                id={`action-${item.id}`}
                checked={isDone(item)}
                disabled={item.id in saving}
                onChange={() => void toggle(item)}
              />
              <div className="action-item-main">
                <label htmlFor={`action-${item.id}`} className="action-item-title">
                  {item.title}
                </label>
                {item.priority !== 'normal' && (
                  <span className={`badge ${PRIORITY_BADGE[item.priority]}`}>{item.priority === 'high' ? 'High priority' : 'Low priority'}</span>
                )}
                {item.details && <div className="small muted">{item.details}</div>}
                <div className="action-item-sources small faint">
                  From: {sources(item.request_ids)}
                  {item.status === 'done' && item.done_by && <span>· Done by {item.done_by}</span>}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

type Filter = 'open' | 'resolved' | 'all'

export function CustomersPage({ app }: { app: AppContext }) {
  if (!app.canManage) return <StaffView app={app} />
  return <ManagerView app={app} />
}

/** Staff write requests down; reading them back, with customers' details, is for owners and admins. */
function StaffView({ app }: { app: AppContext }) {
  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Customer requests</h1>
          <p className="page-subtitle">
            When a customer complains, asks for something or says something kind, write it down here. The owner and admins
            read every one and decide what to do.
          </p>
        </div>
      </div>
      <section className="card">
        <LogRequestForm onLogged={() => app.toast('Request saved', 'The owner and admins will see it.')} />
      </section>
    </main>
  )
}

function ManagerView({ app }: { app: AppContext }) {
  const requestsLoad = useLoad(listCustomerRequests, [])
  const reviewsLoad = useLoad(listCustomerReviews, [])
  const [filter, setFilter] = useState<Filter>('open')
  const [logging, setLogging] = useState(false)
  const [highlight, setHighlight] = useState<string | null>(null)
  /** The run writing a summary right now, if one is. */
  const [reviewRun, setReviewRun] = useState<string | null>(null)
  const [reviewError, setReviewError] = useState<string | null>(null)
  const reloadReviews = useRef(reviewsLoad.reload)
  reloadReviews.current = reviewsLoad.reload

  const requests = requestsLoad.data ?? []
  const byId = new Map(requests.map((r) => [r.id, r]))
  const open = requests.filter((r) => r.status === 'open')
  const visible = filter === 'all' ? requests : requests.filter((r) => r.status === filter)

  // Follow the run until it ends, then show what it wrote.
  useEffect(() => {
    if (!reviewRun) return
    let stopped = false
    const check = async () => {
      try {
        const run = await getRun(reviewRun)
        if (stopped || run.status === 'queued' || run.status === 'running') return
        setReviewRun(null)
        if (run.status === 'succeeded') {
          await reloadReviews.current()
          app.toast('Summary ready', 'The Assistant Manager has read the open requests.')
        } else {
          setReviewError(run.error ?? `The summary didn’t finish: the run was ${run.status}.`)
        }
      } catch {
        // A dropped connection: keep waiting, the next check may get through.
      }
    }
    const timer = window.setInterval(() => void check(), REVIEW_POLL_MS)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reviewRun])

  const summarise = async () => {
    setReviewError(null)
    try {
      setReviewRun((await startCustomerReview()).id)
    } catch (err) {
      setReviewError(message(err))
    }
  }

  const openRequest = (id: string) => {
    const request = byId.get(id)
    if (request && filter !== 'all' && request.status !== filter) setFilter('all')
    setHighlight(id)
    // After the list has re-rendered with it in view.
    window.setTimeout(() => document.getElementById(`request-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 50)
  }

  const [latest, ...earlier] = reviewsLoad.data ?? []

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Customer requests</h1>
          <p className="page-subtitle">
            What customers have told you, each kept in its original words. The Assistant Manager can read the open ones,
            summarise them and suggest what to do.
          </p>
        </div>
        <div className="row wrap">
          <Freshness loads={[requestsLoad, reviewsLoad]} />
          <button className="btn btn-secondary" onClick={() => setLogging((v) => !v)} aria-expanded={logging}>
            <Icon name="plus" size={16} /> Log a request
          </button>
          <button className="btn btn-primary" disabled={reviewRun !== null || open.length === 0} onClick={() => void summarise()}>
            {reviewRun ? 'Summarising…' : 'Summarise open requests'}
          </button>
        </div>
      </div>

      <div className="stack">
        {logging && (
          <section className="card" aria-labelledby="log-request-title">
            <div className="card-header">
              <h2 className="card-title" id="log-request-title">
                Log a request
              </h2>
            </div>
            <LogRequestForm
              onCancel={() => setLogging(false)}
              onLogged={() => {
                setLogging(false)
                setFilter('open')
                void requestsLoad.reload()
                app.toast('Request saved')
              }}
            />
          </section>
        )}

        {reviewRun && (
          <div className="callout callout-info" role="status">
            <Icon name="agents" />
            <div className="callout-grow">
              <div className="callout-title">The Assistant Manager is reading {open.length} open request{open.length === 1 ? '' : 's'}</div>
              <div className="callout-body">
                Its summary and action items will appear here when it’s done. You can{' '}
                <a href="#runs">watch it work in Agent activity</a>.
              </div>
            </div>
          </div>
        )}
        {reviewError && (
          <div className="callout callout-danger" role="alert">
            <Icon name="alert" />
            <div className="callout-grow">
              <div className="callout-title">Couldn’t summarise the requests</div>
              <div className="callout-body">{reviewError}</div>
            </div>
          </div>
        )}

        <section className="card" aria-labelledby="review-title">
          <div className="card-header">
            <div>
              <h2 className="card-title" id="review-title">
                Summary and action items
              </h2>
              <div className="card-subtitle">The Assistant Manager’s reading of the open requests, and what it suggests doing.</div>
            </div>
          </div>
          <Loaded load={reviewsLoad} what="summaries">
            {() =>
              latest ? (
                <>
                  <Review review={latest} requests={byId} onOpenRequest={openRequest} onChanged={reviewsLoad.reload} />
                  {earlier.length > 0 && (
                    <details className="review-earlier">
                      <summary>Earlier summaries ({earlier.length})</summary>
                      {earlier.map((review) => (
                        <Review key={review.id} review={review} requests={byId} onOpenRequest={openRequest} onChanged={reviewsLoad.reload} />
                      ))}
                    </details>
                  )}
                </>
              ) : (
                <Empty title="No summary yet">
                  {open.length === 0
                    ? 'Once there are open requests, the Assistant Manager can summarise them here.'
                    : 'Choose “Summarise open requests” and the Assistant Manager will read them and list what to do.'}
                </Empty>
              )
            }
          </Loaded>
        </section>

        <section className="card" aria-labelledby="requests-title">
          <div className="card-header">
            <div>
              <h2 className="card-title" id="requests-title">
                Requests
              </h2>
              <div className="card-subtitle">Exactly as each was written down. Resolving one adds a note; it never changes the original.</div>
            </div>
            <div className="segmented" role="group" aria-label="Filter requests">
              {(['open', 'resolved', 'all'] as const).map((value) => (
                <button key={value} aria-pressed={filter === value} onClick={() => setFilter(value)}>
                  {value === 'open' ? `Open · ${open.length}` : value === 'resolved' ? `Resolved · ${requests.length - open.length}` : 'All'}
                </button>
              ))}
            </div>
          </div>
          <Loaded load={requestsLoad} what="customer requests" placeholderHeight={200}>
            {() =>
              visible.length === 0 ? (
                <Empty title={filter === 'open' ? 'Nothing open' : 'Nothing here yet'}>
                  {requests.length === 0
                    ? 'When a customer complains, asks for something or pays a compliment, log it and it will be kept here.'
                    : filter === 'open'
                      ? 'Every request has been dealt with.'
                      : 'No requests match this filter.'}
                </Empty>
              ) : (
                <ul className="requests" aria-label="Customer requests">
                  {visible.map((request) => (
                    <RequestItem
                      key={request.id}
                      request={request}
                      highlighted={highlight === request.id}
                      onChanged={requestsLoad.reload}
                      toast={app.toast}
                    />
                  ))}
                </ul>
              )
            }
          </Loaded>
        </section>
      </div>
    </main>
  )
}
