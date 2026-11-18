import { useState } from 'react'
import { approveAction, rejectAction } from '../api/client'
import type { AppContext } from '../app/context'
import { useLoad } from '../lib/useLoad'
import { type ChannelItem, type Channels, type ChannelTermsRow, fetchChannels, requestChannelReprice, saveChannelTerms } from './api'
import { GroupedBars, Waterfall } from './charts'
import { dayLabel, dollars, percent, timeLabel, useRestaurantClock } from './format'
import { HeaderSentence, LoadFailed, ProposalCard, SetupChecklist, type SetupStep, SidePanel, Skeleton, StateBanner, StatTile, StatusWord, TileRow, useDeferred, useRowLimit, useWhy, ViewAsTable, type Why, takeHashParam } from './ui'

const TITLE = 'Channels'
const CHECK = 'Check your agreement with the platform before changing prices.'

export function channelsSentence(data: Channels): string {
  if (data.state === 'setup' || !data.header) {
    const done = data.setup.filter((s) => s.done).length
    return `${done} of ${data.setup.length} setup steps are done.`
  }
  const n = data.header.items
  if (n === 0) return 'No items lose money on delivery platforms, last 28 days.'
  const where = data.header.platform ?? 'delivery platforms'
  const what = data.header.losing ? (n === 1 ? 'item loses money' : 'items lose money') : n === 1 ? 'item keeps too little' : 'items keep too little'
  return `${n} ${what} on ${where}. Cost: about ${dollars(data.header.weekly_dollars)} a week.`
}

