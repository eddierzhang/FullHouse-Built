import { type FormEvent, type KeyboardEvent, useCallback, useEffect, useMemo, useState } from 'react'
import {
  ApiError,
  type Restaurant,
  type RestaurantDetails,
  type RestaurantHours,
  type Role,
  type Section,
  type SectionValues,
  type User,
  type Invite,
  type SecurityEvent,
  canEditRestaurant,
  createInvite,
  createResetLink,
  disableAccess,
  enableAccess,
  fetchRestaurant,
  linkFor,
  listInvites,
  listSecurityEvents,
  listTeam,
  logoutEverywhere,
  revokeInvite,
  saveRestaurantDetails,
  saveSection,
  setTeamRole,
} from '../api/client'
import type { AppContext } from '../app/context'
import { Icon } from '../components/Icon'
import { dateTime, relativeTime } from '../lib/format'
import { DetailsFields, HoursFields, SECTION_META, SectionFields, initialValues, payload, timezones } from '../restaurant/sections'
import { type Errors, normalizeWebsite, validateDetails, validateHours } from '../restaurant/validation'

type Tab = 'details' | 'rules' | 'team' | 'security' | 'account'

const TABS: { id: Tab; label: string; managersOnly?: boolean }[] = [
  { id: 'details', label: 'Restaurant details' },
  { id: 'rules', label: 'Operating rules' },
  { id: 'team', label: 'Team access', managersOnly: true },
  { id: 'security', label: 'Security log', managersOnly: true },
  { id: 'account', label: 'Your account' },
]

