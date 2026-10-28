import type { AuthOptions } from '../api/client'
import { Icon, type IconName } from '../components/Icon'

type Props = {
  /** Null until the server has said what this site offers; nothing is promised before then. */
  options: AuthOptions | null
}

const STEPS: { title: string; body: string }[] = [
  {
    title: 'It reads your invoices',
    body: 'From Bill.com, a forwarded email, a phone photo or a PDF. Every line is matched to one item, with the pack size worked out, so a case from one supplier compares with a case from another.',
  },
  {
    title: 'It asks instead of guessing',
    body: 'When a printed line could be two things, you get one short question with two taps to answer. Five a week at most. Anything it isn’t sure of stays out of the digest.',
  },
  {
    title: 'Every Monday, one decision',
    body: 'What changed, what it cost you, and what to do about it: which items to move between suppliers, which price rises and short deliveries to raise with a rep. With the message to the rep already written.',
  },
]

const AREAS: { icon: IconName; title: string; body: string }[] = [
  {
    icon: 'profit',
    title: 'The weekly digest',
    body: 'Dollars at stake this week in one number, then three lists: switch supplier, price went up, owed to you. Ranked by dollars.',
  },
  {
    icon: 'inventory',
    title: 'Every item, every price',
    body: 'Eight weeks of per-case prices by supplier, and the raw invoice lines as printed, so you can check any number yourself.',
  },
  {
    icon: 'chat',
    title: 'Ask, don’t browse',
    body: '“What did I pay for kale last month.” “Which supplier is cheapest on paper goods.” Two sentences back, with the invoices it used.',
  },
  {
    icon: 'suppliers',
    title: 'Messages to reps, drafted',
    body: 'A price challenge, a credit claim or a short-delivery dispute, in three sentences with the invoice lines attached. You edit it and send it from your own mail app.',
  },
  {
    icon: 'staff',
    title: 'Talking points for the manager meeting',
    body: 'Five lines from the digest in your words, on request.',
  },
  {
    icon: 'approvals',
    title: 'Never on its own',
    body: 'It never places an order, changes a supplier, sends anything, pays anything or alters an invoice. It never claims a market price, only that a price changed or another supplier charged less.',
  },
]

const ASSURANCES: { title: string; body: string }[] = [
  { title: 'Your restaurant only', body: 'Each restaurant’s data is kept apart. Nobody outside your team can see or change it.' },
  { title: 'A bookkeeper can help', body: 'Invite your bookkeeper to answer the agent’s questions across every client they keep. You see the questions as resolved.' },
  { title: 'Two-step sign-in', body: 'Owners and admins sign in with a password and a code from their phone.' },
  { title: 'Yours to take away', body: 'Download everything your restaurant keeps here as spreadsheet files, whenever you like.' },
]

/** One card from the digest, as it looks on a Monday. A picture, not a control. */
function DigestPreview() {
  return (
    <figure className="landing-preview" role="img" aria-label="An example digest card: Kale, green, case, $36.00 a week, with the comparison and a Move kale button">
      <div className="landing-preview-head">
        <span className="badge badge-brand">Switch supplier</span>
        <span className="landing-preview-when">Monday</span>
      </div>
      <div className="landing-preview-title">Kale, green, case</div>
      <p className="landing-preview-why">
        You paid $31.20 at Worldwide on Oct 21. Chef’s Warehouse charged $27.60 on Oct 17.
      </p>
      <dl className="landing-preview-facts">
        <div>
          <dt>A week</dt>
          <dd>$36.00</dd>
        </div>
        <div>
          <dt>At stake this week</dt>
          <dd>$181.90</dd>
        </div>
        <div>
          <dt>Items</dt>
          <dd>7</dd>
        </div>
      </dl>
      <div className="landing-preview-actions">
        <span className="btn btn-primary btn-sm">Move kale</span>
        <span className="btn btn-secondary btn-sm">Not useful</span>
        <span className="landing-preview-status">Nothing is ordered or sent for you</span>
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
            <p className="landing-eyebrow">Price intelligence for small restaurants</p>
            <h1 className="landing-headline">Every week, know what your suppliers changed, what it cost you, and what to do about it.</h1>
            <p className="landing-lead">
              FullHouse reads your supplier invoices, matches every line to one item with the pack size worked out, and
              sends you one digest each Monday: which items to move between suppliers, which price rises and short
              deliveries to raise with a rep. An agent does the reading, matching and drafting. You decide and send.
            </p>
            {actions}
            <p className="landing-note">
              {options?.demo
                ? 'The demo is a sample bistro with eight weeks of invoices from four suppliers. It resets every day, so try anything.'
                : closed
                  ? 'Joining your restaurant’s team? Ask the owner or a manager for an invite link.'
                  : canSignUp
                    ? 'Add one invoice. No account until it is in; your email comes at the end.'
                    : ' '}
            </p>
          </div>
          <DigestPreview />
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
            What you get
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
          <h2 id="landing-start">{canSignUp ? 'Start with one invoice' : 'Pick up where you left off'}</h2>
          <p>
            {canSignUp
              ? 'Connect Bill.com, forward an invoice, or snap a photo of one. The first digest comes the Monday after.'
              : 'Sign in to see this week’s digest.'}
          </p>
          {actions}
        </section>
      </main>

      <footer className="landing-footer">
        <span>FullHouse · supplier prices, read for you, decided by you</span>
        <a href="#signin">Sign in</a>
      </footer>
    </div>
  )
}
