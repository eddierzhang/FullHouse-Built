import { type ReactNode, useId, useMemo, useState } from 'react'
import {
  type DayHours,
  type Restaurant,
  type Section,
  type SectionValues,
  WEEKDAYS,
  type Weekday,
} from '../api/client'

// Shared by the setup wizard and the Profile page, so the two can never
// drift apart: a field added here appears in both.

export const DAY_LABELS: Record<Weekday, string> = {
  mon: 'Monday',
  tue: 'Tuesday',
  wed: 'Wednesday',
  thu: 'Thursday',
  fri: 'Friday',
  sat: 'Saturday',
  sun: 'Sunday',
}

export const SECTION_META: Record<Section, { title: string; description: string }> = {
  details: { title: 'Restaurant details', description: 'Your name, address and how guests and suppliers reach you.' },
  hours: { title: 'Timezone & opening hours', description: 'Agents plan around these, and schedules run on your local clock.' },
  inventory: { title: 'Inventory preferences', description: 'How cautious to be about stock, and when to check it.' },
  staff: { title: 'Staff roles', description: 'The positions people can be hired and scheduled into.' },
  scheduling: { title: 'Scheduling rules', description: 'Limits every shift must respect, whoever schedules it.' },
}

const SUGGESTED_ROLES = ['Head Chef', 'Sous Chef', 'Line Cook', 'Dishwasher', 'Server', 'Host', 'Bartender', 'Manager']

function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

/** Saved values where there are any, sensible starting points where not. */
export function initialValues(restaurant: Restaurant | null): SectionValues {
  const r = restaurant
  const defaultDay: DayHours = { closed: false, open: '11:00', close: '22:00' }
  return {
    details: {
      name: r?.name ?? '',
      street: r?.street ?? '',
      city: r?.city ?? '',
      region: r?.region ?? '',
      postal_code: r?.postal_code ?? '',
      country: r?.country ?? '',
      phone: r?.phone ?? '',
      email: r?.email ?? '',
      website: r?.website ?? '',
    },
    hours: {
      timezone: r?.opening_hours ? r.timezone : browserTimezone(),
      opening_hours:
        r?.opening_hours ?? (Object.fromEntries(WEEKDAYS.map((d) => [d, { ...defaultDay }])) as Record<Weekday, DayHours>),
    },
    inventory: r?.inventory_settings ?? { safety_days: 1, default_lead_time_days: 2, daily_stock_check: '08:00' },
    staff: { staff_roles: r?.staff_roles ?? ['Head Chef', 'Line Cook', 'Server', 'Host'] },
    scheduling: r?.scheduling_rules ?? {
      max_shift_hours: 10,
      min_rest_hours: 10,
      max_weekly_hours: 40,
      min_staff_per_open_day: 2,
    },
  }
}

type FieldsProps<S extends Section> = {
  value: SectionValues[S]
  onChange: (value: SectionValues[S]) => void
  /** Problems to show beside each field, keyed as in restaurant/validation.ts. */
  errors?: Partial<Record<string, string>>
}

/** aria props tying an input to its error message, when it has one. */
function useInvalid(errors: FieldsProps<Section>['errors']) {
  const base = useId()
  return (key: string) => ({
    'aria-invalid': errors?.[key] ? true : undefined,
    'aria-describedby': errors?.[key] ? `${base}-${key}` : undefined,
    errorId: `${base}-${key}`,
  })
}

function FieldError({ id, message }: { id: string; message?: string }) {
  return message ? (
    <span className="field-error" id={id}>
      {message}
    </span>
  ) : null
}

function Field({
  label,
  hint,
  optional,
  children,
  wide,
  error,
  errorId,
}: {
  label: string
  hint?: string
  optional?: boolean
  children: ReactNode
  wide?: boolean
  error?: string
  errorId?: string
}) {
  return (
    <label className={`field${wide ? ' field-wide' : ''}`}>
      <span className="field-label">
        {label}
        {optional && <span className="field-optional"> (optional)</span>}
      </span>
      {children}
      {errorId && <FieldError id={errorId} message={error} />}
      {hint && !error && <span className="field-hint">{hint}</span>}
    </label>
  )
}

/** A number input that lets the box be emptied while typing; empty is sent as null. */
function NumberInput({ value, onChange, min, max, step }: { value: number; onChange: (n: number) => void; min: number; max: number; step?: number }) {
  return (
    <input
      className="input"
      type="number"
      inputMode="decimal"
      min={min}
      max={max}
      step={step ?? 1}
      value={Number.isFinite(value) ? value : ''}
      onChange={(e) => onChange(e.target.value === '' ? Number.NaN : Number(e.target.value))}
    />
  )
}

