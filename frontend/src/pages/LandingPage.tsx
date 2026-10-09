import type { AuthOptions } from '../api/client'
import { Icon, type IconName } from '../components/Icon'

type Props = {
  /** Null until the server has said what this site offers; nothing is promised before then. */
  options: AuthOptions | null
}

const STEPS: { title: string; body: string }[] = [
  {
    title: 'It reads your real numbers',
    body: 'Stock on hand, supplier lead times, the schedule, what sold and what it cost. The assistant works from your restaurant’s own data, every day or whenever you ask it something.',
  },
  {
    title: 'It proposes, you decide',
    body: 'Each idea arrives as a recommendation with its reasoning: reorder 25 kg of tomatoes, cover Friday’s late shift, discount a slow seller. Approve it, change it or turn it down.',
  },
  {
    title: 'Everything is on the record',
    body: 'Every change, by a person or the assistant, is kept with who made it and why. Any of them can be undone, and the undo is recorded too.',
  },
]

const AREAS: { icon: IconName; title: string; body: string }[] = [
  {
    icon: 'inventory',
    title: 'Inventory',
    body: 'Days of cover worked out from what you actually sell, set against how long each supplier takes to deliver. Bring in your stock spreadsheet to start.',
  },
  {
    icon: 'suppliers',
    title: 'Suppliers',
    body: 'Contacts and lead times in one place, and reorders suggested before an item runs short rather than after.',
  },
  {
    icon: 'menu',
    title: 'Menu',
    body: 'Dishes costed from their recipes, so margins are computed instead of guessed. Promotions end by themselves when their time is up.',
  },
  {
    icon: 'staff',
    title: 'Staff and schedule',
    body: 'Build the week within your own rules for shift length, rest and weekly hours. Your team signs in to see their published shifts.',
  },
  {
    icon: 'profit',
    title: 'Profit and forecast',
    body: 'Revenue, gross profit and margins from real sales, plus a seven-day forecast that says how much history it rests on.',
  },
  {
    icon: 'approvals',
    title: 'One list of decisions',
    body: 'Everything the assistant suggests waits in a single queue, on your phone or at a desk, until an owner or admin decides.',
  },
]

const ASSURANCES: { title: string; body: string }[] = [
  { title: 'Your restaurant only', body: 'Each restaurant’s data is kept apart. Nobody outside your team can see or change it.' },
  { title: 'Roles for your team', body: 'Owners and admins decide; staff see what they work with. People join by an invite link you create.' },
  { title: 'Two-step sign-in', body: 'Owners and admins sign in with a password and a code from their phone.' },
  { title: 'Yours to take away', body: 'Download everything your restaurant keeps here as spreadsheet files, whenever you like.' },
]

/** One of the assistant's recommendations, as it looks in the queue. A picture, not a control. */
function ProposalPreview() {
  return (
    <figure className="landing-preview" role="img" aria-label="An example recommendation: reorder 25 kg of tomatoes, waiting for approval">
      <div className="landing-preview-head">
        <span className="badge badge-brand">Assistant Manager</span>
        <span className="landing-preview-when">Today, 10:00</span>
      </div>
      <div className="landing-preview-title">Reorder 25 kg of Tomatoes</div>
      <p className="landing-preview-why">
        3 kg left, about a day and a half at this week’s pace. The supplier takes 2 days to deliver, so this order
        shouldn’t wait.
      </p>
      <dl className="landing-preview-facts">
        <div>
          <dt>On hand</dt>
          <dd>3 kg</dd>
        </div>
        <div>
          <dt>Days of cover</dt>
          <dd>1.5</dd>
        </div>
        <div>
          <dt>Lead time</dt>
          <dd>2 days</dd>
        </div>
      </dl>
      <div className="landing-preview-actions">
        <span className="btn btn-primary btn-sm">Approve</span>
        <span className="btn btn-secondary btn-sm">Reject</span>
        <span className="landing-preview-status">Nothing changes until you choose</span>
      </div>
    </figure>
  )
}

/**
 * What a signed-out visitor sees first: what FullHouse is, and the way in.
 * New restaurants create an account here; a restaurant's own team joins
 * through invite links instead, so on a site that isn't taking new
 * restaurants the only way in offered is signing in.
 */
