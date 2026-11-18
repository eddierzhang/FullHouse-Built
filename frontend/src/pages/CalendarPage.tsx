import { type ChangeEvent, type FormEvent, useState } from 'react'
import { type CalendarItem, addAsset, addCalendarItem, attachProof, fetchCalendar, scanCalendar } from '../api/client'
import type { AppContext } from '../app/context'
import { Icon } from '../components/Icon'
import { Empty, Loaded } from '../components/PageState'
import { shortDate } from '../lib/format'
import { useLoad } from '../lib/useLoad'

const KIND_LABELS: Record<string, string> = {
  license: 'License', permit: 'Permit', inspection: 'Inspection', tax_filing: 'Tax filing', warranty: 'Warranty', service: 'Service',
}

/** One list by date: licenses, permits, inspections, warranties and service, each with proof. A list, not a grid. */
export function CalendarPage({ app }: { app: AppContext }) {
  const load = useLoad(fetchCalendar, [])
  const [busy, setBusy] = useState<string | null>(null)
  const [adding, setAdding] = useState<'item' | 'asset' | null>(null)

  const proofFor = async (item: CalendarItem, event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setBusy(item.id)
    try {
      const buffer = new Uint8Array(await file.arrayBuffer())
      let binary = ''
      for (let i = 0; i < buffer.length; i += 0x8000) binary += String.fromCharCode(...buffer.subarray(i, i + 0x8000))
      await attachProof(item.id, file.name, file.type || 'application/pdf', btoa(binary))
      app.toast('Proof attached', `${item.title} is done.`)
      await load.reload()
      app.refresh()
    } catch (err) {
      app.toast('Couldn’t attach that', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  const scan = async () => {
    setBusy('scan')
    try {
      const out = await scanCalendar()
      app.toast('Scanned', `${out.overdue} overdue, ${out.proposed} service ${out.proposed === 1 ? 'date' : 'dates'} proposed.`)
      await load.reload()
    } catch (err) {
      app.toast('Couldn’t scan', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Calendar</h1>
          <p className="page-subtitle">Licenses, permits, inspections, warranties and equipment service, by date. Attach the renewal or report to clear one.</p>
        </div>
        {app.canManage && (
          <>
            <button className="btn btn-secondary" disabled={busy === 'scan'} onClick={() => void scan()}>
              Scan now
            </button>
            <button className="btn btn-secondary" onClick={() => setAdding(adding === 'asset' ? null : 'asset')}>
              <Icon name="plus" size={16} /> Equipment
            </button>
            <button className="btn btn-primary" onClick={() => setAdding(adding === 'item' ? null : 'item')}>
              <Icon name="plus" size={16} /> Add a date
            </button>
          </>
        )}
      </div>
      <div className="stack">
        {adding === 'item' && <AddItemForm app={app} onDone={() => (setAdding(null), void load.reload())} />}
        {adding === 'asset' && <AddAssetForm app={app} onDone={() => (setAdding(null), void load.reload())} />}
        <Loaded load={load} what="the calendar" placeholderHeight={160}>
          {(data) => (
            <>
              <section className="card">
                {data.items.length === 0 ? (
                  <Empty title="Nothing on the calendar">Upload a permit, license, inspection report or warranty under Documents, or add a date here.</Empty>
                ) : (
                  <ul className="list" aria-label="Calendar">
                    {data.items.map((item) => {
                      const overdue = item.status === 'overdue'
                      const done = item.status === 'done'
                      return (
                        <li key={item.id} className="list-item" style={{ alignItems: 'flex-start' }} data-status={item.status}>
                          <div className="list-item-main">
                            <div className="row wrap">
                              <span className={`strong${overdue ? ' negative' : ''}`}>{item.title}</span>
                              <span className="badge badge-brand">{KIND_LABELS[item.kind] ?? item.kind}</span>
                              <span className={`badge ${overdue ? 'badge-danger' : done ? 'badge-success' : item.days_left <= 30 ? 'badge-warning' : ''}`.trim()}>
                                {done ? 'Done' : overdue ? `Overdue by ${-item.days_left} ${item.days_left === -1 ? 'day' : 'days'}` : item.days_left === 0 ? 'Due today' : `Due in ${item.days_left} days`}
                              </span>
                            </div>
                            <div className={`small ${overdue ? 'negative' : 'muted'}`} style={{ marginTop: 4 }}>
                              {shortDate(item.due_on)}
                              {item.recurrence && ` · repeats ${item.recurrence}`}
                              {item.note && ` · ${item.note}`}
                            </div>
                          </div>
                          {!done && (
                            <label className="btn btn-secondary btn-sm">
                              {busy === item.id ? 'Attaching…' : 'Attach proof'}
                              <input type="file" accept=".pdf,image/*" capture="environment" onChange={(e) => void proofFor(item, e)} style={{ display: 'none' }} aria-label={`Attach proof for ${item.title}`} />
                            </label>
                          )}
                        </li>
                      )
                    })}
                  </ul>
                )}
              </section>
              {data.assets.length > 0 && (
                <section className="card" aria-labelledby="assets-title">
                  <div className="card-header">
                    <h2 className="card-title" id="assets-title">
                      Equipment
                    </h2>
                  </div>
                  <ul className="list" aria-label="Equipment">
                    {data.assets.map((a) => (
                      <li key={a.id} className="list-item">
                        <div className="list-item-main">
                          <div className="strong">{a.name}</div>
                          <div className="muted small">
                            {a.vendor && `${a.vendor} · `}
                            {a.warranty_until && `warranty until ${shortDate(a.warranty_until)}`}
                            {a.service_interval_days && ` · service every ${a.service_interval_days} days`}
                            {a.last_service_on && `, last ${shortDate(a.last_service_on)}`}
                          </div>
                        </div>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </>
          )}
        </Loaded>
      </div>
    </main>
  )
}

function AddItemForm({ app, onDone }: { app: AppContext; onDone: () => void }) {
  const [kind, setKind] = useState('license')
  const [title, setTitle] = useState('')
  const [due, setDue] = useState('')
  const [recurrence, setRecurrence] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!title.trim() || !due) return app.toast('Give it a name and a date')
    setBusy(true)
    try {
      await addCalendarItem({ kind, title: title.trim(), due_on: due, recurrence: recurrence || null })
      app.toast('Added', title)
      onDone()
    } catch (err) {
      app.toast('Couldn’t add that', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <form className="card card-body stack-sm" onSubmit={submit} aria-label="Add a date">
      <div className="row wrap">
        <label className="field" style={{ width: 160 }}>
          <span className="field-label">Kind</span>
          <select className="input" value={kind} onChange={(e) => setKind(e.target.value)}>
            {Object.entries(KIND_LABELS).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <label className="field" style={{ flex: 1, minWidth: 200 }}>
          <span className="field-label">What</span>
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Liquor license" />
        </label>
        <label className="field" style={{ width: 170 }}>
          <span className="field-label">Due</span>
          <input className="input" type="date" value={due} onChange={(e) => setDue(e.target.value)} />
        </label>
        <label className="field" style={{ width: 140 }}>
          <span className="field-label">Repeats</span>
          <select className="input" value={recurrence} onChange={(e) => setRecurrence(e.target.value)}>
            <option value="">no</option>
            <option value="yearly">yearly</option>
            <option value="monthly">monthly</option>
            <option value="90 days">every 90 days</option>
          </select>
        </label>
      </div>
      <div className="row">
        <button type="submit" className="btn btn-primary btn-sm" disabled={busy}>
          Add
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  )
}

function AddAssetForm({ app, onDone }: { app: AppContext; onDone: () => void }) {
  const [name, setName] = useState('')
  const [vendor, setVendor] = useState('')
  const [warranty, setWarranty] = useState('')
  const [interval, setInterval] = useState('')
  const [last, setLast] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return app.toast('Name the equipment')
    setBusy(true)
    try {
      await addAsset({ name: name.trim(), vendor: vendor || null, warranty_until: warranty || null, service_interval_days: interval ? Number(interval) : null, last_service_on: last || null })
      app.toast('Added', name)
      onDone()
    } catch (err) {
      app.toast('Couldn’t add that', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <form className="card card-body stack-sm" onSubmit={submit} aria-label="Add equipment">
      <div className="row wrap">
        <label className="field" style={{ flex: 1, minWidth: 200 }}>
          <span className="field-label">Equipment</span>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Walk-in compressor" />
        </label>
        <label className="field" style={{ width: 180 }}>
          <span className="field-label">Vendor</span>
          <input className="input" value={vendor} onChange={(e) => setVendor(e.target.value)} />
        </label>
        <label className="field" style={{ width: 170 }}>
          <span className="field-label">Warranty until</span>
          <input className="input" type="date" value={warranty} onChange={(e) => setWarranty(e.target.value)} />
        </label>
        <label className="field" style={{ width: 150 }}>
          <span className="field-label">Service every (days)</span>
          <input className="input" inputMode="numeric" value={interval} onChange={(e) => setInterval(e.target.value)} />
        </label>
        <label className="field" style={{ width: 170 }}>
          <span className="field-label">Last serviced</span>
          <input className="input" type="date" value={last} onChange={(e) => setLast(e.target.value)} />
        </label>
      </div>
      <div className="row">
        <button type="submit" className="btn btn-primary btn-sm" disabled={busy}>
          Add
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  )
}
