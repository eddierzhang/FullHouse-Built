import { type Restaurant, WEEKDAYS } from '../api/client'
import { DAY_LABELS } from './sections'

const toMinutes = (clock: string) => {
  const [h, m] = clock.split(':').map(Number)
  return h * 60 + m
}

/** Weekday (0 = Monday) and minutes past midnight, on the restaurant's clock. */
function localNow(timezone: string, now: Date): { day: number; minutes: number } {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: timezone,
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(now)
  const part = (type: string) => parts.find((p) => p.type === type)?.value ?? ''
  const day = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].indexOf(part('weekday'))
  return { day, minutes: Number(part('hour')) * 60 + Number(part('minute')) }
}

/**
 * "Open now · closes 22:00", or when it next opens. Mirrors the backend's
 * `is_open_now`, including hours that run past midnight.
 */
export function openStatus(restaurant: Restaurant, now = new Date()): { open: boolean; label: string } {
  const hours = restaurant.opening_hours
  if (!hours) return { open: false, label: 'No opening hours set' }
  const { day, minutes } = localNow(restaurant.timezone, now)
  const entry = (offset: number) => hours[WEEKDAYS[(day + offset + 7) % 7]]

  // Still inside yesterday's late opening?
  const yesterday = entry(-1)
  if (!yesterday.closed && toMinutes(yesterday.close!) <= toMinutes(yesterday.open!) && minutes < toMinutes(yesterday.close!)) {
    return { open: true, label: `Open now · closes ${yesterday.close}` }
  }
  const today = entry(0)
  if (!today.closed) {
    const opens = toMinutes(today.open!)
    const closes = toMinutes(today.close!)
    const overnight = closes <= opens
    if (minutes >= opens && (overnight || minutes < closes)) return { open: true, label: `Open now · closes ${today.close}` }
    if (minutes < opens) return { open: false, label: `Closed · opens ${today.open}` }
  }
  for (let offset = 1; offset <= 7; offset++) {
    const next = entry(offset)
    if (!next.closed) {
      const when = offset === 1 ? 'tomorrow' : DAY_LABELS[WEEKDAYS[(day + offset) % 7]]
      return { open: false, label: `Closed · opens ${when} ${next.open}` }
    }
  }
  return { open: false, label: 'Closed' }
}
