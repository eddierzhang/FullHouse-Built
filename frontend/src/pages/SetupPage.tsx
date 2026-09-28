import { type FormEvent, useState } from 'react'
import {
  type Restaurant,
  SECTIONS,
  type Section,
  type SectionValues,
  type User,
  completeSetup,
  createInvite,
  linkFor,
  saveSection,
} from '../api/client'
import { Icon } from '../components/Icon'
import { ImportInventory } from '../components/ImportInventory'
import { SECTION_META, SectionFields, initialValues, payload, summarize } from '../restaurant/sections'

/** Steps that save nothing of their own and can be skipped: done now, or later from Profile and Inventory. */
type Extra = 'team' | 'import'
type Step = Section | Extra | 'review'
// After the restaurant's details, so it exists by the time invites are made.
const STEPS: Step[] = [...SECTIONS, 'team', 'import', 'review']

const EXTRA: Record<Extra, { title: string; description: string }> = {
  team: {
    title: 'Invite your team',
    description: 'Make a link for each person. They open it, choose a password, and they’re in. Optional: you can do this later from Profile › Team access.',
  },
  import: {
    title: 'Import inventory',
    description: 'Already keep a stock spreadsheet? Bring it in now. Optional: you can add items one by one, or import later, from Inventory.',
  },
}

const isExtra = (step: Step): step is Extra => step === 'team' || step === 'import'
const titleOf = (step: Step) => (step === 'review' ? 'Review' : isExtra(step) ? EXTRA[step].title : SECTION_META[step].title)

type Props = {
  user: User
  restaurant: Restaurant | null
  onSaved: (restaurant: Restaurant) => void
  onFinished: (restaurant: Restaurant) => void
  onSignOut: () => void
}

/** Where to pick up: the first step not yet saved, or the review once all are. */
function resumeAt(restaurant: Restaurant | null): Step {
  return SECTIONS.find((s) => !restaurant?.completed_steps.includes(s)) ?? 'review'
}

/**
 * Set Up Your Restaurant. Each step is saved to the server when the owner
 * continues, so closing the tab loses nothing: signing back in lands on the
 * first unsaved step with everything before it filled in.
 */
