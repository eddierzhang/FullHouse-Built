import { type ChangeEvent, type FormEvent, useState } from 'react'
import {
  type Connection,
  type CsvPlan,
  type Provider,
  type User,
  csvImport,
  csvPreview,
  disconnect,
  fetchConnect,
  oauthStart,
  setInbound,
  syncNow,
} from '../api/client'
import type { AppContext } from '../app/context'
import { Icon } from '../components/Icon'
import { Empty, Loaded } from '../components/PageState'
import { relativeTime } from '../lib/format'
import { useLoad } from '../lib/useLoad'

const KIND_LABELS: Record<Provider['kind'], string> = { pos: 'Point of sale', payroll: 'Payroll', accounting: 'Accounting', ap: 'Bills', email: 'Email' }
const FIELD_LABELS: Record<string, string> = {
  date: 'Date', net_sales: 'Net sales', gross_sales: 'Gross sales', tax: 'Tax', tips: 'Tips', covers: 'Covers',
  pos_item_name: 'Item', qty: 'Quantity', net: 'Net amount', pos_item_id: 'Item id', hours: 'Hours', dollars: 'Labor dollars',
}
const CAPABILITY_LABELS: Record<string, string> = { sales_days: 'sales by day', sales_items: 'sales by item', labor_days: 'labor by day' }

/**
 * Connected systems: what is on offer, what is connected and when it last synced, a CSV drop
 * zone with a mapping preview, and the restaurant's inbound address with its allow-list.
 * No sync logs: one line per connection says what is wrong, when something is.
 */
