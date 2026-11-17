import { useState } from 'react'
import { niceMax } from '../components/Charts'
import { useElementWidth } from '../lib/useElementWidth'
import { dayLabel } from './format'

/**
 * The charts of the Margin & Labor pages (spec sections 10.6 and 10.12), hand-built SVG like the rest of the
 * app. The rules they keep: bars start at zero; both axes say their unit; a target is a dashed line with its
 * value written on it; a forecast is a solid Expected line inside a shaded Likely range, with actuals as dots;
 * an intraday chart marks Now; nothing is told by colour alone (patterns, labels and words carry it too); and
 * every mark has a tooltip with the exact value, its unit, its date and where it came from. The finding goes in
 * the title, which the page supplies through `ViewAsTable`.
 */

export type Bar = { key: string; label: string; value: number; note?: string; source?: string }

/** Horizontal bars, largest first, from a zero baseline. An optional target is a dashed line with its value on it. */
export function SortedBars({ bars, unit, format, target, onPick }: {
  bars: Bar[]
  /** What the x-axis measures, with its period: "dollars a week". */
  unit: string
  format: (value: number) => string
  target?: { value: number; label: string }
  onPick?: (key: string) => void
}) {
  const [hover, setHover] = useState<string | null>(null)
  const sorted = [...bars].sort((a, b) => b.value - a.value)
  const low = Math.min(0, ...sorted.map((b) => b.value))
  const high = niceMax(Math.max(1e-9, target?.value ?? 0, ...sorted.map((b) => b.value)))
  const span = high - low
  const at = (value: number) => `${((value - low) / span) * 100}%`
  if (sorted.length === 0) return <p className="muted">Nothing to show.</p>
  return (
    <div className="ml-bars">
      <ul className="ml-bars-list">
        {sorted.map((bar) => {
          const body = (
            <>
              <span className="ml-bars-label">{bar.label}</span>
              <span className="ml-bars-track">
                {low < 0 && <span className="ml-bars-zero" style={{ left: at(0) }} />}
                <span
                  className={`ml-bars-fill${bar.value < 0 ? ' negative' : ''}`}
                  style={bar.value >= 0 ? { left: at(0), width: `${(bar.value / span) * 100}%` } : { left: at(bar.value), width: `${(-bar.value / span) * 100}%` }}
                />
                {target && <span className="ml-bars-target" style={{ left: at(target.value) }} />}
              </span>
              <span className="ml-bars-value">
                {format(bar.value)}
                {bar.note && <span className="muted small"> {bar.note}</span>}
              </span>
            </>
          )
          return (
            <li key={bar.key} onMouseEnter={() => setHover(bar.key)} onMouseLeave={() => setHover(null)}>
              {onPick ? (
                <button className="ml-bars-row" onClick={() => onPick(bar.key)}>
                  {body}
                </button>
              ) : (
                <div className="ml-bars-row">{body}</div>
              )}
              {hover === bar.key && (
                <div className="ml-tip" role="tooltip">
                  {bar.label}: {format(bar.value)} ({unit}){bar.source ? `. ${bar.source}` : ''}
                </div>
              )}
            </li>
          )
        })}
      </ul>
      <div className="ml-bars-axis">
        <span>{format(low)}</span>
        <span className="ml-axis-unit">{unit}</span>
        <span>{format(high)}</span>
      </div>
      {target && (
        <div className="ml-bars-key">
          <span className="ml-key-dash" /> {target.label}: {format(target.value)}
        </div>
      )}
    </div>
  )
}

export type ForecastPoint = { date: string; label?: string; low: number; expected: number; high: number; actual?: number | null; drivers?: string[] }

/**
 * Expected as a solid line inside the Likely range as a shaded band; what actually happened as dots. `now`
 * (an index into the points) draws a vertical line labelled Now, for a chart of today.
 */