export function DetailsFields({ value, onChange, errors }: FieldsProps<'details'>) {
  const set = (field: keyof SectionValues['details']) => (e: React.ChangeEvent<HTMLInputElement>) =>
    onChange({ ...value, [field]: e.target.value })
  const invalid = useInvalid(errors)
  /** Error wiring for one field: the message for its Field, the aria props for its input. */
  const wire = (key: keyof SectionValues['details']) => {
    const { errorId, ...aria } = invalid(key)
    return { field: { error: errors?.[key], errorId }, input: aria }
  }
  return (
    <div className="form-grid">
      <Field label="Restaurant name" wide {...wire('name').field}>
        <input {...wire('name').input} className="input" required maxLength={120} autoComplete="organization" value={value.name} onChange={set('name')} />
      </Field>
      <Field label="Street address" wide {...wire('street').field}>
        <input {...wire('street').input} className="input" required maxLength={200} autoComplete="street-address" value={value.street} onChange={set('street')} />
      </Field>
      <Field label="City" {...wire('city').field}>
        <input {...wire('city').input} className="input" required maxLength={100} autoComplete="address-level2" value={value.city} onChange={set('city')} />
      </Field>
      <Field label="State / region" optional {...wire('region').field}>
        <input {...wire('region').input} className="input" maxLength={100} autoComplete="address-level1" value={value.region ?? ''} onChange={set('region')} />
      </Field>
      <Field label="Postal code" optional {...wire('postal_code').field}>
        <input {...wire('postal_code').input} className="input" maxLength={20} autoComplete="postal-code" value={value.postal_code ?? ''} onChange={set('postal_code')} />
      </Field>
      <Field label="Country" {...wire('country').field}>
        <input {...wire('country').input} className="input" required maxLength={100} autoComplete="country-name" value={value.country} onChange={set('country')} />
      </Field>
      <Field label="Phone" {...wire('phone').field}>
        <input {...wire('phone').input} className="input" type="tel" required maxLength={40} autoComplete="tel" value={value.phone} onChange={set('phone')} />
      </Field>
      <Field label="Contact email" optional {...wire('email').field}>
        <input {...wire('email').input} className="input" type="email" maxLength={254} autoComplete="email" value={value.email ?? ''} onChange={set('email')} />
      </Field>
      <Field label="Website" optional wide {...wire('website').field}>
        <input {...wire('website').input} className="input" type="url" maxLength={255} placeholder="https://" value={value.website ?? ''} onChange={set('website')} />
      </Field>
    </div>
  )
}

/** The browser's list of IANA zones, with `current` kept in it even if the browser lacks it. */
export function timezones(current: string): string[] {
  let zones: string[] = []
  try {
    zones = Intl.supportedValuesOf('timeZone')
  } catch {
    zones = []
  }
  return zones.includes(current) ? zones : [current, ...zones]
}