export function LandingPage({ options }: Props) {
  const canSignUp = options?.signup_enabled === true
  const closed = options?.signup_enabled === false

  const actions = (
    <div className="landing-actions">
      {canSignUp && (
        <a className="btn btn-primary landing-cta" href="#signup">
          Set up your restaurant
        </a>
      )}
      <a className={`btn ${canSignUp ? 'btn-secondary' : 'btn-primary'} landing-cta`} href="#signin">
        {options?.demo ? 'Try the demo' : 'Sign in'}
      </a>
    </div>
  )

  return (
    <div className="landing">
      <header className="landing-nav">
        <a className="brand landing-brand" href="#home">
          <span className="brand-mark">F</span>
          FullHouse
        </a>
        <nav className="landing-nav-links" aria-label="Account">
          <a className="btn btn-ghost" href="#signin">
            Sign in
          </a>
          {canSignUp && (
            <a className="btn btn-primary" href="#signup">
              Create account
            </a>
          )}
        </nav>
      </header>

      <main>
        <section className="landing-hero">
          <div>
            <p className="landing-eyebrow">Restaurant operations</p>
            <h1 className="landing-headline">An assistant manager that does the legwork and leaves the decisions to you.</h1>
            <p className="landing-lead">
              FullHouse keeps your stock, suppliers, menu, schedule and profit in one place. Its AI assistant watches
              those numbers and proposes what to do next: reorder before you run out, fill a shift, run a promotion.
              Nothing takes effect until you approve it.
            </p>
            {actions}
            <p className="landing-note">
              {options?.demo
                ? 'The demo is a sample bistro with six weeks of trading. It resets every day, so try anything.'
                : closed
                  ? 'Joining your restaurant’s team? Ask the owner or a manager for an invite link.'
                  : canSignUp
                    ? 'Create an account for your restaurant, answer a few questions about how it runs, and invite your team.'
                    : ' '}
            </p>
          </div>
          <ProposalPreview />
        </section>

        <section className="landing-section" aria-labelledby="landing-how">
          <h2 id="landing-how" className="landing-heading">
            How it works
          </h2>
          <ol className="landing-steps">
            {STEPS.map((step, index) => (
              <li key={step.title} className="landing-step">
                <span className="landing-step-number" aria-hidden="true">
                  {index + 1}
                </span>
                <h3>{step.title}</h3>
                <p>{step.body}</p>
              </li>
            ))}
          </ol>
        </section>

        <section className="landing-section" aria-labelledby="landing-areas">
          <h2 id="landing-areas" className="landing-heading">
            The whole back office, in one place
          </h2>
          <ul className="landing-grid">
            {AREAS.map((area) => (
              <li key={area.title} className="landing-tile">
                <span className="landing-tile-icon">
                  <Icon name={area.icon} size={20} />
                </span>
                <h3>{area.title}</h3>
                <p>{area.body}</p>
              </li>
            ))}
          </ul>
        </section>

        <section className="landing-section" aria-labelledby="landing-trust">
          <h2 id="landing-trust" className="landing-heading">
            Your restaurant stays yours
          </h2>
          <ul className="landing-assurances">
            {ASSURANCES.map((item) => (
              <li key={item.title}>
                <Icon name="check" size={18} className="landing-check" />
                <div>
                  <h3>{item.title}</h3>
                  <p>{item.body}</p>
                </div>
              </li>
            ))}
          </ul>
        </section>

        <section className="landing-band" aria-labelledby="landing-start">
          <h2 id="landing-start">{canSignUp ? 'Ready to hand off the legwork?' : 'Pick up where you left off'}</h2>
          <p>
            {canSignUp
              ? 'Create your restaurant’s account and walk through setup: details, opening hours, stock preferences, staff roles and scheduling rules.'
              : 'Sign in to see what’s waiting for a decision today.'}
          </p>
          {actions}
        </section>
      </main>

      <footer className="landing-footer">
        <span>FullHouse · restaurant operations, with a person in charge</span>
        <a href="#signin">Sign in</a>
      </footer>
    </div>
  )
}
