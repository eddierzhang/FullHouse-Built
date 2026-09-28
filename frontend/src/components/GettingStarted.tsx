import { useState } from 'react'
import { listSuppliers, listTeam } from '../api/client'
import type { AppContext } from '../app/context'
import { useLoad } from '../lib/useLoad'
import { Icon } from './Icon'

type Step = { key: string; label: string; done: boolean; href: string; action: string }

const dismissKey = (restaurantId: string) => `fullhouse:getting-started-dismissed:${restaurantId}`

function readDismissed(restaurantId: string): boolean {
  try {
    return window.localStorage.getItem(dismissKey(restaurantId)) === '1'
  } catch {
    return false
  }
}

/**
 * The first few things a new restaurant does, ticked off as they happen,
 * for owners and admins. It goes away once everything is done, or when
 * dismissed -- it's a nudge, not a gate.
 */
export function GettingStarted({ app }: { app: AppContext }) {
  const [dismissed, setDismissed] = useState(() => readDismissed(app.restaurant.id))
  const team = useLoad(() => listTeam(), [])
  const suppliers = useLoad(() => listSuppliers(), [])

  if (!app.canManage || dismissed || team.status !== 'ready' || suppliers.status !== 'ready') return null

  const steps: Step[] = [
    { key: 'team', label: 'Invite your team', done: (team.data?.length ?? 0) > 1, href: '#profile/team', action: 'Invite' },
    { key: 'stock', label: 'Add or import your inventory', done: app.inventory.length > 0, href: '#inventory', action: 'Go to inventory' },
    { key: 'suppliers', label: 'Add your suppliers', done: (suppliers.data?.length ?? 0) > 0, href: '#suppliers', action: 'Add suppliers' },
    {
      key: 'agent',
      label: 'Ask the Assistant Manager for a stock check',
      done: app.pendingActions.length > 0,
      href: '#agents',
      action: 'Try it',
    },
  ]
  if (steps.every((s) => s.done)) return null
  const left = steps.filter((s) => !s.done).length

  const dismiss = () => {
    setDismissed(true)
    try {
      window.localStorage.setItem(dismissKey(app.restaurant.id), '1')
    } catch {
      // Private browsing: it just comes back next time.
    }
  }

  return (
    <section className="card getting-started" aria-labelledby="getting-started-title">
      <div className="card-header">
        <div>
          <h2 className="card-title" id="getting-started-title">
            Getting started
          </h2>
          <div className="card-subtitle">
            {left} {left === 1 ? 'thing' : 'things'} left to get {app.restaurant.name ?? 'the restaurant'} running on FullHouse.
          </div>
        </div>
        <button className="btn btn-ghost btn-sm" onClick={dismiss}>
          Hide
        </button>
      </div>
      <div className="card-body">
        <ol>
          {steps.map((step) => (
            <li key={step.key}>
              <span className={`step-dot${step.done ? ' done' : ''}`} aria-hidden="true">
                {step.done ? <Icon name="check" size={12} /> : null}
              </span>
              <span className={step.done ? 'done' : undefined} style={{ flex: 1 }}>
                {step.label}
                {step.done && <span className="visually-hidden"> (done)</span>}
              </span>
              {!step.done &&
                (step.key === 'agent' ? (
                  <button className="btn btn-secondary btn-sm" onClick={() => app.newTask()}>
                    {step.action}
                  </button>
                ) : (
                  <a className="btn btn-secondary btn-sm" href={step.href}>
                    {step.action}
                  </a>
                ))}
            </li>
          ))}
        </ol>
      </div>
    </section>
  )
}
