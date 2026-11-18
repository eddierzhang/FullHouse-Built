/**
 * How the Margin & Labor pages write numbers and dates (spec section 10.5). One place, so every page
 * says them the same way: dollars without cents at $100 or more, percentages to one decimal, dates as
 * "Tue Nov 17", and a number never without what it is compared with.
 */

/** "$212" at $100 or more, "$42.50" below. Money arrives from the API as a decimal string. */
export function dollars(value: number | string): string {
  const n = typeof value === 'string' ? Number(value) : value
  if (!Number.isFinite(n)) return '—'
  const whole = Math.abs(n) >= 100
  return `${n < 0 ? '−' : ''}$${Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: whole ? 0 : 2, maximumFractionDigits: whole ? 0 : 2 })}`
}

/** A share given as a fraction (0.203) or as percentage points (20.3): "20.3%". */
export function percent(value: number | string, isFraction = true): string {
  const n = typeof value === 'string' ? Number(value) : value
  if (!Number.isFinite(n)) return '—'
  // Half up, as the server rounds: 0.0785 is 7.9%, not the 7.8% binary floats give.
  const tenths = Math.round((isFraction ? n * 1000 : n * 10) + 1e-7)
  return `${tenths < 0 ? '−' : ''}${(Math.abs(tenths) / 10).toFixed(1)}%` // a true minus sign, as dollars use
}

/** "Tue Nov 17", from a business date (YYYY-MM-DD) or a timestamp. Never a raw timestamp. */
export function dayLabel(iso: string): string {
  const date = /^\d{4}-\d{2}-\d{2}$/.test(iso) ? new Date(`${iso}T12:00:00`) : new Date(iso)
  return date.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }).replace(',', '')
}

/** "7:15 pm" or "19:15", by the restaurant's setting. */
export function timeLabel(iso: string, hour12 = true): string {
  return new Date(iso).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12 }).toLowerCase()
}

/** A forecast is always said as a range: "140 to 190 covers, most likely 165". */
export function rangeWords(low: number, expected: number, high: number, unit: string): string {
  const whole = (n: number) => Math.round(n).toLocaleString('en-US')
  return `${whole(low)} to ${whole(high)} ${unit}, most likely ${whole(expected)}`
}

/** Confidence in words, never as a bare percentage: "Estimate (5 weeks of data)" or "Based on 14 weeks of data". */
export function confidenceWords(weeks: number, needed: number): string {
  const unit = weeks === 1 ? 'week' : 'weeks'
  return weeks < needed ? `Estimate (${weeks} ${unit} of data)` : `Based on ${weeks} ${unit} of data`
}

/** Words in a sentence, for the 20-word limit on header sentences. */
export const wordCount = (text: string) => text.trim().split(/\s+/).filter(Boolean).length

/** A quantity in an ingredient's base unit, in the unit a kitchen says it in: "112 kg", "480 ml", "840". */
export function amount(qty: number | string, baseUnit: string): string {
  const n = typeof qty === 'string' ? Number(qty) : qty
  if (!Number.isFinite(n)) return '—'
  const big = Math.abs(n) >= 1000
  const shown = (v: number) => (Math.round(v * 10 + 1e-7 * Math.sign(v)) / 10).toLocaleString('en-US', { maximumFractionDigits: 1 })
  if (baseUnit === 'g') return big ? `${shown(n / 1000)} kg` : `${shown(n)} g`
  if (baseUnit === 'ml') return big ? `${shown(n / 1000)} l` : `${shown(n)} ml`
  return shown(n)
}
