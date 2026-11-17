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


/** A cost over twelve weeks, small enough for a table row. The row's own text carries the numbers. */
export function Sparkline({ values, label, width = 84, height = 24 }: { values: (number | null)[]; label: string; width?: number; height?: number }) {
  const known = values.filter((v): v is number => v != null)
  if (known.length < 2) return <span className="muted small">No trend yet</span>
  const low = Math.min(...known)
  const high = Math.max(...known)
  const span = high - low || 1
  const x = (i: number) => 2 + (i / (values.length - 1)) * (width - 4)
  const y = (v: number) => height - 3 - ((v - low) / span) * (height - 6)
  let started = false
  const path = values
    .map((v, i) => {
      if (v == null) return ''
      const command = `${started ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`
      started = true
      return command
    })
    .join(' ')
  const last = values.length - 1 - [...values].reverse().findIndex((v) => v != null)
  return (
    <svg className="ml-spark" width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label}>
      <path d={path} fill="none" stroke="var(--chart-1)" strokeWidth={1.5} strokeLinejoin="round" />
      <circle cx={x(last)} cy={y(values[last] as number)} r={2.5} fill="var(--chart-1)" />
    </svg>
  )
}

export type ScatterPoint = { key: string; label: string; x: number; y: number }

/**
 * Each dish by how many it sells (across) and what it keeps a plate (up), with the median of each drawn as a
 * dashed line. The four corners say what to do; every point carries its name.
 */
export function Scatter({ points, xUnit, yUnit, formatY, corners, onPick, height = 320 }: {
  points: ScatterPoint[]
  xUnit: string
  yUnit: string
  formatY: (v: number) => string
  /** Words for the corners: top right, top left, bottom right, bottom left. */
  corners: [string, string, string, string]
  onPick?: (key: string) => void
  height?: number
}) {
  const [hover, setHover] = useState<string | null>(null)
  const [ref, W] = useElementWidth<HTMLDivElement>()
  const pad = { top: 26, right: 24, bottom: 46, left: 64 }
  const innerW = W - pad.left - pad.right
  const innerH = height - pad.top - pad.bottom
  if (points.length === 0) return <div ref={ref} />
  const median = (values: number[]) => {
    const sorted = [...values].sort((a, b) => a - b)
    const mid = Math.floor(sorted.length / 2)
    return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
  }
  const maxX = niceMax(Math.max(1, ...points.map((p) => p.x)))
  const lowY = Math.min(0, ...points.map((p) => p.y))
  const maxY = niceMax(Math.max(1, ...points.map((p) => p.y)))
  const x = (v: number) => pad.left + (v / maxX) * innerW
  const y = (v: number) => pad.top + innerH - ((v - lowY) / (maxY - lowY)) * innerH
  const mx = median(points.map((p) => p.x))
  const my = median(points.map((p) => p.y))
  const shown = points.find((p) => p.key === hover)
  return (
    <div className="chart" ref={ref}>
      <svg width={W} height={height} viewBox={`0 0 ${W} ${height}`} role="img" aria-label={`${yUnit} against ${xUnit}, one point a dish`}>
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line className="chart-grid" x1={pad.left} x2={W - pad.right} y1={y(lowY + (maxY - lowY) * f)} y2={y(lowY + (maxY - lowY) * f)} />
            <text className="chart-axis" x={pad.left - 8} y={y(lowY + (maxY - lowY) * f) + 4} textAnchor="end">
              {formatY(lowY + (maxY - lowY) * f)}
            </text>
            <text className="chart-axis" x={x(maxX * f)} y={height - 26} textAnchor="middle">
              {Math.round(maxX * f).toLocaleString('en-US')}
            </text>
          </g>
        ))}
        <text className="chart-axis" transform={`translate(14 ${pad.top + innerH / 2}) rotate(-90)`} textAnchor="middle">
          {yUnit}
        </text>
        <text className="chart-axis" x={pad.left + innerW / 2} y={height - 6} textAnchor="middle">
          {xUnit}
        </text>
        <line x1={x(mx)} x2={x(mx)} y1={pad.top} y2={pad.top + innerH} stroke="var(--ink)" strokeWidth={1} strokeDasharray="5 4" />
        <line x1={pad.left} x2={W - pad.right} y1={y(my)} y2={y(my)} stroke="var(--ink)" strokeWidth={1} strokeDasharray="5 4" />
        <text className="chart-axis" x={x(mx) + 4} y={pad.top - 8}>
          Median sold {Math.round(mx).toLocaleString('en-US')}
        </text>
        <text className="chart-axis" x={W - pad.right} y={y(my) - 5} textAnchor="end">
          Median kept {formatY(my)}
        </text>
        <text className="chart-label ml-corner" x={W - pad.right - 4} y={pad.top + 14} textAnchor="end">{corners[0]}</text>
        <text className="chart-label ml-corner" x={pad.left + 6} y={pad.top + 14}>{corners[1]}</text>
        <text className="chart-label ml-corner" x={W - pad.right - 4} y={pad.top + innerH - 8} textAnchor="end">{corners[2]}</text>
        <text className="chart-label ml-corner" x={pad.left + 6} y={pad.top + innerH - 8}>{corners[3]}</text>
        {points.map((p) => (
          <g key={p.key} className={onPick ? 'ml-point' : undefined} tabIndex={onPick ? 0 : undefined} role={onPick ? 'link' : undefined} aria-label={`${p.label}: ${Math.round(p.x)} sold, ${formatY(p.y)} kept a plate`}
            onClick={() => onPick?.(p.key)} onKeyDown={(e) => e.key === 'Enter' && onPick?.(p.key)} onMouseEnter={() => setHover(p.key)} onMouseLeave={() => setHover(null)} onFocus={() => setHover(p.key)} onBlur={() => setHover(null)}>
            <circle cx={x(p.x)} cy={y(p.y)} r={12} fill="transparent" />
            <circle cx={x(p.x)} cy={y(p.y)} r={5} fill="var(--chart-1)" stroke="var(--surface)" strokeWidth={2} />
            <text className="chart-axis" x={x(p.x) + 8} y={y(p.y) + 4}>
              {p.label}
            </text>
          </g>
        ))}
      </svg>
      {shown && (
        <div className="tooltip" style={{ left: `${(x(shown.x) / W) * 100}%` }}>
          <div className="tooltip-title">{shown.label}</div>
          <div className="tooltip-row">{Math.round(shown.x).toLocaleString('en-US')} sold, last 28 days</div>
          <div className="tooltip-row">{formatY(shown.y)} kept a plate</div>
          <div className="tooltip-row">Source: point of sale and invoices</div>
        </div>
      )}
    </div>
  )
}

