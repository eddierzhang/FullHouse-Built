import { useMemo, useState } from 'react'
import { useElementWidth } from '../lib/useElementWidth'
import { money, shortDate } from '../lib/format'

/**
 * Chart conventions, shared by both charts:
 * - one y-axis only; every series here is dollars;
 * - solid hairline gridlines, clean axis ticks;
 * - a hover tooltip on every chart, with hit areas wider than the marks;
 * - text in text colours -- series colour only on marks and legend swatches.
 */

/** Rounds an axis maximum up to a clean 1 / 2 / 2.5 / 5 × 10ⁿ. */
export function niceMax(value: number): number {
  if (value <= 0) return 10
  const magnitude = 10 ** Math.floor(Math.log10(value))
  for (const step of [1, 2, 2.5, 5, 10]) if (value <= step * magnitude) return step * magnitude
  return 10 * magnitude
}

type Series = { key: string; label: string; color: string; values: number[] }

export function LineChart({ dates, series, height = 220 }: { dates: string[]; series: Series[]; height?: number }) {
  const [hover, setHover] = useState<number | null>(null)
  const [ref, W] = useElementWidth<HTMLDivElement>()
  const pad = { top: 16, right: 16, bottom: 28, left: 52 }
  const innerW = W - pad.left - pad.right
  const innerH = height - pad.top - pad.bottom
  const n = dates.length

  const geometry = useMemo(() => {
    const max = niceMax(Math.max(1, ...series.flatMap((s) => s.values)))
    const x = (i: number) => pad.left + (n <= 1 ? innerW / 2 : (i / (n - 1)) * innerW)
    const y = (v: number) => pad.top + innerH - (v / max) * innerH
    return { max, x, y }
  }, [series, n, innerW, innerH, pad.left, pad.top])

  if (n === 0) return <div className="empty" ref={ref}>No data in this window.</div>

  const { max, x, y } = geometry
  const path = (values: number[]) => values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  const last = n - 1
  const band = innerW / Math.max(1, n - 1)

  return (
    <div className="chart" ref={ref}>
      <svg width={W} height={height} viewBox={`0 0 ${W} ${height}`} role="img" aria-label={series.map((s) => s.label).join(' and ') + ' over time'}>
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line className="chart-grid" x1={pad.left} x2={W - pad.right} y1={y(max * f)} y2={y(max * f)} />
            <text className="chart-axis" x={pad.left - 10} y={y(max * f) + 4} textAnchor="end">
              {money(max * f)}
            </text>
          </g>
        ))}

        {series[0] && (
          <path
            d={`${path(series[0].values)} L${x(last)},${pad.top + innerH} L${x(0)},${pad.top + innerH} Z`}
            fill={series[0].color}
            opacity={0.08}
          />
        )}

        {series.map((s) => (
          <path key={s.key} d={path(s.values)} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
        ))}

        {hover !== null && <line className="chart-crosshair" x1={x(hover)} x2={x(hover)} y1={pad.top} y2={pad.top + innerH} />}

        {series.map((s) => (
          <circle key={s.key} cx={x(hover ?? last)} cy={y(s.values[hover ?? last])} r={4} fill={s.color} stroke="#fff" strokeWidth={2} />
        ))}

        {/* Label the endpoint only when it says something; a $0 today reads as a glitch. */}
        {series[0] && hover === null && series[0].values[last] > 0 && (
          <text className="chart-label" x={x(last) - 8} y={y(series[0].values[last]) - 10} textAnchor="end">
            {money(series[0].values[last])}
          </text>
        )}

        <text className="chart-axis" x={pad.left} y={height - 6}>
          {shortDate(dates[0])}
        </text>
        <text className="chart-axis" x={W - pad.right} y={height - 6} textAnchor="end">
          {shortDate(dates[last])}
        </text>

        {dates.map((d, i) => (
          <rect key={d} x={x(i) - band / 2} y={pad.top} width={band} height={innerH} fill="transparent"
            onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
        ))}
      </svg>

      {hover !== null && (
        <div className="tooltip" style={{ left: `${(x(hover) / W) * 100}%` }}>
          <div className="tooltip-title">{shortDate(dates[hover])}</div>
          {series.map((s) => (
            <div key={s.key} className="tooltip-row">
              <span className="legend-swatch" style={{ background: s.color, margin: 0 }} />
              {money(s.values[hover], true)} {s.label.toLowerCase()}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

type ForecastDay = { date: string; revenue: number; low: number; high: number; profit: number }

/**
 * Projected revenue per day: one series, so no legend -- the card title
 * names it. Columns are capped at 24px and grow from the baseline; the
 * whisker is the likely range, drawn in neutral ink so it never reads as a
 * second series.
 */
export function ForecastChart({ days, height = 180 }: { days: ForecastDay[]; height?: number }) {
  const [hover, setHover] = useState<number | null>(null)
  const [ref, W] = useElementWidth<HTMLDivElement>()
  const pad = { top: 16, right: 8, bottom: 26, left: 48 }
  const innerW = W - pad.left - pad.right
  const innerH = height - pad.top - pad.bottom

  if (days.length === 0) return <div ref={ref} />

  const max = niceMax(Math.max(1, ...days.map((d) => d.high)))
  const slot = innerW / days.length
  const barW = Math.min(24, slot * 0.5)
  const y = (v: number) => pad.top + innerH - (v / max) * innerH
  const cx = (i: number) => pad.left + slot * i + slot / 2
  const baseline = pad.top + innerH

  // Square at the baseline, 4px rounded at the data end.
  const bar = (i: number, value: number) => {
    const left = cx(i) - barW / 2
    const top = y(value)
    const r = Math.min(4, (baseline - top) / 2, barW / 2)
    return `M${left},${baseline} L${left},${top + r} Q${left},${top} ${left + r},${top} L${left + barW - r},${top} Q${left + barW},${top} ${left + barW},${top + r} L${left + barW},${baseline} Z`
  }

  return (
    <div className="chart" ref={ref}>
      <svg width={W} height={height} viewBox={`0 0 ${W} ${height}`} role="img" aria-label="Projected revenue per day">
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line className="chart-grid" x1={pad.left} x2={W - pad.right} y1={y(max * f)} y2={y(max * f)} />
            <text className="chart-axis" x={pad.left - 10} y={y(max * f) + 4} textAnchor="end">
              {money(max * f)}
            </text>
          </g>
        ))}

        {days.map((d, i) => (
          <g key={d.date}>
            <path d={bar(i, d.revenue)} fill="var(--chart-1)" opacity={hover === null || hover === i ? 1 : 0.55} />
            {d.high > d.low && (
              <g stroke="var(--text-faint)" strokeWidth={1.5}>
                <line x1={cx(i)} x2={cx(i)} y1={y(d.high)} y2={y(d.low)} />
                <line x1={cx(i) - 5} x2={cx(i) + 5} y1={y(d.high)} y2={y(d.high)} />
                <line x1={cx(i) - 5} x2={cx(i) + 5} y1={y(d.low)} y2={y(d.low)} />
              </g>
            )}
            <text className="chart-axis" x={cx(i)} y={height - 6} textAnchor="middle">
              {new Date(`${d.date}T12:00:00`).toLocaleDateString('en-US', { weekday: 'short' })}
            </text>
            <rect x={pad.left + slot * i} y={pad.top} width={slot} height={innerH} fill="transparent"
              onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
          </g>
        ))}
      </svg>

      {hover !== null && (
        <div className="tooltip" style={{ left: `${(cx(hover) / W) * 100}%` }}>
          <div className="tooltip-title">{shortDate(days[hover].date)}</div>
          <div className="tooltip-row">Revenue {money(days[hover].revenue)}</div>
          <div className="tooltip-row">
            Likely {money(days[hover].low)} – {money(days[hover].high)}
          </div>
          <div className="tooltip-row">Gross profit {money(days[hover].profit)}</div>
        </div>
      )}
    </div>
  )
}