export function ChannelsPage({ app }: { app: AppContext }) {
  useRestaurantClock(app.restaurant.timezone)
  const load = useLoad(fetchChannels, [])
  const why = useWhy()
  const deferred = useDeferred()
  const [reviewing, setReviewing] = useState<string | null>(() => takeHashParam('review'))
  const [rates, setRates] = useState<string | null>(null)
  const [decided, setDecided] = useState<Record<string, 'approved' | 'rejected'>>({})
  const [busy, setBusy] = useState(false)
  const data = load.data
  const limit = useRowLimit(data?.items ?? [])

  if (load.status === 'loading') return <main className="page ml-page"><Skeleton title={TITLE} /></main>
  if (!data) return <main className="page ml-page"><LoadFailed title={TITLE} what="Channels" onRetry={() => void load.reload()} /></main>

  const updated = data.computed_at ? `${dayLabel(data.computed_at)} ${timeLabel(data.computed_at)}` : undefined
  const editor = rates !== null && <RatesEditor app={app} platform={rates} current={data.terms.find((t) => t.platform === rates)} platforms={data.platforms} onClose={() => setRates(null)} onSaved={() => { setRates(null); void load.reload() }} />

  if (data.state === 'setup') {
    const words: Record<string, { label: string; detail: string }> = {
      menu_costs: { label: 'Cost a dish', detail: 'What an item keeps starts from its cost per plate.' },
      orders: { label: 'Import orders', detail: 'Each order carries its channel and platform.' },
      rates: { label: 'Set platform rates', detail: 'Rates price every order with no payout report.' },
    }
    const steps: SetupStep[] = data.setup.map((s) => {
      const missing = s.missing ?? []
      const base = { label: s.key === 'rates' && missing.length ? `Set rates for ${missing.join(', ')}` : words[s.key].label, detail: words[s.key].detail, done: s.done }
      if (s.key === 'menu_costs') return { ...base, action: { label: 'Open menu costs', href: '#menu-costs' } }
      if (s.key === 'orders') return { ...base, action: { label: 'Open connections', href: '#connect' } }
      return { ...base, action: app.canManage ? { label: 'Set rates', onClick: () => setRates(missing[0] ?? '') } : undefined }
    })
    return (
      <main className="page ml-page">
        <HeaderSentence title={TITLE} sentence={channelsSentence(data)} state="setup" />
        <SetupChecklist title="Before channels can show" steps={steps} />
        {editor}
      </main>
    )
  }

  const chart = data.chart ?? { series: [], items: [] }
  const flagged = data.items.filter((i) => i.finding)
  const statusOf = (item: ChannelItem) => decided[item.row_id] ?? item.finding?.proposal_status ?? null
  const period = `28 days to ${dayLabel(data.window_end)}`

  const decide = (item: ChannelItem, decision: 'approved' | 'rejected') => {
    const id = item.finding?.proposal_id
    if (!id) return
    setDecided((d) => ({ ...d, [item.row_id]: decision }))
    setReviewing(null)
    const revert = () =>
      setDecided((d) => {
        const next = { ...d }
        delete next[item.row_id]
        return next
      })
    deferred.start(item.row_id, `${item.name} price ${decision === 'approved' ? 'approved' : 'dismissed'}.`, () => {
      const call = decision === 'approved' ? approveAction(id) : rejectAction(id)
      call
        .then(() => app.refresh())
        .catch((err: unknown) => {
          revert()
          app.toast('The decision was not saved', err instanceof Error ? err.message : 'Try again.')
        })
    }, revert)
  }
  const suggest = async (item: ChannelItem) => {
    setBusy(true)
    try {
      await requestChannelReprice(item.row_id)
      await load.reload()
    } catch (err) {
      app.toast('No price was suggested', err instanceof Error ? err.message : 'Try again.')
    } finally {
      setBusy(false)
    }
  }
  const tileWhy = (label: string, keptPct: string): Why => ({
    result: `${percent(keptPct)} of price kept on ${label}`,
    formula: 'Kept share = dollars kept on every item sold / sales of those items, on this channel',
    inputs: chart.items.filter((i) => i.values[label] != null).map((i) => ({ label: i.name, value: `${dollars(i.values[label])} kept per item` })),
    dataBasis: `Orders for the ${period}`,
    freshness: updated ? `Computed ${updated}.` : 'Not computed yet.',
    fix: { label: 'Open menu costs', href: '#menu-costs' },
  })
  const top = flagged[0]
  const topDine = top ? chart.items.find((i) => i.pos_item_id === top.pos_item_id)?.values['Dine in'] : undefined
  const review = reviewing ? data.items.find((i) => i.row_id === reviewing) : undefined

  return (
    <main className={`page ml-page${data.stale ? ' ml-stale' : ''}`}>
      <HeaderSentence title={TITLE} sentence={channelsSentence(data)} updated={updated} state="live">
        {app.canManage && (
          <button className="btn btn-secondary btn-sm" onClick={() => setRates(data.platforms[0] ?? '')}>
            Edit rates
          </button>
        )}
      </HeaderSentence>
      {data.stale && data.last_order_date && <StateBanner kind="stale" text={`Orders were last imported ${dayLabel(data.last_order_date)}. Import a newer export.`} action={{ label: 'Open connections', href: '#connect' }} />}
      {data.match_rate != null &&
        (data.contract_rates ? (
          <StateBanner kind="learning" text={`Based on your contract rates. ${percent(data.match_rate)} of platform orders matched a payout report.`} action={{ label: 'Import payouts', href: '#connect' }} />
        ) : (
          <p className="muted small" data-testid="match-rate">
            {percent(data.match_rate)} of platform orders matched a payout report.
          </p>
        ))}
      <TileRow>
        {data.tiles.map((tile) =>
          tile.kept_pct != null ? (
            <StatTile key={tile.label} value={percent(tile.kept_pct)} label={tile.label} compare={`${dollars(tile.kept ?? 0)} kept of ${dollars(tile.price ?? 0)} an item`} tone={Number(tile.kept_pct) < 0 ? 'bad' : undefined} onWhy={() => why.show(tileWhy(tile.label, tile.kept_pct ?? '0'))} />
          ) : null,
        )}
      </TileRow>

      {chart.items.length > 0 && (
        <ViewAsTable
          title={top && top.kept != null ? `${top.name} keeps ${dollars(top.kept)} on ${top.platform}${topDine ? `, ${dollars(topDine)} dine in` : ''}` : 'Every delivery item keeps over half of its dine-in amount'}
          summary={`Dollars kept per item on each channel for the ${chart.items.length} items that sell most on delivery, ${period}.`}
          chart={<GroupedBars rows={chart.items.map((i) => ({ key: i.pos_item_id, label: i.name, values: Object.fromEntries(Object.entries(i.values).map(([k, v]) => [k, Number(v)])) }))} series={chart.series} unit="dollars kept per item" format={(v) => dollars(v)} />}
          columns={['Item', ...chart.series]}
          rows={chart.items.map((i) => [i.name, ...chart.series.map((s) => (i.values[s] != null ? dollars(i.values[s]) : 'Not sold'))])}
        />
      )}

      {data.items.length > 0 && (
        <section className="card" aria-label="Delivery items">
          <div className="table-wrap">
            <table className="table ml-table">
              <thead>
                <tr>
                  <th>Item</th>
                  <th className="num ml-hide-phone">Price</th>
                  <th className="num ml-hide-phone">Fees</th>
                  <th className="num ml-hide-phone">Cost per plate</th>
                  <th className="num">Kept per item</th>
                  <th className="num ml-hide-phone">Sold in 28 days</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {limit.shown.map((item) => {
                  const status = statusOf(item)
                  return (
                    <tr key={item.row_id}>
                      <td>
                        <span className="strong">{item.name}</span>
                        <div className="muted small">{item.platform}</div>
                      </td>
                      <td className="num ml-hide-phone">{dollars(item.price)}</td>
                      <td className="num ml-hide-phone">{dollars(item.fees)}</td>
                      <td className="num ml-hide-phone">{item.plate_cost != null ? dollars(item.plate_cost) : <span className="muted">No recipe</span>}</td>
                      <td className="num">
                        {item.kept != null ? (
                          <>
                            <button className="ml-why-link ml-cell-link" onClick={() => why.open('channel_item_margins', item.row_id)}>
                              {dollars(item.kept)}
                            </button>
                            {item.finding && <StatusWord tone="bad">{Number(item.kept) < 0 ? 'Loses money' : 'Keeps too little'}</StatusWord>}
                          </>
                        ) : (
                          <span className="muted">Not known</span>
                        )}
                      </td>
                      <td className="num ml-hide-phone">{Number(item.units).toLocaleString('en-US')}</td>
                      <td>
                        {deferred.pendingId === item.row_id ? (
                          <button className="btn btn-secondary btn-sm" onClick={deferred.undo}>
                            Undo
                          </button>
                        ) : status === 'approved' ? (
                          <StatusWord tone="good">Approved</StatusWord>
                        ) : status === 'rejected' ? (
                          <StatusWord tone="neutral">Dismissed</StatusWord>
                        ) : item.finding && app.canManage ? (
                          <button className="btn btn-secondary btn-sm" onClick={() => setReviewing(item.row_id)}>
                            Review price
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {limit.more && <div className="card-footer">{limit.more}</div>}
        </section>
      )}

      {review && review.finding && review.kept != null && review.plate_cost != null && (
        <SidePanel title={`${review.name} on ${review.platform}`} onClose={() => setReviewing(null)}>
          <div className="ml-stack">
            {review.finding.proposal_id && review.finding.suggested_price ? (
              <ProposalCard
                title={`Price it at ${dollars(review.finding.suggested_price)} on ${review.platform}`}
                sentence={`${review.name} keeps ${dollars(review.kept)} an item on ${review.platform} at ${dollars(review.price)}.`}
                note={`${dollars(review.finding.suggested_price)} keeps ${dollars(review.finding.kept_at_suggested ?? 0)}, against a target of ${dollars(review.finding.target ?? 0)}. ${CHECK}`}
                impact={review.finding.weekly_dollars}
                primary={{ label: 'Approve', onClick: () => decide(review, 'approved') }}
                onDismiss={() => decide(review, 'rejected')}
                onWhy={() => why.open('channel_item_margins', review.row_id)}
              />
            ) : (
              <>
                <p>No platform price is suggested yet for {review.name}.</p>
                <button className="btn btn-primary" disabled={busy} onClick={() => void suggest(review)}>
                  Suggest price
                </button>
              </>
            )}
            <div data-testid="waterfall">
              <Waterfall
                start={{ label: 'Price', amount: Number(review.price) }}
                steps={[
                  { label: 'Commission', amount: Number(review.commission) },
                  { label: 'Processing', amount: Number(review.processing) },
                  { label: 'Fixed fees, packaging', amount: Number(review.fixed) },
                  { label: 'Food cost', amount: Number(review.plate_cost) },
                ]}
                end={{ label: 'Kept per item', amount: Number(review.kept) }}
                format={(v) => dollars(v)}
              />
            </div>
          </div>
        </SidePanel>
      )}
      {editor}
      {deferred.bar}
      {why.panel}
    </main>
  )
}

/** A platform's contract rates. They price every order that has no payout row. */
function RatesEditor({ app, platform, current, platforms, onClose, onSaved }: { app: AppContext; platform: string; current?: ChannelTermsRow; platforms: string[]; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(platform)
  const pct = (v?: string) => (v ? String(Math.round(Number(v) * 10000) / 100) : '')
  const [commission, setCommission] = useState(pct(current?.commission_pct))
  const [processing, setProcessing] = useState(pct(current?.processing_pct))
  const [fixed, setFixed] = useState(current ? String(Number(current.fixed_fee_per_order)) : '')
  const [packaging, setPackaging] = useState(current ? String(Number(current.packaging_cost_per_order)) : '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const save = async () => {
    const c = Number(commission)
    const p = Number(processing || 0)
    if (!name.trim() || !(c >= 0) || commission === '' || !(p >= 0) || c + p >= 100 || !(Number(fixed || 0) >= 0) || !(Number(packaging || 0) >= 0)) {
      setError('Name the platform. Commission and processing are percentages that total under 100.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      await saveChannelTerms({ platform: name.trim(), commission_pct: (c / 100).toFixed(6), processing_pct: (p / 100).toFixed(6), fixed_fee_per_order: Number(fixed || 0).toFixed(2), packaging_cost_per_order: Number(packaging || 0).toFixed(2) })
      app.toast('Rates saved', 'Channels were worked out again.')
      onSaved()
    } catch (err) {
      setError(err instanceof Error ? `The rates were not saved. ${err.message}` : 'The rates were not saved. Try again.')
    } finally {
      setSaving(false)
    }
  }
  return (
    <SidePanel title="Platform rates" onClose={onClose}>
      <div className="ml-stack">
        <label className="field">
          <span className="field-label">Platform</span>
          <input className="input" list="ml-platforms" value={name} onChange={(e) => setName(e.target.value)} />
          <datalist id="ml-platforms">
            {platforms.map((p) => (
              <option key={p} value={p} />
            ))}
          </datalist>
        </label>
        <label className="field">
          <span className="field-label">Commission, %</span>
          <input className="input" inputMode="decimal" value={commission} onChange={(e) => setCommission(e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Processing, %</span>
          <input className="input" inputMode="decimal" value={processing} onChange={(e) => setProcessing(e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Fixed fee an order, $</span>
          <input className="input" inputMode="decimal" value={fixed} onChange={(e) => setFixed(e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Packaging an order, $</span>
          <input className="input" inputMode="decimal" value={packaging} onChange={(e) => setPackaging(e.target.value)} />
        </label>
        {error && <StateBanner kind="error" text={error} />}
        <div className="row">
          <button className="btn btn-primary" disabled={saving} onClick={() => void save()}>
            Save
          </button>
          <button className="btn btn-secondary" onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    </SidePanel>
  )
}
