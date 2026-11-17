import { useEffect, useMemo, useRef, useState } from 'react'
import { type CountLine, addArea, discardCount, getCount, listAreas, listCounts, placeItem, postCount, removeArea, saveCountLines, startCount, type StorageArea } from '../api/stock'
import type { AppContext } from '../app/context'
import { Icon } from '../components/Icon'
import { Empty, Loaded } from '../components/PageState'
import { WasteDialog } from '../components/WasteDialog'
import { money, qty, relativeTime } from '../lib/format'
import { say, spokenNumber, useSpeech } from '../lib/speech'
import { readHashParam } from '../lib/useHashRoute'
import { useLoad } from '../lib/useLoad'

/** "$218.20 under what was expected": a count's total difference in words, never a bare minus sign. */
const against = (dollars: number) => (Math.abs(dollars) < 0.005 ? 'as expected' : `${money(Math.abs(dollars), true)} ${dollars < 0 ? 'under' : 'over'} what was expected`)

const draftKey = (id: string) => `fullhouse.count.${id}`
type Draft = Record<string, number | null>
function readDraft(id: string): Draft {
  try {
    return JSON.parse(window.localStorage.getItem(draftKey(id)) ?? '{}') as Draft
  } catch {
    return {}
  }
}
function writeDraft(id: string, draft: Draft | null) {
  try {
    if (draft === null) window.localStorage.removeItem(draftKey(id))
    else window.localStorage.setItem(draftKey(id), JSON.stringify(draft))
  } catch {
    /* no storage: the sheet still saves to the server as you go */
  }
}

/**
 * One count sheet: every item in the area, in shelf order, each with a
 * large field, steppers and "Same". What is entered is kept on the phone
 * and saved to the server as you go, so a dropped signal loses nothing;
 * the stock figures only change when the count is posted.
 */
