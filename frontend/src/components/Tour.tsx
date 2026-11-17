import { useEffect, useState } from 'react'
import { Icon, type IconName } from './Icon'

const STEPS: { title: string; body: string; href: string; go: string; icon: IconName }[] = [
  { title: 'Start with the digest', body: 'Once a week: the dollars at stake, then which items to move to a cheaper supplier, which prices went up, and what you are owed.', href: '#digest', go: 'Open the digest', icon: 'profit' },
  { title: 'Answer a question', body: 'When the agent can’t tell what a printed line is, it asks. Two taps, five a week at most, and it never asks the same thing twice.', href: '#questions', go: 'See the questions', icon: 'approvals' },
  { title: 'Approve what it recommends', body: 'Reorders, recipes and schedule fixes wait for you. Nothing changes until you approve it, and anything approved can be undone.', href: '#approvals', go: 'Open recommendations', icon: 'check' },
  { title: 'Test a decision first', body: 'See when an item runs out, what a supplier switch saves in a year, or what a price rise does to a dish, on your own numbers.', href: '#tools', go: 'Test one', icon: 'trend' },
  { title: 'Find anything', body: 'Press Ctrl or ⌘ K, or the search button, and type an item, a supplier, an invoice or a page. Anything else goes to the assistant as a question.', href: '', go: 'Done', icon: 'search' },
]

const KEY = 'fullhouse.tour.seen'
/** Whether this browser has been shown the tour. Storage may be unavailable; then it is shown once per visit. */
export function tourSeen(): boolean {
  try {
    return window.localStorage.getItem(KEY) === '1'
  } catch {
    return false
  }
}
function markSeen() {
  try {
    window.localStorage.setItem(KEY, '1')
  } catch {
    /* private window: the tour simply shows again next time */
  }
}
export const TOUR_EVENT = 'fullhouse:show-tour'
/** Replay the tour from anywhere (Help has the button). */
export const showTour = () => window.dispatchEvent(new Event(TOUR_EVENT))

/** Five cards, about a minute: what to look at first. Skippable at every step, and replayable from Help. */
export function Tour({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [step, setStep] = useState(0)
  useEffect(() => {
    if (open) setStep(0)
  }, [open])
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && finish()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }) // eslint-disable-line react-hooks/exhaustive-deps

  if (!open) return null
  const finish = () => {
    markSeen()
    onClose()
  }
  const current = STEPS[step]
  const last = step === STEPS.length - 1

  return (
    <>
      <div className="overlay" onClick={finish} />
      <div className="modal-wrap" onClick={(e) => e.target === e.currentTarget && finish()}>
        <div className="modal tour" role="dialog" aria-modal="true" aria-labelledby="tour-title">
          <div className="modal-header">
            <div className="tour-step">
              {step + 1} of {STEPS.length}
            </div>
            <h2 className="tour-title" id="tour-title">
              <span className="tour-icon">
                <Icon name={current.icon} size={18} />
              </span>
              {current.title}
            </h2>
          </div>
          <div className="modal-body">
            <p className="tour-body">{current.body}</p>
            <div className="tour-dots" aria-hidden="true">
              {STEPS.map((s, i) => (
                <span key={s.title} className={i === step ? 'on' : ''} />
              ))}
            </div>
          </div>
          <div className="modal-footer tour-footer">
            <button className="btn btn-ghost" onClick={finish}>
              {last ? 'Close' : 'Skip'}
            </button>
            <div className="row">
              {step > 0 && (
                <button className="btn btn-secondary" onClick={() => setStep(step - 1)}>
                  Back
                </button>
              )}
              {current.href && (
                <a className="btn btn-secondary" href={current.href} onClick={finish}>
                  {current.go}
                </a>
              )}
              <button className="btn btn-primary" onClick={() => (last ? finish() : setStep(step + 1))} autoFocus>
                {last ? 'Done' : 'Next'}
              </button>
            </div>
          </div>
        </div>
      </div>
    </>
  )
}