export function HoursFields({ value, onChange, errors }: FieldsProps<'hours'>) {
  const zones = useMemo(() => timezones(value.timezone), [value.timezone])
  const invalid = useInvalid(errors)
  const timezoneWiring = invalid('timezone')
  const weekWiring = invalid('hours')
  const setDay = (day: Weekday, patch: Partial<DayHours>) =>
    onChange({ ...value, opening_hours: { ...value.opening_hours, [day]: { ...value.opening_hours[day], ...patch } } })

  return (
    <div className="stack-sm">
      <Field
        label="Timezone"
        hint="Agent schedules and “open now” use this clock."
        error={errors?.timezone}
        errorId={timezoneWiring.errorId}
      >
        <select
          className="select"
          aria-invalid={timezoneWiring['aria-invalid']}
          aria-describedby={timezoneWiring['aria-describedby']}
          value={value.timezone} onChange={(e) => onChange({ ...value, timezone: e.target.value })}>
          {zones.map((zone) => (
            <option key={zone} value={zone}>
              {zone.replace(/_/g, ' ')}
            </option>
          ))}
        </select>
      </Field>

      <fieldset className="hours-table" aria-invalid={weekWiring['aria-invalid']} aria-describedby={weekWiring['aria-describedby']}>
        <legend className="field-label">Opening hours</legend>
        {WEEKDAYS.map((day) => {
          const hours = value.opening_hours[day]
          const dayWiring = invalid(`hours.${day}`)
          const dayAria = { 'aria-invalid': dayWiring['aria-invalid'], 'aria-describedby': dayWiring['aria-describedby'] }
          return (
            <div className={`hours-row${errors?.[`hours.${day}`] ? ' has-error' : ''}`} key={day}>
              <span className="hours-day">{DAY_LABELS[day]}</span>
              <label className="hours-open-toggle">
                <input
                  type="checkbox"
                  checked={!hours.closed}
                  onChange={(e) =>
                    setDay(day, e.target.checked ? { closed: false, open: hours.open ?? '11:00', close: hours.close ?? '22:00' } : { closed: true })
                  }
                />
                Open
              </label>
              {hours.closed ? (
                <span className="muted hours-closed">Closed</span>
              ) : (
                <span className="hours-times">
                  <input
                    className="input input-sm"
                    type="time"
                    aria-label={`${DAY_LABELS[day]} opens`}
                    {...dayAria}
                    value={hours.open ?? ''}
                    onChange={(e) => setDay(day, { open: e.target.value })}
                  />
                  <span className="muted">to</span>
                  <input
                    className="input input-sm"
                    type="time"
                    aria-label={`${DAY_LABELS[day]} closes`}
                    {...dayAria}
                    value={hours.close ?? ''}
                    onChange={(e) => setDay(day, { close: e.target.value })}
                  />
                </span>
              )}
              <FieldError id={dayWiring.errorId} message={errors?.[`hours.${day}`]} />
            </div>
          )
        })}
        <FieldError id={weekWiring.errorId} message={errors?.hours} />
        <p className="field-hint">A closing time earlier than the opening time runs past midnight.</p>
      </fieldset>
    </div>
  )
}

export function InventoryFields({ value, onChange }: FieldsProps<'inventory'>) {
  return (
    <div className="form-grid">
      <Field label="Safety stock (days)" hint="Flag an item when its stock won’t outlast the supplier’s lead time plus this many days.">
        <NumberInput min={0} max={30} value={value.safety_days} onChange={(n) => onChange({ ...value, safety_days: n })} />
      </Field>
      <Field label="Default supplier lead time (days)" hint="New suppliers start with this; change it per supplier later.">
        <NumberInput min={0} max={60} value={value.default_lead_time_days} onChange={(n) => onChange({ ...value, default_lead_time_days: n })} />
      </Field>
      <Field label="Daily stock check" hint="The Inventory Agent reviews stock at this local time. Clear it to skip.">
        <input
          className="input"
          type="time"
          value={value.daily_stock_check ?? ''}
          onChange={(e) => onChange({ ...value, daily_stock_check: e.target.value || null })}
        />
      </Field>
    </div>
  )
}