export function ConnectPage({ app, user }: { app: AppContext; user: User }) {
  const load = useLoad(fetchConnect, [])
  const isOwner = user.role === 'owner'
  const [busy, setBusy] = useState<string | null>(null)

  const act = async (key: string, work: () => Promise<unknown>, done: string) => {
    setBusy(key)
    try {
      await work()
      app.toast(done)
      await load.reload()
    } catch (err) {
      app.toast('Couldn’t do that', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  const connect = async (provider: string) => {
    setBusy(provider)
    try {
      const { url } = await oauthStart(provider)
      window.location.href = url
    } catch (err) {
      app.toast('Couldn’t start that', err instanceof Error ? err.message : String(err))
      setBusy(null)
    }
  }

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Connections</h1>
          <p className="page-subtitle">Your point of sale, payroll and accounting, and invoices by email. Read-only, except the bills you push.</p>
        </div>
      </div>
      <Loaded load={load} what="connections" placeholderHeight={200}>
        {(data) => {
          const byProvider = new Map(data.connections.filter((c) => c.status !== 'revoked').map((c) => [c.provider, c]))
          return (
            <div className="stack">
              <section className="card" aria-labelledby="connected-title">
                <div className="card-header">
                  <div>
                    <h2 className="card-title" id="connected-title">
                      Systems
                    </h2>
                    <div className="card-subtitle">Owners connect and disconnect. Anyone can sync now.</div>
                  </div>
                </div>
                <ul className="list" aria-label="Providers">
                  {data.providers
                    .filter((p) => p.provider !== 'csv' && p.provider !== 'resend_inbound')
                    .map((p) => {
                      const c = byProvider.get(p.provider)
                      return (
                        <li key={p.provider} className="list-item">
                          <div className="list-item-main">
                            <div className="row wrap">
                              <span className="strong">{p.label}</span>
                              <span className="muted small">{KIND_LABELS[p.kind]}</span>
                              {c && <ConnectionBadge connection={c} />}
                            </div>
                            <div className="muted small">
                              {c
                                ? c.error
                                  ? c.error
                                  : c.last_sync_at
                                    ? `Last sync ${relativeTime(c.last_sync_at)}`
                                    : 'Connected, first sync queued'
                                : p.available
                                  ? (p.notes ?? 'Not connected.')
                                  : p.auth === 'partner'
                                    ? 'Needs the provider’s partner program; not available yet.'
                                    : 'Not available on this site: the people running it have not set it up.'}
                            </div>
                          </div>
                          <div className="row">
                            {c ? (
                              <>
                                {p.capabilities.some((cap) => cap !== 'push_invoices') && (
                                  <button className="btn btn-secondary btn-sm" disabled={busy === p.provider} onClick={() => void act(p.provider, () => syncNow(p.provider), `${p.label}: sync queued`)}>
                                    Sync now
                                  </button>
                                )}
                                {isOwner && (
                                  <button className="btn btn-ghost btn-sm" disabled={busy === p.provider} onClick={() => void act(p.provider, () => disconnect(c.id), `${p.label} disconnected`)}>
                                    Disconnect
                                  </button>
                                )}
                              </>
                            ) : p.available && p.auth === 'oauth' && isOwner ? (
                              <button className="btn btn-primary btn-sm" disabled={busy === p.provider} onClick={() => void connect(p.provider)}>
                                Connect
                              </button>
                            ) : p.available && p.provider === 'billcom' ? (
                              <button className="btn btn-secondary btn-sm" disabled={busy === p.provider} onClick={() => void act(p.provider, () => syncNow(p.provider), 'Bill.com: pull queued')}>
                                Pull now
                              </button>
                            ) : null}
                          </div>
                        </li>
                      )
                    })}
                </ul>
              </section>

              <CsvCard app={app} profiles={data.csv_profiles} onDone={() => void load.reload()} />

              <InboundCard app={app} inbound={data.inbound} canEdit={app.canManage} onDone={() => void load.reload()} />
            </div>
          )
        }}
      </Loaded>
    </main>
  )
}

function ConnectionBadge({ connection }: { connection: Connection }) {
  const cls = connection.status === 'error' ? 'badge-danger' : 'badge-success'
  return <span className={`badge ${cls}`}>{connection.status === 'error' ? 'Needs attention' : 'Connected'}</span>
}

/** A CSV from the POS or payroll: dropped in, read by its headers, previewed, then imported. */
function CsvCard({ app, profiles, onDone }: { app: AppContext; profiles: Record<string, unknown>[]; onDone: () => void }) {
  const [text, setText] = useState<string | null>(null)
  const [fileName, setFileName] = useState('')
  const [plan, setPlan] = useState<CsvPlan | null>(null)
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [capability, setCapability] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const preview = async (content: string, override?: { mapping?: Record<string, string>; capability?: string }) => {
    setBusy(true)
    setError(null)
    try {
      const next = await csvPreview({ text: content, mapping: override?.mapping ?? null, capability: override?.capability ?? null })
      setPlan(next)
      setMapping(next.mapping)
      setCapability(next.capability)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const onFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    if (/\.xlsx?$/i.test(file.name)) return setError('That’s an Excel file. In your spreadsheet choose File › Save As › CSV, then pick the .csv file.')
    if (file.size > 5_000_000) return setError('That file is over 5 MB. Export a shorter date range.')
    const content = await file.text()
    setFileName(file.name)
    setText(content)
    await preview(content)
  }

  const remap = async (field: string, header: string) => {
    if (!text) return
    const next = { ...mapping, [field]: header }
    setMapping(next)
    await preview(text, { mapping: next, capability })
  }

  const doImport = async (event: FormEvent) => {
    event.preventDefault()
    if (!text || !plan?.ok) return
    setBusy(true)
    try {
      const result = await csvImport({ text, mapping, capability })
      const w = result.written
      app.toast('Imported', `${w.sales_days ? `${w.sales_days} days of sales` : ''}${w.sales_items ? `, ${w.sales_items} item lines` : ''}${w.labor_days ? `${w.labor_days} days of labor` : ''}`.replace(/^, /, ''))
      setText(null)
      setPlan(null)
      onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card" aria-labelledby="csv-title">
      <div className="card-header">
        <div>
          <h2 className="card-title" id="csv-title">
            CSV exports
          </h2>
          <div className="card-subtitle">
            Sales by day or by item from Toast, Square or Clover, or payroll by day. The columns are matched from the headers; a file with a bad row is imported whole or not at all.
            {profiles.length > 0 && ' Your column mapping from last time is remembered.'}
          </div>
        </div>
        <label className="btn btn-secondary">
          <Icon name="plus" size={16} /> Choose a CSV
          <input type="file" accept=".csv,text/csv" onChange={(e) => void onFile(e)} style={{ display: 'none' }} aria-label="Sales or payroll CSV" />
        </label>
      </div>
      {error && (
        <div className="card-body">
          <div className="auth-error" role="alert">
            {error}
          </div>
        </div>
      )}
      {plan && text && (
        <form className="card-body stack-sm" onSubmit={doImport} aria-label="CSV preview">
          <div className="row wrap">
            <span className="strong">{fileName}</span>
            <span className="muted small">
              Read as {CAPABILITY_LABELS[plan.capability]} ({plan.format.replace('_', ' ')}): {plan.row_count} rows over {plan.days} {plan.days === 1 ? 'day' : 'days'}
              {plan.first_date && `, ${plan.first_date} to ${plan.last_date}`}
            </span>
          </div>
          <div className="row wrap">
            <label className="field" style={{ width: 200 }}>
              <span className="field-label">This file holds</span>
              <select className="input" value={capability} onChange={(e) => void preview(text, { capability: e.target.value, mapping: {} })}>
                {Object.entries(CAPABILITY_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
            {plan.fields.map((f) => (
              <label key={f.field} className="field" style={{ width: 180 }}>
                <span className="field-label">
                  {FIELD_LABELS[f.field] ?? f.field}
                  {f.required ? '' : ' (optional)'}
                </span>
                <select className="input" value={mapping[f.field] ?? ''} onChange={(e) => void remap(f.field, e.target.value)}>
                  <option value="">—</option>
                  {plan.headers.map((h) => (
                    <option key={h} value={h}>
                      {h}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </div>
          {plan.problems.length > 0 && (
            <ul className="list" aria-label="Problems">
              {plan.problems.slice(0, 8).map((p, i) => (
                <li key={i} className="negative small">
                  {p.line ? `Line ${p.line}: ` : ''}
                  {p.message}
                </li>
              ))}
              {plan.problems.length > 8 && <li className="muted small">and {plan.problems.length - 8} more</li>}
            </ul>
          )}
          {plan.preview.length > 0 && (
            <div className="table-wrap">
              <table className="table" aria-label="Preview">
                <thead>
                  <tr>
                    {Object.keys(plan.preview[0]).filter((k) => k !== 'source').map((k) => (
                      <th key={k}>{FIELD_LABELS[k] ?? k}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {plan.preview.map((row, i) => (
                    <tr key={i}>
                      {Object.entries(row).filter(([k]) => k !== 'source').map(([k, v]) => (
                        <td key={k}>{typeof v === 'number' ? (Number.isInteger(v) ? v : v.toFixed(2)) : String(v ?? '')}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="row">
            <button type="submit" className="btn btn-primary btn-sm" disabled={busy || !plan.ok}>
              Import {plan.row_count} rows
            </button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => (setText(null), setPlan(null))}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </section>
  )
}

/** The restaurant's inbound address, and who may send to it. */
function InboundCard({ app, inbound, canEdit, onDone }: { app: AppContext; inbound: { address: string | null; allowed_senders: string[]; last_refused_sender: string | null }; canEdit: boolean; onDone: () => void }) {
  const [senders, setSenders] = useState(inbound.allowed_senders.join('\n'))
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    if (!inbound.address) return
    try {
      await navigator.clipboard.writeText(inbound.address)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      app.toast('Couldn’t copy', inbound.address)
    }
  }

  const save = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    try {
      await setInbound(senders.split(/[\n,;]+/).map((s) => s.trim()).filter(Boolean))
      app.toast('Allowed senders saved')
      onDone()
    } catch (err) {
      app.toast('Couldn’t save', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card" aria-labelledby="inbound-title">
      <div className="card-header">
        <div>
          <h2 className="card-title" id="inbound-title">
            Invoices by email
          </h2>
          <div className="card-subtitle">Forward invoices here, or have suppliers send them straight in. Only senders on the list are read.</div>
        </div>
      </div>
      <div className="card-body stack-sm">
        {inbound.address ? (
          <div className="row wrap">
            <code className="strong" data-testid="inbound-address">{inbound.address}</code>
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => void copy()}>
              {copied ? 'Copied' : 'Copy'}
            </button>
          </div>
        ) : (
          <Empty title="No inbound address on this site">The people running the site set INBOUND_DOMAIN to turn this on.</Empty>
        )}
        {inbound.last_refused_sender && (
          <div className="callout callout-warning">
            <Icon name="alert" />
            <div className="callout-body">
              Mail from <strong>{inbound.last_refused_sender}</strong> arrived and was not read, because the sender isn’t on the list below.
            </div>
          </div>
        )}
        {canEdit ? (
          <form className="stack-sm" onSubmit={save} aria-label="Allowed senders">
            <label className="field">
              <span className="field-label">Allowed senders (one per line: an address, or @supplier.com for everyone there)</span>
              <textarea className="input" rows={4} value={senders} onChange={(e) => setSenders(e.target.value)} />
            </label>
            <div className="row">
              <button type="submit" className="btn btn-primary btn-sm" disabled={busy}>
                Save senders
              </button>
            </div>
          </form>
        ) : (
          <div className="muted small">Allowed senders: {inbound.allowed_senders.join(', ') || 'none yet'}</div>
        )}
      </div>
    </section>
  )
}
