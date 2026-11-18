import type { AuthOptions } from "../api/client";
import { Icon, type IconName } from "../components/Icon";

type Props = {
  /** Null until the server has said what this site offers; nothing is promised before then. */
  options: AuthOptions | null;
};

const STEPS: { title: string; body: string }[] = [
  {
    title: "An AI agent reads what you already have",
    body: "Supplier invoices from Bill.com, a forwarded email, a phone photo or a PDF. Sales, time punches and delivery payouts from your point of sale or a spreadsheet export. Nothing has to be counted or typed in twice.",
  },
  {
    title: "It works out what each thing costs you",
    body: "What every dish costs to make at this week’s prices. Which ingredients are bought faster than recipes use them. What each item keeps on each delivery app. How many people each hour of the week needs.",
  },
  {
    title: "It drafts what to do about it",
    body: "A new menu price, an order sized to the forecast, a prep sheet, a message to a rep, next week’s schedule. Each comes with its dollars a week and the working behind every number.",
  },
  {
    title: "You decide, and you ask",
    body: "Approve, edit or dismiss, with ten seconds to undo. Nothing reaches a supplier, your staff, your point of sale or a delivery app unless a person sends it.",
  },
];

const AREAS: { icon: IconName; title: string; body: string }[] = [
  {
    icon: "profit",
    title: "One list of what to fix",
    body: "A weekly digest ranked by dollars: switch supplier, price went up, owed to you, dishes over target. The dollars at stake sit in one number at the top.",
  },
  {
    icon: "suppliers",
    title: "Supplier prices, watched",
    body: "Eight weeks of per-case prices by supplier, with the invoice lines as printed. A price challenge or a credit claim is drafted for you to send.",
  },
  {
    icon: "menu",
    title: "Menu costs",
    body: "Every dish is recosted the moment an ingredient price changes. You see which ones pass your food cost target, what that costs a week, and the price that would fix it.",
  },
  {
    icon: "trend",
    title: "Usage, without counting",
    body: "What recipes say was used against what was bought, ingredient by ingredient. The gap is ranked in dollars a week, with three checks to find its cause.",
  },
  {
    icon: "inventory",
    title: "Ordering and prep",
    body: "Orders drafted from the sales forecast, in whole cases, before each supplier’s cutoff. A prep sheet each morning with a par for every item, ready to print.",
  },
  {
    icon: "staff",
    title: "Labor and the schedule",
    body: "People needed by the half hour, from expected sales and your own standards. A draft schedule checked against the rules you set, then cut or extend suggestions during service.",
  },
  {
    icon: "store",
    title: "Delivery channels",
    body: "What each item keeps on each delivery app after commission, fees, packaging and food cost, and the price at which it stops losing money.",
  },
  {
    icon: "clock",
    title: "Stock, deliveries and waste",
    body: "Stock follows deliveries and sales. Check a delivery in against its order, log waste in three taps, and count a shelf from your phone when you want to.",
  },
  {
    icon: "chat",
    title: "Ask, don’t browse",
    body: "“What did I pay for kale last month.” “Which supplier is cheapest on paper goods.” Two sentences back, with the invoices it used.",
  },
];

const ASSURANCES: { title: string; body: string }[] = [
  {
    title: "A person always sends",
    body: "The AI agent proposes; people approve. It cannot place an order, change a price, publish a schedule or message your staff on its own.",
  },
  {
    title: "Every number shows its working",
    body: "Click any figure to see the formula, each input and the invoice or recipe it came from. With too little data, it says so instead of guessing.",
  },
  {
    title: "Your restaurant only",
    body: "Each restaurant’s data is kept apart. Nobody outside your team can see or change it.",
  },
  {
    title: "Wages stay private",
    body: "Hourly rates are seen only by owners and managers. Everyone else sees hours, never a rate or a cost.",
  },
  {
    title: "Your rules, not ours",
    body: "Labor rules, targets and limits are yours to set, and nothing is enforced until you confirm it. FullHouse gives no legal advice.",
  },
  {
    title: "Yours to take away",
    body: "Download everything your restaurant keeps here as spreadsheet files, whenever you like. Owners sign in with a password and a code from their phone.",
  },
];