export function ForecastChart({ points, unit, xLabel = 'Day', now, height = 240 }: { points: ForecastPoint[]; unit: string; xLabel?: string; now?: number; height?: number }) {
  const [hover, setHover] = useState<number | null>(null)
  const [ref, W] = useElementWidth<HTMLDivElement>()
  const pad = { top: 22, right: 20, bottom: 46, left: 56 }
  const innerW = W - pad.left - pad.right
  const innerH = height - pad.top - pad.bottom
  const n = points.length
  if (n === 0) return <div ref={ref} />
  const max = niceMax(Math.max(1, ...points.flatMap((p) => [p.high, p.actual ?? 0])))
  const x = (i: number) => pad.left + (n <= 1 ? innerW / 2 : (i / (n - 1)) * innerW)
  const y = (v: number) => pad.top + innerH - (Math.max(0, v) / max) * innerH
  const line = points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.expected).toFixed(1)}`).join(' ')
  const band = `${points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.high).toFixed(1)}`).join(' ')} ${[...points].reverse().map((p, i) => `L${x(n - 1 - i).toFixed(1)},${y(p.low).toFixed(1)}`).join(' ')} Z`
  const slot = innerW / Math.max(1, n - 1)
  const last = n - 1
  return (
    <div className="chart" ref={ref}>
      <svg width={W} height={height} viewBox={`0 0 ${W} ${height}`} role="img" aria-label={`Expected ${unit} with the likely range, and what happened`}>
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line className="chart-grid" x1={pad.left} x2={W - pad.right} y1={y(max * f)} y2={y(max * f)} />
            <text className="chart-axis" x={pad.left - 8} y={y(max * f) + 4} textAnchor="end">
              {Math.round(max * f).toLocaleString('en-US')}
            </text>
          </g>
        ))}
        <text className="chart-axis" transform={`translate(14 ${pad.top + innerH / 2}) rotate(-90)`} textAnchor="middle">
          {unit}
        </text>
        <path d={band} fill="var(--chart-1)" opacity={0.14} />
        <path d={line} fill="none" stroke="var(--chart-1)" strokeWidth={2} strokeLinejoin="round" />
        {points.map((p, i) => (p.actual != null ? <circle key={p.date} cx={x(i)} cy={y(p.actual)} r={4} fill="var(--ink)" stroke="var(--surface)" strokeWidth={2} /> : null))}
        {now != null && now >= 0 && now < n && (
          <g>
            <line x1={x(now)} x2={x(now)} y1={pad.top - 6} y2={pad.top + innerH} stroke="var(--ink)" strokeWidth={1} strokeDasharray="3 3" />
            <text className="chart-label" x={x(now)} y={pad.top - 10} textAnchor="middle">
              Now
            </text>
          </g>
        )}
        {/* Direct labels: three series or fewer are named on the chart, not in a legend to match by colour. */}
        <text className="chart-label" x={x(last) - 6} y={y(points[last].expected) - 8} textAnchor="end">
          Expected
        </text>
        <text className="chart-axis" x={x(last) - 6} y={y(points[last].high) - 6} textAnchor="end">
          Likely range
        </text>
        {points.map((p, i) => (
          <g key={`x-${p.date}`}>
            {(n <= 14 || i % Math.ceil(n / 7) === 0) && (
              <text className="chart-axis" x={x(i)} y={height - 26} textAnchor="middle">
                {p.label ?? dayLabel(p.date).split(' ')[0]}
              </text>
            )}
            {p.drivers && p.drivers.length > 0 && (
              <text className="chart-axis ml-driver" x={x(i)} y={height - 12} textAnchor="middle">
                {p.drivers[0]}
              </text>
            )}
            <rect x={x(i) - slot / 2} y={pad.top} width={slot} height={innerH} fill="transparent" onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
          </g>
        ))}
        <text className="chart-axis" x={pad.left + innerW / 2} y={height - 1} textAnchor="middle">
          {xLabel}
        </text>
      </svg>
      {hover !== null && (
        <div className="tooltip" style={{ left: `${(x(hover) / W) * 100}%` }}>
          <div className="tooltip-title">{points[hover].label ?? dayLabel(points[hover].date)}</div>
          <div className="tooltip-row">Expected {Math.round(points[hover].expected).toLocaleString('en-US')} {unit}</div>
          <div className="tooltip-row">
            Likely range {Math.round(points[hover].low).toLocaleString('en-US')} to {Math.round(points[hover].high).toLocaleString('en-US')}
          </div>
          {points[hover].actual != null && <div className="tooltip-row">Actual {Math.round(points[hover].actual as number).toLocaleString('en-US')}</div>}
          {(points[hover].drivers ?? []).map((d) => (
            <div key={d} className="tooltip-row">{d}</div>
          ))}
          <div className="tooltip-row">Source: forecast and point-of-sale orders</div>
        </div>
      )}
    </div>
  )
}