export type StepPoint = { date: string; value: number | null; second?: number | null; marks?: string[] }

/**
 * A value that changes in steps (a cost that moves when an invoice arrives), with an optional second line on
 * the same dollar axis and a marker on each week something changed. Both lines are named on the chart.
 */
export function StepLines({ points, label, secondLabel, unit, format, height = 260 }: { points: StepPoint[]; label: string; secondLabel?: string; unit: string; format: (v: number) => string; height?: number }) {
  const [hover, setHover] = useState<number | null>(null)
  const [ref, W] = useElementWidth<HTMLDivElement>()
  const pad = { top: 22, right: 20, bottom: 44, left: 60 }
  const innerW = W - pad.left - pad.right
  const innerH = height - pad.top - pad.bottom
  const n = points.length
  if (n === 0) return <div ref={ref} />
  const all = points.flatMap((p) => [p.value, p.second ?? null]).filter((v): v is number => v != null)
  const max = niceMax(Math.max(1e-9, ...all))
  const slot = innerW / n
  const x = (i: number) => pad.left + slot * i
  const y = (v: number) => pad.top + innerH - (v / max) * innerH
  const steps = (pick: (p: StepPoint) => number | null | undefined) => {
    let d = ''
    let pen = false
    points.forEach((p, i) => {
      const v = pick(p)
      if (v == null) {
        pen = false
        return
      }
      d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)} L${x(i + 1).toFixed(1)},${y(v).toFixed(1)} `
      pen = true
    })
    return d
  }
  const lastValue = [...points].reverse().find((p) => p.value != null)?.value
  const lastSecond = [...points].reverse().find((p) => p.second != null)?.second
  return (
    <div className="chart" ref={ref}>
      <svg width={W} height={height} viewBox={`0 0 ${W} ${height}`} role="img" aria-label={`${label}${secondLabel ? ` and ${secondLabel}` : ''} by week, in ${unit}`}>
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line className="chart-grid" x1={pad.left} x2={W - pad.right} y1={y(max * f)} y2={y(max * f)} />
            <text className="chart-axis" x={pad.left - 8} y={y(max * f) + 4} textAnchor="end">
              {format(max * f)}
            </text>
          </g>
        ))}
        <text className="chart-axis" transform={`translate(14 ${pad.top + innerH / 2}) rotate(-90)`} textAnchor="middle">
          {unit}
        </text>
        {secondLabel && <path d={steps((p) => p.second)} fill="none" stroke="var(--ink)" strokeWidth={1.5} strokeDasharray="6 4" />}
        <path d={steps((p) => p.value)} fill="none" stroke="var(--chart-1)" strokeWidth={2.5} strokeLinejoin="round" />
        {points.map((p, i) =>
          p.value != null && p.marks && p.marks.length > 0 ? (
            <path key={`m-${p.date}`} d={`M${x(i)},${y(p.value) - 9} l5,9 l-5,9 l-5,-9 z`} fill="var(--surface)" stroke="var(--ink)" strokeWidth={1.5} />
          ) : null,
        )}
        {lastValue != null && (
          <text className="chart-label" x={W - pad.right - 4} y={y(lastValue) - 8} textAnchor="end">
            {label}
          </text>
        )}
        {secondLabel && lastSecond != null && (
          <text className="chart-label" x={W - pad.right - 4} y={y(lastSecond) - 8} textAnchor="end">
            {secondLabel}
          </text>
        )}
        {points.map((p, i) => (
          <g key={`x-${p.date}`}>
            {i % Math.max(1, Math.ceil(n / 6)) === 0 && (
              <text className="chart-axis" x={x(i) + slot / 2} y={height - 24} textAnchor="middle">
                {dayLabel(p.date).split(' ').slice(1).join(' ')}
              </text>
            )}
            <rect x={x(i)} y={pad.top} width={slot} height={innerH} fill="transparent" onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
          </g>
        ))}
        <text className="chart-axis" x={pad.left + innerW / 2} y={height - 6} textAnchor="middle">
          Week ending
        </text>
      </svg>
      <div className="ml-coverage-key">
        <span>
          <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
            <path d="M7,1 l5,6 l-5,6 l-5,-6 z" fill="var(--surface)" stroke="var(--ink)" strokeWidth="1.5" />
          </svg>{' '}
          An ingredient price changed
        </span>
      </div>
      {hover !== null && (
        <div className="tooltip" style={{ left: `${((x(hover) + slot / 2) / W) * 100}%` }}>
          <div className="tooltip-title">Week ending {dayLabel(points[hover].date)}</div>
          <div className="tooltip-row">{label} {points[hover].value != null ? format(points[hover].value as number) : 'not priced'}</div>
          {secondLabel && points[hover].second != null && <div className="tooltip-row">{secondLabel} {format(points[hover].second as number)}</div>}
          {(points[hover].marks ?? []).map((m) => (
            <div key={m} className="tooltip-row">{m} price changed</div>
          ))}
          <div className="tooltip-row">Source: invoices and the recipe</div>
        </div>
      )}
    </div>
  )
}


export type GapPoint = { date: string; expected: number; purchased: number; noise: number }

/**
 * Expected use against what was bought, over weekly 28-day windows. The band above Expected is one typical
 * delivery of slack; purchases above the band are hatched, so the gap reads without colour. Both lines are named
 * on the chart.
 */
export function GapLines({ points, format, unit, height = 280 }: { points: GapPoint[]; format: (v: number) => string; unit: string; height?: number }) {
  const [hover, setHover] = useState<number | null>(null)
  const [ref, W] = useElementWidth<HTMLDivElement>()
  const pad = { top: 24, right: 96, bottom: 44, left: 64 }
  const innerW = W - pad.left - pad.right
  const innerH = height - pad.top - pad.bottom
  const n = points.length
  if (n === 0) return <div ref={ref} />
  const max = niceMax(Math.max(1, ...points.flatMap((p) => [p.purchased, p.expected + p.noise])))
  const x = (i: number) => pad.left + (n <= 1 ? innerW / 2 : (i / (n - 1)) * innerW)
  const y = (v: number) => pad.top + innerH - (Math.max(0, v) / max) * innerH
  const line = (pick: (p: GapPoint) => number) => points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(pick(p)).toFixed(1)}`).join(' ')
  const area = (top: (p: GapPoint) => number, bottom: (p: GapPoint) => number) =>
    `${points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(top(p)).toFixed(1)}`).join(' ')} ${[...points].reverse().map((p, i) => `L${x(n - 1 - i).toFixed(1)},${y(bottom(p)).toFixed(1)}`).join(' ')} Z`
  const last = points[n - 1]
  const slot = innerW / Math.max(1, n - 1)
  const labelGap = Math.abs(y(last.purchased) - y(last.expected)) < 14 ? 14 : 0
  return (
    <div className="chart" ref={ref}>
      <svg width={W} height={height} viewBox={`0 0 ${W} ${height}`} role="img" aria-label={`Expected use and purchased quantity over ${n} weekly windows, in ${unit}`}>
        <defs>
          <pattern id="gap-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="6" stroke="var(--danger)" strokeWidth="1.5" />
          </pattern>
        </defs>
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line className="chart-grid" x1={pad.left} x2={W - pad.right} y1={y(max * f)} y2={y(max * f)} />
            <text className="chart-axis" x={pad.left - 8} y={y(max * f) + 4} textAnchor="end">
              {format(max * f)}
            </text>
          </g>
        ))}
        <text className="chart-axis" transform={`translate(14 ${pad.top + innerH / 2}) rotate(-90)`} textAnchor="middle">
          {unit}
        </text>
        <path d={area((p) => p.expected + p.noise, (p) => p.expected)} fill="var(--chart-1)" opacity={0.14} />
        <path d={area((p) => Math.max(p.purchased, p.expected + p.noise), (p) => p.expected + p.noise)} fill="url(#gap-hatch)" />
        <path d={line((p) => p.expected)} fill="none" stroke="var(--chart-1)" strokeWidth={2.5} />
        <path d={line((p) => p.purchased)} fill="none" stroke="var(--ink)" strokeWidth={2} strokeDasharray="7 4" />
        {points.map((p, i) => (
          <g key={p.date}>
            <circle cx={x(i)} cy={y(p.purchased)} r={3.5} fill="var(--ink)" />
            <circle cx={x(i)} cy={y(p.expected)} r={3.5} fill="var(--chart-1)" />
            <text className="chart-axis" x={x(i)} y={height - 24} textAnchor="middle">
              {dayLabel(p.date).split(' ').slice(1).join(' ')}
            </text>
            <rect x={x(i) - slot / 2} y={pad.top} width={slot} height={innerH} fill="transparent" onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
          </g>
        ))}
        <text className="chart-label" x={W - pad.right + 8} y={y(last.purchased) + 4 - (last.purchased >= last.expected ? labelGap : -labelGap)}>
          Purchased
        </text>
        <text className="chart-label" x={W - pad.right + 8} y={y(last.expected) + 4 + (last.purchased >= last.expected ? labelGap : -labelGap)}>
          Expected use
        </text>
        <text className="chart-axis" x={pad.left + innerW / 2} y={height - 6} textAnchor="middle">
          28 days ending
        </text>
      </svg>
      <div className="ml-coverage-key">
        <span><span className="ml-key-fill" /> One typical delivery</span>
        <span><span className="ml-key-under" /> Unexplained use</span>
      </div>
      {hover !== null && (
        <div className="tooltip" style={{ left: `${(x(hover) / W) * 100}%` }}>
          <div className="tooltip-title">28 days ending {dayLabel(points[hover].date)}</div>
          <div className="tooltip-row">Purchased {format(points[hover].purchased)}</div>
          <div className="tooltip-row">Expected use {format(points[hover].expected)}</div>
          <div className="tooltip-row">One typical delivery {format(points[hover].noise)}</div>
          <div className="tooltip-row">Source: invoices, recipes and sales</div>
        </div>
      )}
    </div>
  )
}

/** Recipe coverage as a bar, with a marker at the level findings need. The figures are written beside it. */
export function CoverageBar({ value, needed, label }: { value: number; needed: number; label: string }) {
  const pct = Math.max(0, Math.min(1, value)) * 100
  return (
    <div className="ml-cover" data-testid="coverage-bar">
      <div className="ml-cover-track" role="img" aria-label={label}>
        <div className="ml-cover-fill" style={{ width: `${pct}%` }} />
        <div className="ml-cover-mark" style={{ left: `${needed * 100}%` }} />
      </div>
      <div className="ml-cover-words">{label}</div>
    </div>
  )
}