export function StaffFields({ value, onChange }: FieldsProps<'staff'>) {
  const [draft, setDraft] = useState('')
  const roles = value.staff_roles
  const has = (role: string) => roles.some((r) => r.toLowerCase() === role.trim().toLowerCase())
  const add = (role: string) => {
    const trimmed = role.trim()
    if (!trimmed || has(trimmed)) return
    onChange({ staff_roles: [...roles, trimmed] })
    setDraft('')
  }

  return (
    <div className="stack-sm">
      <ul className="role-list" aria-label="Staff roles">
        {roles.map((role) => (
          <li className="role-chip" key={role}>
            {role}
            <button
              type="button"
              className="role-remove"
              aria-label={`Remove ${role}`}
              onClick={() => onChange({ staff_roles: roles.filter((r) => r !== role) })}
            >
              ×
            </button>
          </li>
        ))}
        {roles.length === 0 && <li className="muted">No roles yet — add at least one.</li>}
      </ul>
      <div className="role-add">
        <input
          className="input"
          aria-label="New role"
          placeholder="Add a role, e.g. Pastry Chef"
          maxLength={64}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              add(draft)
            }
          }}
        />
        <button type="button" className="btn btn-secondary" disabled={!draft.trim() || has(draft)} onClick={() => add(draft)}>
          Add role
        </button>
      </div>
      {SUGGESTED_ROLES.some((r) => !has(r)) && (
        <div className="role-suggestions">
          <span className="field-hint">Suggestions:</span>
          {SUGGESTED_ROLES.filter((r) => !has(r)).map((role) => (
            <button type="button" key={role} className="btn btn-ghost btn-sm" onClick={() => add(role)}>
              + {role}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export function SchedulingFields({ value, onChange }: FieldsProps<'scheduling'>) {
  return (
    <div className="form-grid">
      <Field label="Longest shift (hours)">
        <NumberInput min={1} max={24} step={0.5} value={value.max_shift_hours} onChange={(n) => onChange({ ...value, max_shift_hours: n })} />
      </Field>
      <Field label="Rest between shifts (hours)" hint="Minimum time off between one person’s shifts.">
        <NumberInput min={0} max={24} step={0.5} value={value.min_rest_hours} onChange={(n) => onChange({ ...value, min_rest_hours: n })} />
      </Field>
      <Field label="Weekly hours per person (max)">
        <NumberInput min={1} max={100} step={0.5} value={value.max_weekly_hours} onChange={(n) => onChange({ ...value, max_weekly_hours: n })} />
      </Field>
      <Field label="Staff on every open day (min)" hint="The agents plan schedules to meet this; it isn’t enforced shift by shift.">
        <NumberInput min={0} max={100} value={value.min_staff_per_open_day} onChange={(n) => onChange({ ...value, min_staff_per_open_day: n })} />
      </Field>
    </div>
  )
}

export function SectionFields<S extends Section>({ section, value, onChange }: { section: S } & FieldsProps<S>) {
  // Each branch narrows `section`; the casts only restate what that implies.
  switch (section) {
    case 'details':
      return <DetailsFields value={value as SectionValues['details']} onChange={onChange as FieldsProps<'details'>['onChange']} />
    case 'hours':
      return <HoursFields value={value as SectionValues['hours']} onChange={onChange as FieldsProps<'hours'>['onChange']} />
    case 'inventory':
      return <InventoryFields value={value as SectionValues['inventory']} onChange={onChange as FieldsProps<'inventory'>['onChange']} />
    case 'staff':
      return <StaffFields value={value as SectionValues['staff']} onChange={onChange as FieldsProps<'staff'>['onChange']} />
    default:
      return <SchedulingFields value={value as SectionValues['scheduling']} onChange={onChange as FieldsProps<'scheduling'>['onChange']} />
  }
}

const hoursText = (h: number) => `${h} ${h === 1 ? 'hour' : 'hours'}`
const daysText = (d: number) => `${d} ${d === 1 ? 'day' : 'days'}`

/** Label/value rows for the review screen. */
export function summarize<S extends Section>(section: S, values: SectionValues[S]): [string, string][] {
  switch (section) {
    case 'details': {
      const v = values as SectionValues['details']
      const address = [v.street, v.city, v.region, v.postal_code, v.country].filter(Boolean).join(', ')
      return [
        ['Name', v.name],
        ['Address', address],
        ['Phone', v.phone],
        ['Email', v.email || '—'],
        ['Website', v.website || '—'],
      ]
    }
    case 'hours': {
      const v = values as SectionValues['hours']
      return [
        ['Timezone', v.timezone.replace(/_/g, ' ')],
        ...WEEKDAYS.map((d): [string, string] => {
          const h = v.opening_hours[d]
          return [DAY_LABELS[d], h.closed ? 'Closed' : `${h.open} – ${h.close}`]
        }),
      ]
    }
    case 'inventory': {
      const v = values as SectionValues['inventory']
      return [
        ['Safety stock', daysText(v.safety_days)],
        ['Default lead time', daysText(v.default_lead_time_days)],
        ['Daily stock check', v.daily_stock_check ?? 'Off'],
      ]
    }
    case 'staff':
      return [['Roles', (values as SectionValues['staff']).staff_roles.join(', ')]]
    default: {
      const v = values as SectionValues['scheduling']
      return [
        ['Longest shift', hoursText(v.max_shift_hours)],
        ['Rest between shifts', hoursText(v.min_rest_hours)],
        ['Weekly hours per person', hoursText(v.max_weekly_hours)],
        ['Staff on every open day', String(v.min_staff_per_open_day)],
      ]
    }
  }
}

/** What to send: blank optional details go as null rather than "". */
export function payload<S extends Section>(section: S, values: SectionValues[S]): SectionValues[S] {
  if (section !== 'details') return values
  const v = values as SectionValues['details']
  const blank = (s: string | null) => (s && s.trim() ? s : null)
  return { ...v, region: blank(v.region), postal_code: blank(v.postal_code), email: blank(v.email), website: blank(v.website) } as SectionValues[S]
}