function Sheet({ app, id, onLeave }: { app: AppContext; id: string; onLeave: () => void }) {
  const load = useLoad(() => getCount(id), [id])
  const [entered, setEntered] = useState<Draft>(() => readDraft(id))
  const [saveState, setSaveState] = useState<'saved' | 'saving' | 'offline'>('saved')
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const pending = useRef<Draft>({})
  const timer = useRef<number | null>(null)
  // Hands-free: the sheet reads each item out and takes a number, "same", "skip", "back" or "stop".
  const speech = useSpeech()
  const [speaking, setSpeaking] = useState<string | null>(null)
  const halt = useRef(false)

  const value = (itemId: string, fromServer: number | null) => (itemId in entered ? entered[itemId] : fromServer)

  const flush = async () => {
    const lines = Object.entries(pending.current).map(([inventory_item_id, counted_qty]) => ({ inventory_item_id, counted_qty }))
    if (lines.length === 0) return true
    const sending = pending.current
    pending.current = {}
    setSaveState('saving')
    try {
      await saveCountLines(id, lines)
      setSaveState('saved')
      return true
    } catch {
      pending.current = { ...sending, ...pending.current }
      setSaveState('offline')
      return false
    }
  }
  const set = (itemId: string, counted: number | null) => {
    setEntered((held) => {
      const next = { ...held, [itemId]: counted }
      writeDraft(id, next)
      return next
    })
    pending.current[itemId] = counted
    if (timer.current) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => void flush(), 700)
  }
  // Anything typed while offline is kept on the phone: send it when the sheet opens again or signal returns.
  useEffect(() => {
    const draft = readDraft(id)
    if (Object.keys(draft).length) {
      pending.current = { ...draft }
      void flush()
    }
    const online = () => void flush()
    window.addEventListener('online', online)
    return () => {
      window.removeEventListener('online', online)
      if (timer.current) window.clearTimeout(timer.current)
      void flush()
    }
  }, [id]) // eslint-disable-line react-hooks/exhaustive-deps

  const countAloud = async (lines: CountLine[]) => {
    halt.current = false
    let index = Math.max(0, lines.findIndex((l) => value(l.inventory_item_id, l.counted_qty) === null))
    let misses = 0
    while (index < lines.length && !halt.current) {
      const line = lines[index]
      setSpeaking(line.inventory_item_id)
      document.getElementById(`count-${line.inventory_item_id}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' })
      await say(`${line.item_name}. Expected ${qty(line.expected_qty)} ${line.unit}.`)
      if (halt.current) break
      let heard = ''
      try {
        heard = (await speech.listen()).toLowerCase()
      } catch {
        if (++misses >= 3) break
        await say('I didn’t catch that.')
        continue
      }
      if (/\b(stop|done|finish|finished|pause|that's all)\b/.test(heard)) break
      if (/\b(back|previous)\b/.test(heard)) {
        index = Math.max(0, index - 1)
        continue
      }
      if (/\b(skip|next|pass)\b/.test(heard)) {
        index += 1
        continue
      }
      const amount = /\b(same|correct|yes|right|yep)\b/.test(heard) ? line.expected_qty : spokenNumber(heard)
      if (amount === null) {
        if (++misses >= 3) break
        await say('Say a number, or same, skip, back or stop.')
        continue
      }
      misses = 0
      set(line.inventory_item_id, amount)
      index += 1
    }
    setSpeaking(null)
    halt.current = true
    await say(index >= lines.length ? 'That’s the last one. Post the count when you’re ready.' : 'Paused.')
  }

  const post = async () => {
    setBusy(true)
    try {
      if (!(await flush())) throw new Error('Couldn’t reach FullHouse. Your count is kept on this phone; post it when you have signal.')
      const posted = await postCount(id)
      writeDraft(id, null)
      app.toast('Count posted', `${posted.summary.counted} items counted, ${posted.summary.short} short${posted.summary.over ? `, ${posted.summary.over} over` : ''}.`)
      app.refresh()
      setConfirming(false)
      load.reload()
    } catch (err) {
      app.toast('Could not post the count', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Loaded load={load} what="the count sheet" placeholderHeight={300}>
      {(sheet) => {
        const posted = sheet.status === 'posted'
        const lines = sheet.lines.map((l) => ({ ...l, counted: posted ? l.counted_qty : value(l.inventory_item_id, l.counted_qty) }))
        const done = lines.filter((l) => l.counted !== null)
        const difference = done.reduce((sum, l) => sum + ((l.counted as number) - l.expected_qty) * l.unit_cost, 0)
        return (
          <>
            <div className="count-bar">
              <button className="btn btn-ghost btn-sm" onClick={onLeave}>
                ← All counts
              </button>
              <div className="count-progress" role="status">
                <span className="strong">
                  {done.length} of {lines.length}
                </span>{' '}
                counted
                {!posted && <span className="muted small"> · {saveState === 'saved' ? 'saved' : saveState === 'saving' ? 'saving…' : 'kept on this phone, not sent yet'}</span>}
              </div>
              {!posted && app.flags.v3_voice && speech.supported && (
                <button
                  className="btn btn-secondary"
                  onClick={() => {
                    if (speaking) {
                      halt.current = true
                      speech.stop()
                    } else void countAloud(sheet.lines)
                  }}
                >
                  {speaking ? 'Stop' : 'Count by voice'}
                </button>
              )}
              {!posted && (
                <button className="btn btn-primary" disabled={done.length === 0} onClick={() => setConfirming(true)}>
                  Post count
                </button>
              )}
            </div>
            {posted && (
              <div className="callout callout-info">
                <Icon name="check" />
                <div className="callout-grow">
                  Posted {sheet.posted_at ? relativeTime(sheet.posted_at) : ''}: {sheet.summary.counted} items counted, {sheet.summary.short} short, {sheet.summary.over} over,{' '}
                  {against(sheet.summary.difference_dollars)}. <a href="#variance">See the variance report</a>
                </div>
              </div>
            )}
            {lines.length === 0 ? (
              <Empty title="Nothing kept here yet">Put items in this area under Arrange shelves, then count again.</Empty>
            ) : (
              <ul className="count-list" aria-label={`${sheet.area_name} count sheet`}>
                {lines.map((l) => {
                  const off = l.counted !== null && Math.abs(l.counted - l.expected_qty) > 1e-6
                  const step = l.unit === 'lb' || l.unit === 'kg' || l.unit === 'L' ? 0.5 : 1
                  return (
                    <li key={l.inventory_item_id} id={`count-${l.inventory_item_id}`} className={`count-row${l.counted !== null ? ' done' : ''}${speaking === l.inventory_item_id ? ' speaking' : ''}`}>
                      <div className="count-row-main">
                        <div className="count-row-name">{l.item_name}</div>
                        <div className="muted small">
                          Expected {qty(l.expected_qty)} {l.unit}
                          {off && (
                            <span className={(l.counted as number) < l.expected_qty ? 'count-short' : 'count-over'}>
                              {' '}
                              · {(l.counted as number) < l.expected_qty ? '−' : '+'}
                              {qty(Math.abs((l.counted as number) - l.expected_qty))} {l.unit}
                            </span>
                          )}
                        </div>
                      </div>
                      {posted ? (
                        <div className="count-row-posted">{l.counted === null ? 'not counted' : `${qty(l.counted)} ${l.unit}`}</div>
                      ) : (
                        <div className="count-row-entry">
                          <button className="btn btn-secondary btn-icon" aria-label={`One less ${l.item_name}`} onClick={() => set(l.inventory_item_id, Math.max(0, (l.counted ?? l.expected_qty) - step))}>
                            −
                          </button>
                          <input
                            className="input count-input"
                            type="number"
                            inputMode="decimal"
                            min="0"
                            step="any"
                            aria-label={`${l.item_name}, counted in ${l.unit}`}
                            placeholder={qty(l.expected_qty)}
                            value={l.counted ?? ''}
                            onChange={(e) => set(l.inventory_item_id, e.target.value === '' ? null : Math.max(0, Number(e.target.value)))}
                          />
                          <button className="btn btn-secondary btn-icon" aria-label={`One more ${l.item_name}`} onClick={() => set(l.inventory_item_id, (l.counted ?? l.expected_qty) + step)}>
                            +
                          </button>
                          <button className="btn btn-ghost btn-sm" onClick={() => set(l.inventory_item_id, l.expected_qty)}>
                            Same
                          </button>
                        </div>
                      )}
                    </li>
                  )
                })}
              </ul>
            )}
            {confirming && (
              <>
                <div className="overlay" onClick={() => setConfirming(false)} />
                <div className="modal-wrap">
                  <div className="modal" role="dialog" aria-modal="true" aria-labelledby="post-title">
                    <div className="modal-header">
                      <h2 className="card-title" id="post-title">
                        Post this count?
                      </h2>
                    </div>
                    <div className="modal-body">
                      <p style={{ margin: 0 }}>
                        {done.length} of {lines.length} items counted. The stock figures change to what you counted:{' '}
                        <span className="strong">{money(Math.abs(difference), true)}</span> {difference < 0 ? 'less' : 'more'} than expected. Items you didn’t count stay as they are.
                      </p>
                    </div>
                    <div className="modal-footer">
                      <button className="btn btn-ghost" onClick={() => setConfirming(false)}>
                        Keep counting
                      </button>
                      <button className="btn btn-primary" disabled={busy} onClick={() => void post()}>
                        {busy ? 'Posting…' : 'Post count'}
                      </button>
                    </div>
                  </div>
                </div>
              </>
            )}
          </>
        )
      }}
    </Loaded>
  )
}

/** Managers say where each item is kept and in what order it sits on the shelf: that is the order of the sheet. */
function Arrange({ app, areas, onChanged }: { app: AppContext; areas: StorageArea[]; onChanged: () => void }) {
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const items = useMemo(() => [...app.inventory].sort((a, b) => (a.shelf_order ?? 0) - (b.shelf_order ?? 0) || a.name.localeCompare(b.name)), [app.inventory])
  const act = async (work: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await work()
      app.refresh()
      onChanged()
    } catch (err) {
      app.toast('Could not save that', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }
  const move = (areaId: string | null, index: number, by: number) => {
    const row = items.filter((i) => (i.storage_area_id ?? null) === areaId)
    const other = row[index + by]
    if (!other) return
    const reordered = [...row]
    reordered[index + by] = row[index]
    reordered[index] = other
    void act(() => Promise.all(reordered.map((item, position) => placeItem(item.id, { shelf_order: position }))))
  }
  const groups: { id: string | null; name: string }[] = [...areas.map((a) => ({ id: a.id as string | null, name: a.name })), { id: null, name: 'Not placed yet' }]

  return (
    <section className="card" aria-labelledby="arrange-title">
      <div className="card-header">
        <div>
          <h2 className="card-title" id="arrange-title">
            Arrange shelves
          </h2>
          <div className="card-subtitle">Where each item is kept, top shelf first. The count sheet follows this order.</div>
        </div>
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault()
            if (name.trim()) void act(() => addArea(name.trim())).then(() => setName(''))
          }}
        >
          <input className="input input-sm" aria-label="New area name" placeholder="New area, e.g. Bar" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} />
          <button className="btn btn-secondary btn-sm" disabled={busy || !name.trim()}>
            Add area
          </button>
        </form>
      </div>
      <div className="card-body arrange">
        {groups.map((group) => {
          const row = items.filter((i) => (i.storage_area_id ?? null) === group.id)
          if (group.id === null && row.length === 0) return null
          return (
            <div key={group.id ?? 'none'} className="arrange-group">
              <div className="arrange-head">
                <span className="strong">{group.name}</span>
                <span className="muted small">
                  {row.length} {row.length === 1 ? 'item' : 'items'}
                </span>
                {group.id && (
                  <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => void act(() => removeArea(group.id as string))}>
                    Remove area
                  </button>
                )}
              </div>
              <ul className="arrange-list">
                {row.map((item, index) => (
                  <li key={item.id}>
                    <span className="arrange-name">{item.name}</span>
                    <select
                      className="select input-sm"
                      aria-label={`Where ${item.name} is kept`}
                      value={item.storage_area_id ?? ''}
                      disabled={busy}
                      onChange={(e) => void act(() => placeItem(item.id, { storage_area_id: e.target.value || null, shelf_order: 999 }))}
                    >
                      <option value="">Not placed</option>
                      {areas.map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.name}
                        </option>
                      ))}
                    </select>
                    <button className="btn btn-ghost btn-icon" aria-label={`Move ${item.name} up`} disabled={busy || index === 0} onClick={() => move(group.id, index, -1)}>
                      ↑
                    </button>
                    <button className="btn btn-ghost btn-icon" aria-label={`Move ${item.name} down`} disabled={busy || index === row.length - 1} onClick={() => move(group.id, index, 1)}>
                      ↓
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )
        })}
      </div>
    </section>
  )
}

/** Counting: pick an area, walk it, post. Counts are never required; each one tightens the figures and feeds the variance report. */
export function CountPage({ app }: { app: AppContext }) {
  const [openId, setOpenId] = useState(() => readHashParam())
  const areas = useLoad(listAreas, [])
  const counts = useLoad(listCounts, [openId])
  const [starting, setStarting] = useState<string | null>(null)
  const [wasteOpen, setWasteOpen] = useState(false)

  useEffect(() => {
    const onHash = () => setOpenId(readHashParam())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  const open = (id: string) => {
    window.location.hash = id ? `#count/${id}` : '#count'
    setOpenId(id)
  }
  const start = async (areaId: string | null) => {
    setStarting(areaId ?? 'all')
    try {
      open((await startCount(areaId)).id)
    } catch (err) {
      app.toast('Could not start the count', err instanceof Error ? err.message : String(err))
    } finally {
      setStarting(null)
    }
  }
  const unplaced = app.inventory.filter((i) => !i.storage_area_id).length
  const current = counts.data?.find((c) => c.id === openId)

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">{openId ? (current ? `${current.area_name} count` : 'Count sheet') : 'Count sheet'}</h1>
          <p className="page-subtitle">{openId ? 'Say what is on each shelf. Nothing changes until you post.' : 'Walk an area and say what is there. Each count tightens the stock figures.'}</p>
        </div>
        <button className="btn btn-secondary" onClick={() => setWasteOpen(true)}>
          Log waste
        </button>
      </div>

      {openId ? (
        <Sheet app={app} id={openId} onLeave={() => open('')} />
      ) : (
        <div className="stack">
          <section className="card">
            <div className="card-header">
              <div>
                <h2 className="card-title">Start a count</h2>
                <div className="card-subtitle">One area takes a few minutes. Count what you have time for.</div>
              </div>
            </div>
            <Loaded load={areas} what="storage areas" placeholderHeight={90}>
              {(list) => (
                <div className="card-body count-starts">
                  {list.map((area) => (
                    <button key={area.id} className="count-start" disabled={starting !== null || area.items === 0} onClick={() => void start(area.id)}>
                      <span className="strong">{area.name}</span>
                      <span className="muted small">
                        {area.items} {area.items === 1 ? 'item' : 'items'}
                      </span>
                    </button>
                  ))}
                  {unplaced > 0 && list.length > 0 && (
                    <button className="count-start" disabled={starting !== null} onClick={() => void start('unplaced')}>
                      <span className="strong">Not placed yet</span>
                      <span className="muted small">
                        {unplaced} {unplaced === 1 ? 'item' : 'items'}
                      </span>
                    </button>
                  )}
                  <button className="count-start" disabled={starting !== null || app.inventory.length === 0} onClick={() => void start(null)}>
                    <span className="strong">Everything</span>
                    <span className="muted small">
                      {app.inventory.length} {app.inventory.length === 1 ? 'item' : 'items'}
                    </span>
                  </button>
                </div>
              )}
            </Loaded>
          </section>

          <section className="card">
            <div className="card-header">
              <div>
                <h2 className="card-title">Recent counts</h2>
              </div>
            </div>
            <Loaded load={counts} what="counts" placeholderHeight={90}>
              {(list) =>
                list.length === 0 ? (
                  <div className="card-body">
                    <Empty title="No counts yet">Stock runs on sales and deliveries until you count. A count shows what went missing.</Empty>
                  </div>
                ) : (
                  <ul className="list">
                    {list.map((c) => (
                      <li key={c.id} className="list-item">
                        <div className="list-item-main">
                          <div className="row wrap">
                            <span className="strong">{c.area_name}</span>
                            <span className={`badge ${c.status === 'open' ? 'badge-warning' : 'badge-success'}`}>{c.status === 'open' ? 'In progress' : 'Posted'}</span>
                          </div>
                          <div className="muted small">
                            {c.status === 'open'
                              ? `Started ${relativeTime(c.started_at)} · ${c.summary.counted} of ${c.summary.lines} counted`
                              : `${c.posted_at ? relativeTime(c.posted_at) : ''} · ${c.summary.counted} counted, ${c.summary.short} short, ${against(c.summary.difference_dollars)}`}
                          </div>
                        </div>
                        <div className="row">
                          {c.status === 'open' && app.canManage && (
                            <button className="btn btn-ghost btn-sm" onClick={() => void discardCount(c.id).then(counts.reload).catch((err: unknown) => app.toast('Could not discard it', String(err)))}>
                              Discard
                            </button>
                          )}
                          <button className="btn btn-secondary btn-sm" onClick={() => open(c.id)}>
                            {c.status === 'open' ? 'Continue' : 'View'}
                          </button>
                        </div>
                      </li>
                    ))}
                  </ul>
                )
              }
            </Loaded>
          </section>

          {app.canManage && areas.data && <Arrange app={app} areas={areas.data} onChanged={areas.reload} />}
        </div>
      )}

      <WasteDialog
        open={wasteOpen}
        items={app.inventory}
        onClose={() => setWasteOpen(false)}
        onDone={(r) => {
          app.toast('Waste logged', `${qty(r.taken)} ${r.unit} of ${r.name}, ${money(r.dollars, true)}.`)
          app.refresh()
        }}
      />
    </main>
  )
}
