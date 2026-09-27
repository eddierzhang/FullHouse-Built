import { type RestaurantDetails, type RestaurantHours, WEEKDAYS } from '../api/client'
import { DAY_LABELS } from './sections'

// The same rules as backend/app/restaurant/profile_schemas.py, checked
// before sending so mistakes show next to the field at once. The server
// checks again: this is a convenience, not the guard.

/** Field -> problem. Keys are detail fields, "timezone", "hours" (the week), or "hours.<day>". */
export type Errors = Partial<Record<string, string>>

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/
const PHONE = /^[0-9+()\-.\s]{5,40}$/

/** A bare "casaluna.com" gets https:// in front, as the server does. */
export function normalizeWebsite(value: string | null): string | null {
  const trimmed = (value ?? '').trim()
  if (!trimmed) return null
  return trimmed.includes('://') ? trimmed : `https://${trimmed}`
}

function websiteOk(value: string): boolean {
  if (/\s/.test(value) || value.length > 255) return false
  try {
    const url = new URL(value)
    const host = url.hostname.replace(/^\.+|\.+$/g, '')
    return (url.protocol === 'http:' || url.protocol === 'https:') && host.includes('.')
  } catch {
    return false
  }
}

export function validateDetails(v: RestaurantDetails): Errors {
  const errors: Errors = {}
  for (const field of ['name', 'street', 'city', 'country'] as const) {
    if (!v[field].trim()) errors[field] = 'This field is required.'
  }
  const phone = v.phone.trim()
  if (!PHONE.test(phone) || (phone.match(/\d/g)?.length ?? 0) < 5) {
    errors.phone = 'Enter a phone number, like +1 555 123 4567.'
  }
  const email = (v.email ?? '').trim()
  if (email && !EMAIL.test(email)) errors.email = 'Enter a valid email address, or leave it blank.'
  const website = normalizeWebsite(v.website)
  if (website && !websiteOk(website)) errors.website = 'Enter a web address, like https://example.com, or leave it blank.'
  return errors
}

export function validateHours(v: RestaurantHours, knownZones: string[]): Errors {
  const errors: Errors = {}
  if (!knownZones.includes(v.timezone)) errors.timezone = 'Choose a timezone from the list.'

  const week = v.opening_hours
  WEEKDAYS.forEach((day) => {
    const h = week[day]
    if (h.closed) return
    if (!h.open || !h.close) errors[`hours.${day}`] = 'Give an opening and a closing time, or mark the day closed.'
    else if (h.open === h.close) errors[`hours.${day}`] = 'Opening and closing times can’t be the same.'
  })
  if (WEEKDAYS.every((day) => week[day].closed)) {
    errors.hours = 'The restaurant needs to be open at least one day a week.'
  }

  // A day that runs past midnight mustn't run into the next day's opening.
  WEEKDAYS.forEach((day, index) => {
    if (errors[`hours.${day}`]) return
    const today = week[day]
    const nextDay = WEEKDAYS[(index + 1) % 7]
    const tomorrow = week[nextDay]
    if (today.closed || tomorrow.closed || !today.open || !today.close || !tomorrow.open) return
    // "HH:MM" strings compare in clock order.
    if (today.close < today.open && tomorrow.open < today.close) {
      errors[`hours.${day}`] =
        `${DAY_LABELS[day]}’s hours run to ${today.close}, past ${DAY_LABELS[nextDay]}’s opening at ${tomorrow.open}.`
    }
  })
  return errors
}
