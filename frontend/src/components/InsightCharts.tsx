import { useState } from 'react'
import { money, pct, qty, shortDate } from '../lib/format'
import { useElementWidth } from '../lib/useElementWidth'
import { niceMax } from './Charts'

/**
 * The phase-2 charts. Same conventions as Charts.tsx: one y-axis, hairline
 * grid, a hover tooltip on every chart, text in text colours and series
 * colour only on marks and legend swatches. Every chart here also has a
 * table view on its page, so no figure is readable from colour alone.
 */

type ShareWeek = { week_of: string; food_pct: number; labor_pct: number; prime_pct: number; sales: number }

/** Prime cost by week: food and labor as shares of sales, stacked, so the bar's height is the prime cost. */
export function PrimeCostChart({ weeks, height = 220 }: { weeks: ShareWeek[]; height?: number }) {
  const [hover, setHover] = useState<number | null>(null)
  const [ref, W] = useElementWidth<HTMLDivElement>()
  const pad = { top: 18, right: 12, bottom: 28, left: 44 }
  const innerW = W - pad.left - pad.right
  const innerH = height - pad.top - pad.bottom
  if (weeks.length === 0) return <div className="empty" ref={ref}>No whole week of sales and labor yet.</div>

  const max = Math.max(50, Math.ceil(Math.max(...weeks.map((w) => w.food_pct + w.labor_pct)) / 25) * 25)
  const slot = innerW / weeks.length
  const barW = Math.min(44, slot * 0.5)
  const y = (v: number) => pad.top + innerH - (v / max) * innerH
  const cx = (i: number) => pad.left + slot * i + slot / 2
  const ticks = [0, max / 2, max]

  return (
    <div className="chart" ref={ref}>
      <svg width={W} height={height} viewBox={`0 0 ${W} ${height}`} role="img" aria-label="Food and labor cost as a share of sales, by week">
        {ticks.map((t) => (
          <g key={t}>
            <line className="chart-grid" x1={pad.left} x2={W - pad.right} y1={y(t)} y2={y(t)} />
            <text className="chart-axis" x={pad.left - 10} y={y(t) + 4} textAnchor="end">
              {t}%
            </text>
          </g>
        ))}
        {weeks.map((w, i) => {
          const left = cx(i) - barW / 2
          const foodTop = y(w.food_pct)
          const laborTop = y(w.food_pct + w.labor_pct)
          const dim = hover === null || hover === i ? 1 : 0.55
          return (
            <g key={w.week_of}>
              <rect x={left} y={foodTop} width={barW} height={Math.max(0, y(0) - foodTop)} fill="var(--chart-1)" opacity={dim} />
              {/* A 2px gap of surface between the two segments, and a rounded data end. */}
              <path
                d={`M${left},${foodTop - 2} L${left},${laborTop + 4} Q${left},${laborTop} ${left + 4},${laborTop} L${left + barW - 4},${laborTop} Q${left + barW},${laborTop} ${left + barW},${laborTop + 4} L${left + barW},${foodTop - 2} Z`}
                fill="var(--chart-2)"
                opacity={dim}
              />
              <text className="chart-label" x={cx(i)} y={laborTop - 6} textAnchor="middle">
                {pct(w.prime_pct, 0)}
              </text>
              <text className="chart-axis" x={cx(i)} y={height - 6} textAnchor="middle">
                {shortDate(w.week_of)}
              </text>
              <rect x={pad.left + slot * i} y={pad.top} width={slot} height={innerH} fill="transparent" onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
            </g>
          )
        })}
      </svg>
      {hover !== null && (
        <div className="tooltip" style={{ left: `${(cx(hover) / W) * 100}%` }}>
          <div className="tooltip-title">Week of {shortDate(weeks[hover].week_of)}</div>
          <div className="tooltip-row">
            <span className="legend-swatch" style={{ background: 'var(--chart-2)', margin: 0 }} />
            Labor {pct(weeks[hover].labor_pct)}
          </div>
          <div className="tooltip-row">
            <span className="legend-swatch" style={{ background: 'var(--chart-1)', margin: 0 }} />
            Food {pct(weeks[hover].food_pct)}
          </div>
          <div className="tooltip-row">Prime cost {pct(weeks[hover].prime_pct)} of {money(weeks[hover].sales)}</div>
        </div>
      )}
    </div>
  )
}

type CompareRow = { key: string; label: string; now: number; before: number }

/**
 * This period against the last, one row each: a bar for now and a tick for
 * before. One hue, because the rows are the same measure; the tick is ink,
 * so "before" never reads as a second series.
 */
export function CompareBars({ rows, onPick, picked }: { rows: CompareRow[]; onPick?: (key: string) => void; picked?: string | null }) {
  const max = niceMax(Math.max(1, ...rows.flatMap((r) => [r.now, r.before])))
  if (rows.length === 0) return <div className="empty">Nothing bought in this window.</div>
  return (
    <ul className="compare-bars" aria-label="This period against the one before">
      {rows.map((r) => {
        const change = r.before > 0 ? ((r.now - r.before) / r.before) * 100 : null
        const body = (
          <>
            <span className="compare-label">{r.label}</span>
            <span className="compare-track" title={`${money(r.now)} now, ${money(r.before)} before`}>
              <span className="compare-fill" style={{ width: `${(r.now / max) * 100}%` }} />
              {r.before > 0 && <span className="compare-tick" style={{ left: `${(r.before / max) * 100}%` }} />}
            </span>
            <span className="compare-value">
              <span className="strong">{money(r.now)}</span>
              <span className="muted small">
                {change === null ? 'new' : `${change >= 0 ? '+' : '−'}${Math.abs(change).toFixed(0)}% vs ${money(r.before)}`}
              </span>
            </span>
          </>
        )
        return (
          <li key={r.key}>
            {onPick ? (
              <button className="compare-row" aria-pressed={picked === r.key} onClick={() => onPick(r.key)}>
                {body}
              </button>
            ) : (
              <div className="compare-row">{body}</div>
            )}
          </li>
        )
      })}
    </ul>
  )
}

