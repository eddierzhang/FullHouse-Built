import type { AuthOptions } from "../api/client";
import { Icon, type IconName } from "../components/Icon";

type Props = {
  /** Null until the server has said what this site offers; nothing is promised before then. */
  options: AuthOptions | null;
};

const STEPS: { title: string; body: string }[] = [
  {
    title: "An AI agent reads what you already have",
    body: "Supplier invoices from Bill.com, a forwarded email, a phone photo or a PDF, and sales from your point of sale. Every invoice line is matched to one item, so a case from one supplier compares with a case from another.",
  },
  {
    title: "It finds where the money goes",
    body: "A price that crept up. A short delivery nobody claimed. A dish that now costs too much to make. An order bigger than the week needs. Each one comes with its dollars a week and the invoices behind it.",
  },
  {
    title: "You decide, and you send",
    body: "The AI agent drafts the order, the message to the rep, the new price and the schedule. You approve, edit or dismiss. Nothing reaches a supplier, your staff or your point of sale unless you send it.",
  },
];

const AREAS: { icon: IconName; title: string; body: string }[] = [
  {
    icon: "profit",
    title: "One list of what to fix",
    body: "A weekly digest ranked by dollars: switch supplier, price went up, owed to you. The dollars at stake this week sit in one number at the top.",
  },
  {
    icon: "suppliers",
    title: "Supplier prices, watched",
    body: "Eight weeks of per-case prices by supplier, with the invoice lines as printed. A price challenge or credit claim is drafted for you to send.",
  },
  {
    icon: "inventory",
    title: "Stock and ordering",
    body: "Stock follows deliveries and sales. Orders are drafted from what you sell and what is on hand, in whole cases, before each supplier’s cutoff.",
  },
  {
    icon: "menu",
    title: "What each dish costs",
    body: "Recipes are costed from your latest invoices. When an ingredient rises, you see which dishes pass your food cost target and the price that would fix it.",
  },
  {
    icon: "staff",
    title: "The schedule and labor",
    body: "A draft schedule built from how busy each hour is expected to be, checked against the rules you set. You edit it and publish it.",
  },
  {
    icon: "approvals",
    title: "Inside your limits",
    body: "The AI agent proposes; people approve. It cannot place an order, change a price or publish a schedule on its own. An order goes out by itself only if you switch autopilot on for that supplier, inside limits you set, with time to stop it.",
  },
];

const ASSURANCES: { title: string; body: string }[] = [
  {
    title: "Your restaurant only",
    body: "Each restaurant’s data is kept apart. Nobody outside your team can see or change it.",
  },
  {
    title: "A bookkeeper can help",
    body: "Invite your bookkeeper to answer the agent’s questions across every client they keep. You see the questions as resolved.",
  },
  {
    title: "Two-step sign-in",
    body: "Owners and admins sign in with a password and a code from their phone.",
  },
  {
    title: "Yours to take away",
    body: "Download everything your restaurant keeps here as spreadsheet files, whenever you like.",
  },
];

/** The card shown when the site has no demo to draw one from: one item, with figures that add up. */
const SAMPLE_CARD = {
  item_name: "Tomatoes, 25 lb case",
  sentence: "You paid $31.20 at Green Valley Farms on Monday. Sysco Foods charged $27.60 the day before.",
  impact: 126,
  total: 641.75,
  items: 6,
};

const dollars = (n: number) => n.toLocaleString("en-US", { style: "currency", currency: "USD" });

/** One card from the digest, as it looks on a Monday. A picture, not a control. On the demo it is the demo's own top card. */
function DigestPreview({ card }: { card?: AuthOptions["demo_card"] }) {
  const shown = card ?? SAMPLE_CARD;
  const short = shown.item_name.split(",")[0].toLowerCase();
  return (
    <figure
      className="landing-preview"
      role="img"
      aria-label={`An example digest card: ${shown.item_name}, ${dollars(shown.impact)} a week, with the comparison and a Move ${short} button`}
    >
      <div className="landing-preview-head">
        <span className="badge badge-brand">Switch supplier</span>
        <span className="landing-preview-when">Monday</span>
      </div>
      <div className="landing-preview-title">{shown.item_name}</div>
      <p className="landing-preview-why">{shown.sentence}</p>
      <dl className="landing-preview-facts">
        <div>
          <dt>A week</dt>
          <dd>{dollars(shown.impact)}</dd>
        </div>
        <div>
          <dt>At stake this week</dt>
          <dd>{dollars(shown.total)}</dd>
        </div>
        <div>
          <dt>Items</dt>
          <dd>{shown.items}</dd>
        </div>
      </dl>
      <div className="landing-preview-actions">
        <span className="btn btn-primary btn-sm">Move {short}</span>
        <span className="btn btn-secondary btn-sm">Not useful</span>
        <span className="landing-preview-status">
          Nothing is sent unless you say so
        </span>
      </div>
    </figure>
  );
}

/**
 * What a signed-out visitor sees first: what FullHouse is, and the way in.
 * New restaurants create an account here; a restaurant's own team joins
 * through invite links instead, so on a site that isn't taking new
 * restaurants the only way in offered is signing in.
 */
export function LandingPage({ options }: Props) {
  const canSignUp = options?.signup_enabled === true;
  const closed = options?.signup_enabled === false;

  const actions = (
    <div className="landing-actions">
      {canSignUp && (
        <a className="btn btn-primary landing-cta" href="#signup">
          Set up your restaurant
        </a>
      )}
      <a
        className={`btn ${canSignUp ? "btn-secondary" : "btn-primary"} landing-cta`}
        href="#signin"
      >
        {options?.demo ? "Try the demo" : "Sign in"}
      </a>
    </div>
  );

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
        <div className="landing-hero-wrap">
          <section className="landing-hero">
            <div>
              <p className="landing-eyebrow">
                The back office for independent restaurants
              </p>
              <h1 className="landing-headline">
                Run the back of the house in one place, and{" "}
                <em>keep more of every dollar.</em>
              </h1>
              <p className="landing-lead">
                FullHouse keeps your invoices, stock, orders, menu costs and
                schedule together, and each week shows where money is slipping
                away: a supplier price that rose, a dish that costs too much to
                make, an order bigger than the week needs. Its AI agent, the
                Assistant Manager, reads the invoices, watches stock and sales,
                and drafts the orders, messages and schedules. You decide and
                send.
              </p>
              {actions}
              <p className="landing-note">
                {options?.demo
                  ? "The demo is a sample bistro with eight weeks of invoices from four suppliers. It resets every day, so try anything."
                  : closed
                    ? "Joining your restaurant’s team? Ask the owner or a manager for an invite link."
                    : canSignUp
                      ? "Add one invoice. No account until it is in; your email comes at the end."
                      : " "}
              </p>
            </div>
            <DigestPreview card={options?.demo_card} />
          </section>
        </div>

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
          <h2 id="landing-start">
            {canSignUp
              ? "Start with one invoice"
              : "Pick up where you left off"}
          </h2>
          <p>
            {canSignUp
              ? "Connect Bill.com, forward an invoice, or snap a photo of one. The first list of what to fix comes the Monday after."
              : "Sign in to see this week’s digest."}
          </p>
          {actions}
        </section>
      </main>

      <footer className="landing-footer">
        <span>FullHouse · the restaurant back office that helps you keep more of what you earn</span>
        <a href="#signin">Sign in</a>
      </footer>
    </div>
  );
}