/**
 * Three things FullHouse might put in front of an owner in one week, each from a different part of the back
 * office: dishes that cost too much to make, a slow night with one server too many, and a supplier charging
 * more than another. Example figures that add up; a picture, not a control.
 */
const EXAMPLES: { badge: string; when: string; title: string; why: string; saves: string; action: string }[] = [
  {
    badge: "Menu cost",
    when: "Tuesday",
    title: "3 dishes are over your food cost target",
    why: "Ingredient prices rose on this week’s invoices. Three dishes now cost more than 30% of their menu price to make.",
    saves: "$212 a week",
    action: "Review prices",
  },
  {
    badge: "Labor",
    when: "Friday, 7:15 pm",
    title: "Cut one server at 7:45",
    why: "Covers are running 12% below forecast, and four servers are on where three are needed.",
    saves: "$42 tonight",
    action: "Accept",
  },
  {
    badge: "Switch supplier",
    when: "Monday",
    title: "Tomatoes, 25 lb case",
    why: "You paid $31.20 at Green Valley Farms. Sysco Foods charged $27.60 the day before.",
    saves: "$126 a week",
    action: "Move tomatoes",
  },
];

/** The first example as a full card, the others as short rows under it. */
function ExamplesPreview() {
  const [first, ...rest] = EXAMPLES;
  return (
    <figure
      className="landing-preview"
      role="img"
      aria-label={`Examples of what FullHouse raises in a week: ${EXAMPLES.map((e) => `${e.title}, ${e.saves}`).join("; ")}`}
    >
      <div className="landing-preview-head">
        <span className="badge badge-brand">{first.badge}</span>
        <span className="landing-preview-when">{first.when}</span>
      </div>
      <div className="landing-preview-title">{first.title}</div>
      <p className="landing-preview-why">{first.why}</p>
      <dl className="landing-preview-facts">
        <div>
          <dt>Costing you</dt>
          <dd>{first.saves}</dd>
        </div>
        <div>
          <dt>Dishes over target</dt>
          <dd>3 of 24</dd>
        </div>
        <div>
          <dt>Largest price rise</dt>
          <dd>9%</dd>
        </div>
      </dl>
      <div className="landing-preview-actions">
        <span className="btn btn-primary btn-sm">{first.action}</span>
        <span className="btn btn-secondary btn-sm">Dismiss</span>
        <span className="landing-preview-status">Nothing is sent unless you say so</span>
      </div>
      <ul className="landing-preview-more">
        {rest.map((example) => (
          <li key={example.title}>
            <div>
              <span className="badge badge-brand">{example.badge}</span>
              <span className="landing-preview-when">{example.when}</span>
            </div>
            <div className="landing-preview-row">
              <span>
                <strong>{example.title}</strong>
                <span className="landing-preview-small">{example.why}</span>
              </span>
              <span className="landing-preview-saves">{example.saves}</span>
            </div>
          </li>
        ))}
      </ul>
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
                ask.
              </p>
              {actions}
              <p className="landing-note">
                {options?.demo
                  ? "The demo is a sample bistro with eight weeks of invoices, stock and orders. It resets every day, so try anything."
                  : closed
                    ? "Joining your restaurant’s team? Ask the owner or a manager for an invite link."
                    : canSignUp
                      ? "Add one invoice. No account until it is in; your email comes at the end."
                      : " "}
              </p>
            </div>
            <ExamplesPreview />
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
              ? "Connect Bill.com, forward an invoice, or snap a photo of one. The first list of what to fix comes the Monday after; costs, ordering and labor switch on as their data arrives."
              : "Sign in to see what needs your attention this week."}
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
