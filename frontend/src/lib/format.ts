const currency = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
const currencyCents = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 })

export const money = (value: number, cents = false) => (cents ? currencyCents : currency).format(value)

/** Drops the trailing ".0" a whole-number quantity would otherwise show. */
export const qty = (value: number) => (Number.isInteger(value) ? String(value) : value.toFixed(1))

export const pct = (value: number, digits = 1) => `${value.toFixed(digits)}%`

/** "2026-10-01" (a calendar date, not an instant) -> "Oct 1". */
export function shortDate(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number)
  return new Date(y, m - 1, d).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

/** "2026-10-01" -> "Thu, Oct 1". */
export function longDate(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number)
  return new Date(y, m - 1, d).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
}

export const dateTime = (iso: string) =>
  new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })

export function relativeTime(iso: string, now = Date.now()): string {
  const seconds = Math.round((new Date(iso).getTime() - now) / 1000)
  const abs = Math.abs(seconds)
  const [value, unit] =
    abs < 60 ? [seconds, 'second'] : abs < 3600 ? [seconds / 60, 'minute'] : abs < 86400 ? [seconds / 3600, 'hour'] : [seconds / 86400, 'day']
  return new Intl.RelativeTimeFormat('en-US', { numeric: 'auto' }).format(Math.round(value), unit as Intl.RelativeTimeFormatUnit)
}

export function duration(startIso: string | null, endIso: string | null): string {
  if (!startIso) return '—'
  const end = endIso ? new Date(endIso).getTime() : Date.now()
  const seconds = Math.max(0, (end - new Date(startIso).getTime()) / 1000)
  if (seconds < 60) return `${seconds.toFixed(0)}s`
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
}

export const timeHm = (value: string) => value.slice(0, 5)

export function greeting(date = new Date()): string {
  const hour = date.getHours()
  return hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'
}

/** "Marketing / Product Agent" -> "MP": punctuation-only words are skipped. */
export const initials = (name: string) =>
  name
    .split(/\s+/)
    .filter((part) => /[a-z0-9]/i.test(part))
    .map((part) => part[0])
    .join('')
    .slice(0, 2)
    .toUpperCase()

export const titleCase = (value: string) => value.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

/** Shared comparator, so every name-sorted list orders the same way. */
export const byName = <T extends { name: string }>(a: T, b: T) => a.name.localeCompare(b.name)

/** Common schedules, so most people never write cron by hand. Schedules run in UTC. */
export const SCHEDULE_PRESETS: { label: string; cron: string | null }[] = [
  { label: 'Not scheduled', cron: null },
  { label: 'Every hour', cron: '0 * * * *' },
  { label: 'Daily at 09:00 UTC', cron: '0 9 * * *' },
  { label: 'Weekdays at 09:00 UTC', cron: '0 9 * * 1-5' },
  { label: 'Mondays at 08:00 UTC', cron: '0 8 * * 1' },
]

export function scheduleLabel(cron: string | null): string {
  if (!cron) return 'Not scheduled'
  return SCHEDULE_PRESETS.find((p) => p.cron === cron)?.label ?? `Custom: ${cron} (UTC)`
}
