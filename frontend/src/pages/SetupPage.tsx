import { type FormEvent, useState } from 'react'
import { type Restaurant, SECTIONS, type Section, type SectionValues, type User, completeSetup, saveSection } from '../api/client'
import { Icon } from '../components/Icon'
import { SECTION_META, SectionFields, initialValues, payload, summarize } from '../restaurant/sections'

type Step = Section | 'review'
const STEPS: Step[] = [...SECTIONS, 'review']

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
    if (await save(step)) go(STEPS[index + 1])
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
    if (step !== 'review' && !(await save(step))) return
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
            const done = s !== 'review' && completed.has(s)
            const current = s === step
            const label = s === 'review' ? 'Review' : SECTION_META[s].title
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
              <h2 className="card-title">{step === 'review' ? 'Review and finish' : SECTION_META[step].title}</h2>
              <p className="card-subtitle">
                {step === 'review' ? 'Check everything over. You can change any of it later on the Profile page.' : SECTION_META[step].description}
              </p>
            </div>
          </div>

          <div className="card-body">
            {error && (
              <div className="auth-error setup-error" role="alert">
                {error}
              </div>
            )}

            {step === 'review' ? (
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
                  {busy ? 'Saving…' : 'Save and continue'}
                </button>
              )}
            </div>
          </div>
        </form>
      </div>
    </main>
  )
}