export type CoverageStep = { label: string; need: number; needIfBusy: number; scheduled: number }

/**
 * People needed against people scheduled, in steps. Needed is a solid step line, needed-if-busy a dashed one,
 * scheduled a filled step. Where they differ the gap is hatched: one pattern for too few, another for too many.
 */
export function CoverageChart({ steps, role, now, height = 220 }: { steps: CoverageStep[]; role: string; now?: number; height?: number }) {
  const [hover, setHover] = useState<number | null>(null)
  const [ref, W] = useElementWidth<HTMLDivElement>()
  const pad = { top: 22, right: 16, bottom: 40, left: 44 }
  const innerW = W - pad.left - pad.right
  const innerH = height - pad.top - pad.bottom
  const n = steps.length
  if (n === 0) return <div ref={ref} />
  const max = Math.max(2, ...steps.flatMap((s) => [s.needIfBusy, s.scheduled, s.need])) + 1
  const slot = innerW / n
  const x = (i: number) => pad.left + slot * i
  const y = (v: number) => pad.top + innerH - (v / max) * innerH
  const stepPath = (pick: (s: CoverageStep) => number) => steps.map((s, i) => `${i ? 'L' : 'M'}${x(i)},${y(pick(s))} L${x(i + 1)},${y(pick(s))}`).join(' ')
  const id = `cov-${role.replace(/\W+/g, '')}`
  return (
    <div className="chart" ref={ref}>
      <svg width={W} height={height} viewBox={`0 0 ${W} ${height}`} role="img" aria-label={`${role}: people needed against people scheduled, by interval`}>
        <defs>
          <pattern id={`${id}-under`} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="6" stroke="var(--danger)" strokeWidth="2" />
          </pattern>
          <pattern id={`${id}-over`} width="6" height="6" patternUnits="userSpaceOnUse">
            <circle cx="3" cy="3" r="1.2" fill="var(--text-muted)" />
          </pattern>
        </defs>
        {Array.from({ length: max + 1 }, (_, v) => v)
          .filter((v) => v % Math.ceil(max / 4) === 0)
          .map((v) => (
            <g key={v}>
              <line className="chart-grid" x1={pad.left} x2={W - pad.right} y1={y(v)} y2={y(v)} />
              <text className="chart-axis" x={pad.left - 8} y={y(v) + 4} textAnchor="end">
                {v}
              </text>
            </g>
          ))}
        <text className="chart-axis" transform={`translate(12 ${pad.top + innerH / 2}) rotate(-90)`} textAnchor="middle">
          people
        </text>
        {steps.map((s, i) => (
          <g key={s.label}>
            <rect x={x(i)} y={y(s.scheduled)} width={slot} height={y(0) - y(s.scheduled)} fill="var(--chart-1)" opacity={0.22} />
            {s.scheduled < s.need && <rect x={x(i)} y={y(s.need)} width={slot} height={y(s.scheduled) - y(s.need)} fill={`url(#${id}-under)`} />}
            {s.scheduled > s.needIfBusy && <rect x={x(i)} y={y(s.scheduled)} width={slot} height={y(s.needIfBusy) - y(s.scheduled)} fill={`url(#${id}-over)`} />}
          </g>
        ))}
        <path d={stepPath((s) => s.needIfBusy)} fill="none" stroke="var(--ink)" strokeWidth={1.5} strokeDasharray="5 4" />
        <path d={stepPath((s) => s.need)} fill="none" stroke="var(--ink)" strokeWidth={2} />
        {now != null && now >= 0 && now <= n && (
          <g>
            <line x1={x(now)} x2={x(now)} y1={pad.top - 6} y2={pad.top + innerH} stroke="var(--ink)" strokeWidth={1} strokeDasharray="3 3" />
            <text className="chart-label" x={x(now)} y={pad.top - 10} textAnchor="middle">
              Now
            </text>
          </g>
        )}
        {steps.map((s, i) => (
          <g key={`x-${s.label}`}>
            {i % Math.max(1, Math.ceil(n / 8)) === 0 && (
              <text className="chart-axis" x={x(i)} y={height - 22} textAnchor="middle">
                {s.label}
              </text>
            )}
            <rect x={x(i)} y={pad.top} width={slot} height={innerH} fill="transparent" onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
          </g>
        ))}
        <text className="chart-axis" x={pad.left + innerW / 2} y={height - 4} textAnchor="middle">
          Time of day, 30-minute intervals
        </text>
      </svg>
      <div className="ml-coverage-key">
        <span><span className="ml-key-solid" /> People needed</span>
        <span><span className="ml-key-dash" /> People needed if busy</span>
        <span><span className="ml-key-fill" /> Scheduled</span>
        <span><span className="ml-key-under" /> Too few</span>
        <span><span className="ml-key-over" /> More than needed</span>
      </div>
      {hover !== null && (
        <div className="tooltip" style={{ left: `${((x(hover) + slot / 2) / W) * 100}%` }}>
          <div className="tooltip-title">{role}, {steps[hover].label}</div>
          <div className="tooltip-row">People needed {steps[hover].need}</div>
          <div className="tooltip-row">People needed if busy {steps[hover].needIfBusy}</div>
          <div className="tooltip-row">Scheduled {steps[hover].scheduled}</div>
          <div className="tooltip-row">Source: forecast and your standards</div>
        </div>
      )}
    </div>
  )
}