/** "#profile/team" -> "team". Anything else, including the old "#settings", opens the details. */
function readTab(): Tab {
  const sub = window.location.hash.replace(/^#\/?/, '').split('/')[1]
  return TABS.some((t) => t.id === sub) ? (sub as Tab) : 'details'
}

const ROLE_LABELS: Record<Role, string> = { owner: 'Owner', admin: 'Admin', member: 'Member' }

/** The restaurant's profile: its details, the rules it runs by, and who may change them. */
export function ProfilePage({ app, user }: { app: AppContext; user: User }) {
  const [tab, setTab] = useState<Tab>(readTab)
  const tabs = TABS.filter((t) => !t.managersOnly || canEditRestaurant(user))
  const current = tabs.some((t) => t.id === tab) ? tab : 'details'

  useEffect(() => {
    const onChange = () => setTab(readTab())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  const open = (next: Tab) => {
    window.location.hash = `#profile/${next}`
  }

  // Arrow keys move between tabs, as for any tab list.
  const onTabKey = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = tabs.findIndex((t) => t.id === current)
    const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0
    if (!step) return
    event.preventDefault()
    const next = tabs[(index + step + tabs.length) % tabs.length].id
    open(next)
    document.getElementById(`profile-tab-${next}`)?.focus()
  }

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Profile</h1>
          <p className="page-subtitle">
            Signed in as {user.name} · {ROLE_LABELS[user.role] ?? user.role}
          </p>
        </div>
      </div>

      <div className="tabs" role="tablist" aria-label="Profile sections" onKeyDown={onTabKey}>
        {tabs.map((t) => (
          <button
            key={t.id}
            id={`profile-tab-${t.id}`}
            className="tab"
            role="tab"
            type="button"
            aria-selected={current === t.id}
            aria-controls={`profile-panel-${t.id}`}
            tabIndex={current === t.id ? 0 : -1}
            onClick={() => open(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div
        className="profile-panel"
        role="tabpanel"
        id={`profile-panel-${current}`}
        aria-labelledby={`profile-tab-${current}`}
      >
        {current === 'details' && <DetailsTab app={app} />}
        {current === 'rules' && <RulesTab app={app} />}
        {current === 'team' && <TeamTab app={app} user={user} />}
        {current === 'security' && <SecurityTab />}
        {current === 'account' && <AccountTab app={app} user={user} />}
      </div>
    </main>
  )
}

// --- Restaurant details -----------------------------------------------------------

type DetailsValues = { details: RestaurantDetails; hours: RestaurantHours }

function valuesOf(restaurant: Restaurant): DetailsValues {
  const initial = initialValues(restaurant)
  return { details: initial.details, hours: initial.hours }
}

/** What the server is sent, and what "unsaved changes" is judged by. */
function bodyOf(values: DetailsValues): RestaurantDetails & RestaurantHours {
  const details = payload('details', values.details)
  return { ...details, website: normalizeWebsite(details.website), ...values.hours }
}

function DetailsTab({ app }: { app: AppContext }) {
  const { setRestaurant } = app
  const [loaded, setLoaded] = useState<Restaurant | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [values, setValues] = useState<DetailsValues | null>(null)
  const [showErrors, setShowErrors] = useState(false)
  const [serverError, setServerError] = useState<{ message: string; conflict: boolean } | null>(null)
  const [busy, setBusy] = useState(false)

  // Fresh from the server every time the tab opens -- not the copy the app
  // loaded at sign-in, which another owner or admin may have changed since.
  const load = useCallback(async () => {
    setLoadError(null)
    try {
      const restaurant = await fetchRestaurant()
      if (!restaurant) {
        setLoadError('This restaurant hasn’t been set up yet.')
        return
      }
      setLoaded(restaurant)
      setValues(valuesOf(restaurant))
      setShowErrors(false)
      setServerError(null)
      setRestaurant(restaurant)
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : String(err))
    }
  }, [setRestaurant])

  useEffect(() => {
    void load()
  }, [load])

  const zones = useMemo(() => timezones(loaded?.timezone ?? 'UTC'), [loaded?.timezone])
  const errors: Errors = useMemo(
    () => (values ? { ...validateDetails(values.details), ...validateHours(values.hours, zones) } : {}),
    [values, zones],
  )
  const dirty = !!loaded && !!values && JSON.stringify(bodyOf(values)) !== JSON.stringify(bodyOf(valuesOf(loaded)))

  // Leaving with unsaved edits asks first.
  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  if (loadError) {
    return (
      <div className="callout callout-danger" role="alert">
        <Icon name="alert" />
        <div>
          <div className="callout-title">Couldn’t load the restaurant’s details</div>
          <div className="callout-body">
            {loadError}{' '}
            <button type="button" className="link-button" onClick={() => void load()}>
              Try again
            </button>
          </div>
        </div>
      </div>
    )
  }
  if (!loaded || !values) return <div className="skeleton" style={{ height: 480 }} aria-busy="true" />

  const readOnly = !loaded.can_edit
  const shown = showErrors ? errors : {}

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (readOnly || !dirty) return
    setServerError(null)
    if (Object.keys(errors).length > 0) {
      setShowErrors(true)
      // After the errors render, put the cursor on the first one.
      requestAnimationFrame(() => document.querySelector<HTMLElement>('.profile-form [aria-invalid="true"]')?.focus())
      return
    }
    setBusy(true)
    try {
      const updated = await saveRestaurantDetails(bodyOf(values), loaded.updated_at)
      setLoaded(updated)
      setValues(valuesOf(updated))
      setShowErrors(false)
      // The dashboard, sidebar and "open now" read this; agents read the
      // server's copy on their next run.
      setRestaurant(updated)
      app.toast('Restaurant details saved')
      app.refresh()
    } catch (err) {
      setServerError({
        message: err instanceof Error ? err.message : String(err),
        conflict: err instanceof ApiError && err.status === 409,
      })
    } finally {
      setBusy(false)
    }
  }

  const problemCount = Object.keys(shown).length

  return (
    <div className="stack">
      {readOnly && (
        <div className="callout callout-info">
          <Icon name="info" />
          <div className="callout-body">You can see these details. Only the owner or an admin can change them.</div>
        </div>
      )}

      <form className="stack profile-form" onSubmit={onSubmit} aria-labelledby="restaurant-details-title" noValidate>
        {serverError && (
          <div className="auth-error" role="alert">
            {serverError.message}
            {serverError.conflict && (
              <>
                {' '}
                <button type="button" className="link-button" onClick={() => void load()}>
                  Reload
                </button>
              </>
            )}
          </div>
        )}
        {problemCount > 0 && (
          <div className="auth-error" role="alert">
            {problemCount === 1 ? 'One entry needs fixing' : `${problemCount} entries need fixing`} before you can save.
          </div>
        )}

        <fieldset className="plain-fieldset" disabled={readOnly || busy}>
          <div className="stack">
            <section className="card">
              <div className="card-header">
                <div>
                  <h2 className="card-title" id="restaurant-details-title">
                    Restaurant details
                  </h2>
                  <p className="card-subtitle">{SECTION_META.details.description}</p>
                </div>
              </div>
              <div className="card-body">
                <DetailsFields
                  value={values.details}
                  onChange={(details) => setValues({ ...values, details })}
                  errors={shown}
                />
              </div>
            </section>

            <section className="card">
              <div className="card-header">
                <div>
                  <h2 className="card-title">{SECTION_META.hours.title}</h2>
                  <p className="card-subtitle">{SECTION_META.hours.description}</p>
                </div>
              </div>
              <div className="card-body">
                <HoursFields value={values.hours} onChange={(hours) => setValues({ ...values, hours })} errors={shown} />
              </div>
            </section>
          </div>
        </fieldset>

        <div className="profile-footer">
          <span className="muted small" title={loaded.updated_by?.email ?? dateTime(loaded.updated_at)}>
            Last updated {relativeTime(loaded.updated_at)}
            {loaded.updated_by && ` by ${loaded.updated_by.name}`}
          </span>
          {!readOnly && (
            <span className="row">
              {dirty && (
                <button
                  type="button"
                  className="btn btn-ghost"
                  disabled={busy}
                  onClick={() => {
                    setValues(valuesOf(loaded))
                    setShowErrors(false)
                    setServerError(null)
                  }}
                >
                  Discard
                </button>
              )}
              <button type="submit" className="btn btn-primary" disabled={busy || !dirty}>
                {busy ? 'Saving…' : 'Save changes'}
              </button>
            </span>
          )}
        </div>
      </form>

      <section className="card">
        <div className="card-header">
          <div>
            <h2 className="card-title">Managed on their own pages</h2>
            <p className="card-subtitle">The restaurant’s people, dishes and stock aren’t part of its details.</p>
          </div>
        </div>
        <ul className="list">
          {(
            [
              ['#staff', 'staff', 'Staff & scheduling', 'The team, their roles and shifts'],
              ['#menu', 'menu', 'Menu', 'Dishes, prices and recipes'],
              ['#inventory', 'inventory', 'Inventory', 'Stock levels and reordering'],
              ['#suppliers', 'suppliers', 'Suppliers', 'Who supplies what, and lead times'],
            ] as const
          ).map(([href, icon, label, hint]) => (
            <li key={href} className="list-item">
              <Icon name={icon} />
              <div className="list-item-main">
                <a className="strong" href={href}>
                  {label}
                </a>
                <div className="muted small">{hint}</div>
              </div>
              <Icon name="arrowRight" size={14} />
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}

// --- Operating rules ----------------------------------------------------------------

const RULE_SECTIONS: Section[] = ['inventory', 'staff', 'scheduling']

function RulesTab({ app }: { app: AppContext }) {
  const readOnly = !app.restaurant.can_edit
  return (
    <div className="stack settings-stack">
      {readOnly && (
        <div className="callout callout-info">
          <Icon name="info" />
          <div className="callout-body">You can see these rules. Only the owner or an admin can change them.</div>
        </div>
      )}
      {RULE_SECTIONS.map((section) => (
        <SettingsCard key={section} section={section} restaurant={app.restaurant} app={app} readOnly={readOnly} />
      ))}
    </div>
  )
}

/** One setup step again, saved on its own. */
function SettingsCard({
  section,
  restaurant,
  app,
  readOnly,
}: {
  section: Section
  restaurant: Restaurant
  app: AppContext
  readOnly: boolean
}) {
  const saved = initialValues(restaurant)[section]
  const [value, setValue] = useState<SectionValues[Section]>(saved)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const dirty = JSON.stringify(payload(section, value)) !== JSON.stringify(payload(section, saved))

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const updated = await saveSection(section, payload(section, value))
      app.setRestaurant(updated)
      setValue(initialValues(updated)[section])
      app.toast('Settings saved', SECTION_META[section].title)
      app.refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const titleId = `settings-${section}`
  return (
    <form className="card" onSubmit={onSubmit} aria-labelledby={titleId} noValidate>
      <div className="card-header">
        <div>
          <h2 className="card-title" id={titleId}>
            {SECTION_META[section].title}
          </h2>
          <p className="card-subtitle">{SECTION_META[section].description}</p>
        </div>
      </div>
      <div className="card-body">
        {error && (
          <div className="auth-error setup-error" role="alert">
            {error}
          </div>
        )}
        <fieldset className="plain-fieldset" disabled={readOnly}>
          <SectionFields section={section} value={value} onChange={setValue} />
        </fieldset>
      </div>
      {!readOnly && (
        <div className="card-footer settings-footer">
          {dirty && (
            <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => (setValue(saved), setError(null))}>
              Discard
            </button>
          )}
          <button type="submit" className="btn btn-primary" disabled={busy || !dirty}>
            {busy ? 'Saving…' : 'Save changes'}
          </button>
        </div>
      )}
    </form>
  )
}

// --- Team access ---------------------------------------------------------------------

/** Mirrors the server: owners manage admins and members; admins manage members; nobody their own account. */
const canManage = (actor: User, target: User) =>
  target.id !== actor.id && target.role !== 'owner' && (actor.role === 'owner' || (actor.role === 'admin' && target.role === 'member'))

/** A link to hand over: shown once, with a copy button. */
function LinkBox({ label, url, note, onDone }: { label: string; url: string; note: string; onDone: () => void }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="callout callout-info" role="status">
      <Icon name="info" />
      <div className="callout-body stack" style={{ gap: 8, flex: 1, minWidth: 0 }}>
        <div className="callout-title">{label}</div>
        <div className="row">
          <input className="input input-sm mono" readOnly aria-label={label} value={url} onFocus={(e) => e.target.select()} style={{ flex: 1 }} />
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => navigator.clipboard?.writeText(url).then(() => setCopied(true)).catch(() => {})}
          >
            {copied ? 'Copied' : 'Copy'}
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onDone}>
            Done
          </button>
        </div>
        <div className="small">{note}</div>
      </div>
    </div>
  )
}

function TeamTab({ app, user }: { app: AppContext; user: User }) {
  const [team, setTeam] = useState<User[] | null>(null)
  const [invites, setInvites] = useState<Invite[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [inviteRole, setInviteRole] = useState<Exclude<Role, 'owner'>>('member')
  const [inviteEmail, setInviteEmail] = useState('')
  const [shown, setShown] = useState<{ label: string; url: string; note: string } | null>(null)
  const isOwner = user.role === 'owner'

  const load = useCallback(() => {
    listTeam()
      .then(setTeam)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
    listInvites().then(setInvites).catch(() => {})
  }, [])
  useEffect(load, [load])

  const act = async (id: string, work: () => Promise<unknown>, done?: string) => {
    setBusyId(id)
    try {
      await work()
      if (done) app.toast(done)
      load()
    } catch (err) {
      app.toast('That didn’t work', err instanceof Error ? err.message : String(err))
    } finally {
      setBusyId(null)
    }
  }

  const change = (person: User, role: Exclude<Role, 'owner'>) =>
    act(person.id, () => setTeamRole(person.id, role), role === 'admin' ? `${person.name} is now an admin` : `${person.name} is now a member`)

  return (
    <div className="stack">
      <section className="card settings-stack">
        <div className="card-header">
          <div>
            <h2 className="card-title">Invite someone</h2>
            <p className="card-subtitle">
              Create a link and send it to them yourself. It works once, for 7 days, and makes an account in this restaurant.
            </p>
          </div>
        </div>
        <div className="card-body stack" style={{ gap: 10 }}>
          <div className="form-row">
            <label className="field">
              <span className="field-label">Access</span>
              <select className="select" value={inviteRole} onChange={(e) => setInviteRole(e.target.value as Exclude<Role, 'owner'>)}>
                <option value="member">Member — sees everything, changes only day-to-day things</option>
                {isOwner && <option value="admin">Admin — can manage the restaurant and team</option>}
              </select>
            </label>
            <label className="field">
              <span className="field-label">Their email (optional)</span>
              <input className="input" type="email" value={inviteEmail} onChange={(e) => setInviteEmail(e.target.value)} />
              <span className="field-hint">If given, only this address can use the link.</span>
            </label>
          </div>
          <div>
            <button
              type="button"
              className="btn btn-primary"
              disabled={busyId === 'invite'}
              onClick={() =>
                act('invite', async () => {
                  const invite = await createInvite(inviteRole, inviteEmail.trim() || null)
                  setInviteEmail('')
                  setShown({
                    label: 'Invite link',
                    url: linkFor('invite', invite.token),
                    note: 'Copy it now: for safety it can’t be shown again. Anyone with the link can join, so send it only to them.',
                  })
                })
              }
            >
              Create invite link
            </button>
          </div>
          {shown && <LinkBox {...shown} onDone={() => setShown(null)} />}
          {invites.length > 0 && (
            <ul className="list" aria-label="Unused invites">
              {invites.map((invite) => (
                <li key={invite.id} className="list-item">
                  <div className="list-item-main">
                    <div className="strong">{invite.email ?? 'Anyone with the link'}</div>
                    <div className="muted small">
                      {ROLE_LABELS[invite.role]} · expires {relativeTime(invite.expires_at)}
                    </div>
                  </div>
                  <button type="button" className="btn btn-ghost btn-sm" disabled={busyId === invite.id} onClick={() => act(invite.id, () => revokeInvite(invite.id), 'Invite cancelled')}>
                    Cancel invite
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <section className="card settings-stack">
        <div className="card-header">
          <div>
            <h2 className="card-title">Who can sign in</h2>
            <p className="card-subtitle">
              Owners and admins can edit the restaurant; members see it but can’t change it.
              {isOwner ? ' Make someone an admin to share the work.' : ' Only an owner can change who is an admin.'} Turning access off signs them out
              everywhere at once.
            </p>
          </div>
        </div>
        {error ? (
          <div className="card-body">
            <div className="auth-error" role="alert">
              {error}
            </div>
          </div>
        ) : !team ? (
          <div className="card-body">
            <div className="skeleton" style={{ height: 120 }} aria-busy="true" />
          </div>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Email</th>
                  <th>Access</th>
                  <th className="actions">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {team.map((person) => (
                  <tr key={person.id}>
                    <td className="strong">
                      {person.name}
                      {person.id === user.id && <span className="muted"> (you)</span>}
                      {person.disabled_at && <span className="badge badge-danger" style={{ marginLeft: 6 }}>Access off</span>}
                    </td>
                    <td className="muted">{person.email}</td>
                    <td>
                      {isOwner && person.role !== 'owner' ? (
                        <select
                          className="select input-sm"
                          aria-label={`Access for ${person.name}`}
                          value={person.role}
                          disabled={busyId === person.id}
                          onChange={(e) => void change(person, e.target.value as Exclude<Role, 'owner'>)}
                        >
                          <option value="admin">Admin</option>
                          <option value="member">Member</option>
                        </select>
                      ) : (
                        ROLE_LABELS[person.role] ?? person.role
                      )}
                    </td>
                    <td className="actions">
                      {canManage(user, person) && (
                        <div className="row" style={{ justifyContent: 'flex-end' }}>
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            disabled={busyId === person.id || !!person.disabled_at}
                            onClick={() =>
                              act(person.id, async () => {
                                const link = await createResetLink(person.id)
                                setShown({
                                  label: `Password reset link for ${person.name}`,
                                  url: linkFor('reset', link.token),
                                  note: 'Works once, for one hour. Give it only to them: whoever opens it sets their password.',
                                })
                              })
                            }
                          >
                            Reset password
                          </button>
                          {person.disabled_at ? (
                            <button type="button" className="btn btn-secondary btn-sm" disabled={busyId === person.id} onClick={() => act(person.id, () => enableAccess(person.id), `${person.name} can sign in again`)}>
                              Turn access on
                            </button>
                          ) : (
                            <button
                              type="button"
                              className="btn btn-ghost btn-sm negative"
                              disabled={busyId === person.id}
                              onClick={() =>
                                window.confirm(`Turn off ${person.name}'s access? They'll be signed out everywhere, right away.`) &&
                                act(person.id, () => disableAccess(person.id), `${person.name}'s access is off`)
                              }
                            >
                              Turn access off
                            </button>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}

// --- Security log -----------------------------------------------------------------

/** "Mozilla/5.0 (Macintosh; ...) Chrome/..." -> "Chrome on Mac", roughly. */
function device(agent: string | null): string {
  if (!agent) return '—'
  const browser = /Edg\//.test(agent) ? 'Edge' : /Chrome\//.test(agent) ? 'Chrome' : /Firefox\//.test(agent) ? 'Firefox' : /Safari\//.test(agent) ? 'Safari' : null
  const os = /iPhone|iPad/.test(agent) ? 'iPhone/iPad' : /Android/.test(agent) ? 'Android' : /Mac OS X/.test(agent) ? 'Mac' : /Windows/.test(agent) ? 'Windows' : /Linux/.test(agent) ? 'Linux' : null
  return browser && os ? `${browser} on ${os}` : agent.slice(0, 40)
}

const WORRYING = new Set(['login_failed', 'login_blocked', 'login_disabled', 'new_device', 'access_denied', 'mfa_failed'])

function SecurityTab() {
  const [rows, setRows] = useState<SecurityEvent[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [onlyWorrying, setOnlyWorrying] = useState(false)

  useEffect(() => {
    listSecurityEvents()
      .then(setRows)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
  }, [])

  const shown = (rows ?? []).filter((r) => !onlyWorrying || WORRYING.has(r.type))

  return (
    <section className="card settings-stack">
      <div className="card-header">
        <div>
          <h2 className="card-title">Security log</h2>
          <p className="card-subtitle">Sign-ins, failed attempts, refused actions and account changes at this restaurant, newest first.</p>
        </div>
        <label className="row small">
          <input type="checkbox" checked={onlyWorrying} onChange={(e) => setOnlyWorrying(e.target.checked)} /> Only things worth a look
        </label>
      </div>
      {error ? (
        <div className="card-body">
          <div className="auth-error" role="alert">
            {error}
          </div>
        </div>
      ) : !rows ? (
        <div className="card-body">
          <div className="skeleton" style={{ height: 120 }} aria-busy="true" />
        </div>
      ) : shown.length === 0 ? (
        <div className="empty">Nothing recorded yet.</div>
      ) : (
        <div className="table-wrap">
          <table className="table" aria-label="Security events">
            <thead>
              <tr>
                <th>When</th>
                <th>What</th>
                <th>Account</th>
                <th>By</th>
                <th>From</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.id}>
                  <td className="muted small" title={dateTime(r.ts)}>
                    {relativeTime(r.ts)}
                  </td>
                  <td>
                    {WORRYING.has(r.type) ? <span className="badge badge-warning">{r.label}</span> : r.label}
                    {typeof r.detail?.path === 'string' && <div className="cell-sub mono">{r.detail.path}</div>}
                  </td>
                  <td>{r.email ?? '—'}</td>
                  <td className="muted">{r.actor_email ?? ''}</td>
                  <td className="muted small">
                    {device(r.user_agent)}
                    {r.ip && <div className="cell-sub mono">{r.ip}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

// --- Your account ---------------------------------------------------------------------

function AccountTab({ app, user }: { app: AppContext; user: User }) {
  const [busy, setBusy] = useState(false)
  return (
    <section className="card settings-stack">
      <div className="card-header">
        <div>
          <h2 className="card-title">Your account</h2>
          <p className="card-subtitle">
            {user.name} · {user.email} · {ROLE_LABELS[user.role] ?? user.role}
          </p>
        </div>
      </div>
      <div className="card-body stack" style={{ gap: 10 }}>
        <div className="strong">Signed in somewhere you shouldn’t be?</div>
        <p className="muted small" style={{ margin: 0 }}>
          Sign out of every browser and device at once, including this one. Forgot your password? Ask the owner or an admin for a reset link.
        </p>
        <div>
          <button
            type="button"
            className="btn btn-secondary"
            disabled={busy}
            onClick={async () => {
              if (!window.confirm('Sign out of every device, including this one?')) return
              setBusy(true)
              try {
                await logoutEverywhere()
                window.location.reload()
              } catch (err) {
                app.toast('Couldn’t sign out everywhere', err instanceof Error ? err.message : String(err))
                setBusy(false)
              }
            }}
          >
            Sign out everywhere
          </button>
        </div>
      </div>
    </section>
  )
}