export type RunwayDay = { date: string; level: number; delivered: number }

/**
 * Stock projected forward a day at a time. The line is the level; the
 * dashed rules are the reorder point and the par, labelled on the plot; a
 * delivery is a marker on the day it lands.
 */
export function RunwayChart({ days, unit, reorderAt, par, height = 230 }: { days: RunwayDay[]; unit: string; reorderAt: number; par: number; height?: number }) {
  const [hover, setHover] = useState<number | null>(null)
  const [ref, W] = useElementWidth<HTMLDivElement>()
  const pad = { top: 18, right: 16, bottom: 28, left: 52 }
  const innerW = W - pad.left - pad.right
  const innerH = height - pad.top - pad.bottom
  const n = days.length
  if (n === 0) return <div ref={ref} />

  const max = niceMax(Math.max(1, par, reorderAt, ...days.map((d) => d.level)))
  const x = (i: number) => pad.left + (n <= 1 ? innerW / 2 : (i / (n - 1)) * innerW)
  const y = (v: number) => pad.top + innerH - (Math.max(0, v) / max) * innerH
  const path = days.map((d, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(d.level).toFixed(1)}`).join(' ')
  const band = innerW / Math.max(1, n - 1)
  const out = days.findIndex((d) => d.level <= 0)
  const rule = (value: number, label: string) =>
    value > 0 && value <= max ? (
      <g>
        <line x1={pad.left} x2={W - pad.right} y1={y(value)} y2={y(value)} stroke="var(--text-faint)" strokeWidth={1} strokeDasharray="4 4" />
        <text className="chart-axis" x={W - pad.right} y={y(value) - 5} textAnchor="end">
          {label} {qty(value)} {unit}
        </text>
      </g>
    ) : null

  return (
    <div className="chart" ref={ref}>
      <svg width={W} height={height} viewBox={`0 0 ${W} ${height}`} role="img" aria-label={`Stock on hand projected over the next ${n - 1} days`}>
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line className="chart-grid" x1={pad.left} x2={W - pad.right} y1={y(max * f)} y2={y(max * f)} />
            <text className="chart-axis" x={pad.left - 10} y={y(max * f) + 4} textAnchor="end">
              {qty(max * f)}
            </text>
          </g>
        ))}
        {rule(reorderAt, 'Reorder at')}
        {rule(par, 'Par')}
        <path d={`${path} L${x(n - 1)},${pad.top + innerH} L${x(0)},${pad.top + innerH} Z`} fill="var(--chart-1)" opacity={0.08} />
        <path d={path} fill="none" stroke="var(--chart-1)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {days.map((d, i) =>
          d.delivered > 0 ? (
            <g key={`in-${d.date}`}>
              <circle cx={x(i)} cy={y(d.level)} r={5} fill="var(--chart-1)" stroke="var(--surface)" strokeWidth={2} />
              <text className="chart-label" x={x(i)} y={y(d.level) - 10} textAnchor={i > n * 0.8 ? 'end' : 'middle'}>
                +{qty(d.delivered)} {unit}
              </text>
            </g>
          ) : null,
        )}
        {out > 0 && (
          <text className="chart-label" x={Math.min(x(out), W - pad.right)} y={y(0) - 8} textAnchor={out > n * 0.7 ? 'end' : 'start'}>
            Out {shortDate(days[out].date)}
          </text>
        )}
        {hover !== null && <line className="chart-crosshair" x1={x(hover)} x2={x(hover)} y1={pad.top} y2={pad.top + innerH} />}
        {hover !== null && <circle cx={x(hover)} cy={y(days[hover].level)} r={4} fill="var(--chart-1)" stroke="var(--surface)" strokeWidth={2} />}
        <text className="chart-axis" x={pad.left} y={height - 6}>
          Today
        </text>
        <text className="chart-axis" x={W - pad.right} y={height - 6} textAnchor="end">
          {shortDate(days[n - 1].date)}
        </text>
        {days.map((d, i) => (
          <rect key={d.date} x={x(i) - band / 2} y={pad.top} width={band} height={innerH} fill="transparent" onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
        ))}
      </svg>
      {hover !== null && (
        <div className="tooltip" style={{ left: `${(x(hover) / W) * 100}%` }}>
          <div className="tooltip-title">{hover === 0 ? 'Today' : shortDate(days[hover].date)}</div>
          <div className="tooltip-row">
            {qty(Math.max(0, days[hover].level))} {unit} on hand
          </div>
          {days[hover].delivered > 0 && (
            <div className="tooltip-row">
              +{qty(days[hover].delivered)} {unit} delivered
            </div>
          )}
        </div>
      )}
    </div>
  )
}