export function SetupPage({ user, restaurant, onSaved, onFinished, onSignOut }: Props) {
  const [step, setStep] = useState<Step>(() => resumeAt(restaurant))
  const [values, setValues] = useState<SectionValues>(() => initialValues(restaurant))
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // What the optional steps got done this session, for their button and the review.
  const [invited, setInvited] = useState<{ link: string; role: string; email: string | null }[]>([])
  const [imported, setImported] = useState<string | null>(null)

  const completed = new Set(restaurant?.completed_steps ?? [])
  const index = STEPS.indexOf(step)
  const reachable = (s: Step) => s === step || completed.has(s as Section) || STEPS.indexOf(s) <= STEPS.indexOf(resumeAt(restaurant))

  const go = (next: Step) => {
    setError(null)
    setStep(next)
    window.scrollTo({ top: 0 })
  }

  const save = async (section: Section): Promise<boolean> => {
    setBusy(true)
    setError(null)
    try {
      onSaved(await saveSection(section, payload(section, values[section])))
      return true
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      return false
    } finally {
      setBusy(false)
    }
  }

  const onContinue = async (event: FormEvent) => {
    event.preventDefault()
    if (step === 'review') return
    if (isExtra(step) || (await save(step))) go(STEPS[index + 1])
  }

  const finish = async () => {
    setBusy(true)
    setError(null)
    try {
      onFinished(await completeSetup())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

  const saveAndLeave = async () => {
    if (step !== 'review' && !isExtra(step) && !(await save(step))) return
    onSignOut()
  }

  return (
    <main className="setup-screen">
      <div className="setup-shell">
        <header className="setup-header">
          <div className="brand setup-brand">
            <span className="brand-mark">F</span>
            FullHouse
          </div>
          <span className="faint hide-mobile">Signed in as {user.email}</span>
        </header>

        <h1 className="setup-title">Set up your restaurant</h1>
        <p className="setup-subtitle">
          A few details so your dashboard, inventory, schedules and agents fit how you run things. Each step saves as you go.
        </p>

        <ol className="setup-steps" aria-label="Setup steps">
          {STEPS.map((s, i) => {
            const done = s === 'team' ? invited.length > 0 : s === 'import' ? imported !== null : s !== 'review' && completed.has(s as Section)
            const current = s === step
            const label = titleOf(s)
            return (
              <li key={s} className={`setup-step${current ? ' current' : ''}${done ? ' done' : ''}`}>
                <button
                  type="button"
                  className="setup-step-button"
                  disabled={busy || !reachable(s)}
                  aria-current={current ? 'step' : undefined}
                  // The label is hidden on narrow screens; this keeps a name either way.
                  aria-label={`Step ${i + 1}: ${label}${done ? ' (saved)' : ''}`}
                  onClick={() => go(s)}
                >
                  <span className="setup-step-mark" aria-hidden="true">
                    {done && !current ? <Icon name="check" size={12} /> : i + 1}
                  </span>
                  <span className="setup-step-label">{label}</span>
                </button>
              </li>
            )
          })}
        </ol>

        <form className="card setup-card" onSubmit={onContinue} noValidate>
          <div className="card-header">
            <div>
              <div className="setup-step-count">
                Step {index + 1} of {STEPS.length}
              </div>
              <h2 className="card-title">{step === 'review' ? 'Review and finish' : titleOf(step)}</h2>
              <p className="card-subtitle">
                {step === 'review'
                  ? 'Check everything over. You can change any of it later on the Profile page.'
                  : isExtra(step)
                    ? EXTRA[step].description
                    : SECTION_META[step].description}
              </p>
            </div>
          </div>

          <div className="card-body">
            {error && (
              <div className="auth-error setup-error" role="alert">
                {error}
              </div>
            )}

            {step === 'team' ? (
              <InviteTeam user={user} invited={invited} onInvited={(invite) => setInvited((all) => [...all, invite])} />
            ) : step === 'import' ? (
              imported ? (
                <div className="callout callout-info" role="status">
                  <Icon name="check" />
                  <div className="callout-grow">
                    <div className="callout-title">Inventory imported</div>
                    <div className="callout-body">{imported}</div>
                  </div>
                </div>
              ) : (
                <ImportInventory
                  onImported={(result) =>
                    setImported(
                      `${result.new} ${result.new === 1 ? 'item' : 'items'} added${result.updated ? `, ${result.updated} updated` : ''}.`,
                    )
                  }
                />
              )
            ) : step === 'review' ? (
              <div className="review">
                {SECTIONS.map((section) => (
                  <section className="review-section" key={section} aria-label={SECTION_META[section].title}>
                    <div className="review-head">
                      <h3 className="review-title">{SECTION_META[section].title}</h3>
                      <button type="button" className="link-button" onClick={() => go(section)}>
                        Edit
                      </button>
                    </div>
                    <dl className="review-rows">
                      {summarize(section, values[section]).map(([label, value]) => (
                        <div className="review-row" key={label}>
                          <dt>{label}</dt>
                          <dd>{value}</dd>
                        </div>
                      ))}
                    </dl>
                  </section>
                ))}
                <section className="review-section" aria-label="Team and inventory">
                  <h3 className="review-title">Team and inventory</h3>
                  <dl className="review-rows">
                    <div className="review-row">
                      <dt>Invites</dt>
                      <dd>{invited.length ? `${invited.length} link${invited.length === 1 ? '' : 's'} made` : 'None yet: Profile › Team access, any time'}</dd>
                    </div>
                    <div className="review-row">
                      <dt>Inventory</dt>
                      <dd>{imported ?? 'Not imported: add or import it from Inventory, any time'}</dd>
                    </div>
                  </dl>
                </section>
              </div>
            ) : (
              <SectionFields
                section={step}
                value={values[step]}
                onChange={(value) => setValues((previous) => ({ ...previous, [step]: value }))}
              />
            )}
          </div>

          <div className="card-footer setup-footer">
            <button type="button" className="btn btn-ghost" disabled={busy || index === 0} onClick={() => go(STEPS[index - 1])}>
              Back
            </button>
            <div className="setup-footer-right">
              <button type="button" className="btn btn-secondary" disabled={busy} onClick={saveAndLeave}>
                Save and finish later
              </button>
              {step === 'review' ? (
                <button type="button" className="btn btn-primary" disabled={busy} onClick={finish}>
                  {busy ? 'Finishing…' : 'Finish setup'}
                </button>
              ) : (
                <button type="submit" className="btn btn-primary" disabled={busy}>
                  {busy
                    ? 'Saving…'
                    : step === 'team'
                      ? invited.length ? 'Continue' : 'Skip for now'
                      : step === 'import'
                        ? imported ? 'Continue' : 'Skip for now'
                        : 'Save and continue'}
                </button>
              )}
            </div>
          </div>
        </form>
      </div>
    </main>
  )
}

/** One invite link at a time; the person opens it and chooses their own password. */
function InviteTeam({
  user,
  invited,
  onInvited,
}: {
  user: User
  invited: { link: string; role: string; email: string | null }[]
  onInvited: (invite: { link: string; role: string; email: string | null }) => void
}) {
  // Only an owner can make someone an admin; everyone invited here is otherwise staff.
  const [role, setRole] = useState<'member' | 'admin'>('member')
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState<string | null>(null)

  const make = async () => {
    setBusy(true)
    setError(null)
    try {
      const invite = await createInvite(role, email.trim() || null)
      onInvited({ link: linkFor('invite', invite.token), role, email: invite.email })
      setEmail('')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const copy = async (link: string) => {
    try {
      await navigator.clipboard.writeText(link)
      setCopied(link)
    } catch {
      setError('Couldn’t copy it. Select the link and copy it yourself.')
    }
  }

  return (
    <div className="stack-sm">
      <div className="form-grid">
        <label className="field">
          <span className="field-label">
            Their email <span className="field-optional">(optional)</span>
          </span>
          <input className="input" type="email" maxLength={254} value={email} onChange={(e) => setEmail(e.target.value)} />
          <span className="field-hint">If given, only this email can use the link.</span>
        </label>
        <label className="field">
          <span className="field-label">They’ll be</span>
          <select className="select" value={role} onChange={(e) => setRole(e.target.value as 'member' | 'admin')}>
            <option value="member">Staff: stock, schedule, recommendations</option>
            {user.role === 'owner' && <option value="admin">Admin: everything but ownership</option>}
          </select>
        </label>
      </div>
      <div className="row">
        <button type="button" className="btn btn-secondary" disabled={busy} onClick={make}>
          <Icon name="plus" size={16} /> {busy ? 'Making a link…' : 'Make an invite link'}
        </button>
      </div>
      {error && (
        <div className="auth-error" role="alert">
          {error}
        </div>
      )}
      {invited.length > 0 && (
        <ul className="list invite-links" aria-label="Invite links">
          {invited.map((invite) => (
            <li className="list-item" key={invite.link}>
              <div className="list-item-main">
                <div className="strong">
                  {invite.email ?? 'Anyone with the link'} · {invite.role === 'admin' ? 'Admin' : 'Staff'}
                </div>
                <input className="input input-sm" readOnly value={invite.link} aria-label="Invite link" onFocus={(e) => e.target.select()} />
              </div>
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => copy(invite.link)}>
                {copied === invite.link ? 'Copied' : 'Copy'}
              </button>
            </li>
          ))}
        </ul>
      )}
      <p className="faint small">Links work once and expire after a week. Send them however you like: text, email, WhatsApp.</p>
    </div>
  )
}