export type WaterfallStep = { label: string; amount: number }

/**
 * From the price down to what is kept: each fee and cost is a step down, and the last bar is the result. Every
 * step carries its own figure, so the chart reads without colour.
 */
export function Waterfall({ start, steps, end, format, height = 220 }: { start: WaterfallStep; steps: WaterfallStep[]; end: WaterfallStep; format: (v: number) => string; height?: number }) {
  const [ref, W] = useElementWidth<HTMLDivElement>()
  const pad = { top: 26, right: 12, bottom: 48, left: 12 }
  const innerW = W - pad.left - pad.right
  const innerH = height - pad.top - pad.bottom
  const all = [start, ...steps, end]
  let running = start.amount
  const levels = [{ from: 0, to: start.amount }]
  for (const step of steps) {
    levels.push({ from: running, to: running - step.amount })
    running -= step.amount
  }
  levels.push({ from: 0, to: end.amount })
  const low = Math.min(0, ...levels.flatMap((l) => [l.from, l.to]))
  const high = Math.max(...levels.flatMap((l) => [l.from, l.to]), 1e-9)
  const y = (v: number) => pad.top + innerH - ((v - low) / (high - low)) * innerH
  const slot = innerW / all.length
  const barW = Math.min(64, slot * 0.6)
  return (
    <div className="chart" ref={ref}>
      <svg width={W} height={height} viewBox={`0 0 ${W} ${height}`} role="img" aria-label={`${start.label} ${format(start.amount)}, less fees and food cost, leaves ${format(end.amount)}`}>
        <line className="chart-grid" x1={pad.left} x2={W - pad.right} y1={y(0)} y2={y(0)} />
        {all.map((step, i) => {
          const level = levels[i]
          const top = Math.min(y(level.from), y(level.to))
          const tall = Math.max(2, Math.abs(y(level.from) - y(level.to)))
          const cx = pad.left + slot * i + slot / 2
          const total = i === 0 || i === all.length - 1
          return (
            <g key={step.label}>
              <rect x={cx - barW / 2} y={top} width={barW} height={tall} rx={3} fill={total ? 'var(--chart-1)' : 'var(--chart-2)'} opacity={total ? 1 : 0.85} />
              {i > 0 && i < all.length - 1 && <line x1={cx - slot + barW / 2} x2={cx - barW / 2} y1={y(level.from)} y2={y(level.from)} stroke="var(--border-strong)" strokeDasharray="2 3" />}
              <text className="chart-label" x={cx} y={top - 6} textAnchor="middle">
                {total ? format(step.amount) : `−${format(step.amount)}`}
              </text>
              <text className="chart-axis" x={cx} y={height - 28} textAnchor="middle">
                {step.label.split(' ').slice(0, 2).join(' ')}
              </text>
              <text className="chart-axis" x={cx} y={height - 14} textAnchor="middle">
                {step.label.split(' ').slice(2).join(' ')}
              </text>
            </g>
          )
        })}
        <text className="chart-axis" x={pad.left + innerW / 2} y={height - 1} textAnchor="middle">
          dollars per item
        </text>
      </svg>
    </div>
  )
}
