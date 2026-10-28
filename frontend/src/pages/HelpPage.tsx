import { type ReactNode, useState } from 'react'
import { downloadExport, fetchSupportInfo, listFeedback } from '../api/client'
import type { AppContext } from '../app/context'
import { FeedbackButton } from '../components/FeedbackButton'
import { Empty, Loaded } from '../components/PageState'
import { dateTime, relativeTime } from '../lib/format'
import { useLoad } from '../lib/useLoad'

/** Prices, each job in a few steps. Kept in step with docs/USER_GUIDE.md. */
const PRICE_GUIDE: { title: string; steps: ReactNode[] }[] = [
  {
    title: 'Add an invoice',
    steps: [
      <>Open <a href="#invoices">Invoices</a> and press <strong>Add invoice</strong> (the first time, the three choices are the whole page).</>,
      'Pick a Bill.com CSV, paste the text of an emailed invoice, or take a photo and type its lines.',
      'Check the lines as printed, the quantity and the price each, then press Save invoice. The agent matches what it can and asks about the rest.',
    ],
  },
  {
    title: 'Answer the agent’s questions',
    steps: [
      <>Open <a href="#questions">Questions</a>. Each card shows the invoice line and asks one thing: is this the same item, or how many come in a pack.</>,
      'Tap an answer. "Something else" lets you name the item or type the count. Items with an open question stay out of the digest until you answer.',
      'The agent asks at most five questions a week, biggest dollars first.',
    ],
  },
  {
    title: 'Read the digest',
    steps: [
      <>Open <a href="#digest">Digest</a>. The number at the top is the dollars at stake this week.</>,
      'Switch supplier: another supplier charged less for the same item recently. Price went up: the same supplier charges more than before. Owed to you: billed more than delivered, no credit yet.',
      'Tap a card for the item: eight weeks of prices by supplier and every invoice line behind them.',
    ],
  },
  {
    title: 'Draft a message to a supplier',
    steps: [
      <>On a card or an item, press <strong>Draft message</strong>. The subject, three sentences and the invoice lines are filled in.</>,
      'Edit anything. Send via email opens the message in your own mail app after you confirm the recipient; Copy puts it on the clipboard for a text.',
      'Nothing is ever sent for you. The rep’s name and email are remembered for next time.',
    ],
  },
  {
    title: 'Hide an item',
    steps: [
      <>Press <strong>Not useful</strong> on a card. The item leaves the digest for four weeks; Undo brings it back.</>,
      <>Hidden items are still listed under <a href="#items">Items</a> with the date they return.</>,
    ],
  },
  {
    title: 'Let the invoices keep the stock and propose reorders',
    steps: [
      <>An item on the invoices is counted as the Inventory item its name matches; the item page says which (<strong>Change</strong> to pick another, or none).</>,
      'A line on a confirmed invoice is booked into Inventory as a delivery, once, in the agent’s name, with the invoice in the note.',
      <>An item at its reorder point gets a reorder proposed under <a href="#approvals">Recommendations</a> from the cheapest recent supplier, in whole packs, with the cost. Approve it or reject it; nothing is ordered for you.</>,
    ],
  },
  {
    title: 'Connect your POS and payroll for prime cost',
    steps: [
      <>The owner switches it on under <a href="#profile/features">Profile › Features</a>; <strong>Connections</strong> then appears under Restaurant.</>,
      'Connect Square or Gusto, or drop in a CSV export of sales or payroll: the columns are matched from the headers and shown back before anything is imported.',
      'Once a week has sales and labor for every day, the Monday digest opens with prime cost as a share of sales. A missing day shows what to connect instead of a number.',
    ],
  },
  {
    title: 'Ask the agent anything about prices',
    steps: [
      'Type into the field at the top (or the bottom, on a phone): "what did I pay for tomatoes last month", "which supplier is cheapest on paper goods".',
      <>The answer appears in the <a href="#thread">Conversation</a>, with the invoices and items it used underneath. Ask for "talking points" to get five lines for the manager meeting.</>,
    ],
  },
]

/** The everyday jobs: stock, the schedule, the Assistant Manager, undo. */
const GUIDE: { title: string; steps: ReactNode[] }[] = [
  {
    title: 'Log a delivery',
    steps: [
      <>Open <a href="#inventory">Inventory</a> and find the item.</>,
      <>Press <strong>Record change</strong>, then <strong>Delivery received</strong>.</>,
      'Enter how much arrived. For items bought by the case, switch to cases and enter the number of cases.',
      <>Press <strong>Save change</strong>. If the connection drops, just press it again: it won’t be counted twice.</>,
    ],
  },
  {
    title: 'Do a stock count',
    steps: [
      <>In <a href="#inventory">Inventory</a>, press <strong>Record change</strong> on the item, then <strong>Stock count</strong>.</>,
      'Enter what’s actually there. The difference is saved in the item’s history, so nothing is overwritten silently.',
    ],
  },
  {
    title: 'Bring in your existing stock list',
    steps: [
      <>Save your spreadsheet as CSV, then in <a href="#inventory">Inventory</a> press <strong>Import</strong>.</>,
      'Check the preview. Any row with a problem is listed by line number; fix the file and try again. Nothing is saved until every row is right.',
    ],
  },
  {
    title: 'Check and publish the schedule',
    steps: [
      <>Open <a href="#staff">Schedule & staff</a>. Owners and admins add shifts as drafts.</>,
      <>Press <strong>Publish</strong> when it’s ready. Staff see only published shifts.</>,
      'Shifts that break the restaurant’s scheduling rules (too long, too little rest, too many hours) are refused with the reason.',
    ],
  },
  {
    title: 'Review the Assistant Manager’s recommendations',
    steps: [
      <>Open <a href="#approvals">Recommendations</a>. Each one says what it would change and why.</>,
      <><strong>Approve</strong> carries it out; <strong>Reject</strong> drops it. Nothing changes until someone approves. Approving a reorder places the order; the stock moves when it arrives.</>,
      <>Ask it for something any time with <strong>New task</strong>, e.g. “Check stock and reorder anything low”.</>,
    ],
  },
  {
    title: 'Track an order from approval to delivery',
    steps: [
      <>Approving a reorder puts it under <strong>On order</strong> on <a href="#inventory">Inventory</a> with the supplier and the day it’s expected. Orders placed by phone go there too: <strong>Record an order</strong>.</>,
      <><strong>Mark shipped</strong> when the supplier says it’s on its way. An order past its expected date shows as overdue, here and on Today.</>,
      <><strong>Receive</strong> when it arrives: enter what actually came and it is booked into stock, once. If the invoice for it is added first, the invoice receives it instead, and nothing is counted twice.</>,
    ],
  },
  {
    title: 'Undo a mistake',
    steps: [
      <>Open <a href="#history">History & undo</a> to see every change, who made it, and when.</>,
      <>Owners and admins press <strong>Undo</strong> on the change. Approved recommendations can be undone the same way.</>,
    ],
  },
  {
    title: 'Invite someone',
    steps: [
      <>Owners and admins: <a href="#profile/team">Profile › Team access</a>, then make an invite link.</>,
      'Send it however you like. It works once and expires after a week.',
    ],
  },
  {
    title: 'When something’s down',
    steps: [
      'Offline: a red bar says so. You can look around; changes save once you’re back online.',
      'If the AI assistant is unavailable, everything else still works: stock, the schedule, the menu and history.',
      <>Signed out unexpectedly? Sign in again. Anything already saved is kept.</>,
    ],
  },
]

export function HelpPage({ app }: { app: AppContext }) {
  const support = useLoad(fetchSupportInfo, [])
  const feedback = useLoad(() => (app.canManage ? listFeedback() : Promise.resolve([])), [app.canManage])
  const [exporting, setExporting] = useState(false)

  const exportData = async () => {
    setExporting(true)
    try {
      await downloadExport()
      app.toast('Export downloaded', 'A zip of CSV files, one per kind of record.')
    } catch (err) {
      app.toast('Couldn’t export', err instanceof Error ? err.message : String(err))
    } finally {
      setExporting(false)
    }
  }

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Help</h1>
          <p className="page-subtitle">How to add invoices, answer the agent, read the digest, and reach a rep. Plus who to ask, and your data.</p>
        </div>
      </div>

      <div className="stack help">
        <section className="card" aria-labelledby="contact-title">
          <div className="card-header">
            <div>
              <h2 className="card-title" id="contact-title">
                Get help
              </h2>
              <div className="card-subtitle">Stuck, or something looks wrong? Tell us; it goes straight to the people running FullHouse.</div>
            </div>
          </div>
          <div className="card-body stack-sm">
            <Loaded load={support} what="the support contact" placeholderHeight={40}>
              {(info) =>
                info.support_email || info.support_url ? (
                  <div className="row wrap">
                    {info.support_email && (
                      <a className="btn btn-secondary" href={`mailto:${info.support_email}`}>
                        Email {info.support_email}
                      </a>
                    )}
                    {info.support_url && (
                      <a className="btn btn-secondary" href={info.support_url} target="_blank" rel="noreferrer">
                        Support site
                      </a>
                    )}
                  </div>
                ) : (
                  <p className="muted">
                    No support contact has been set up yet. Use Send feedback below, or ask whoever set FullHouse up for you.
                  </p>
                )
              }
            </Loaded>
            <div>
              <FeedbackButton onSent={() => (app.toast('Thanks, feedback sent'), void feedback.reload())} />
            </div>
          </div>
        </section>

        <section className="card" aria-labelledby="guide-title">
          <div className="card-header">
            <div>
              <h2 className="card-title" id="guide-title">
                How to…
              </h2>
            </div>
          </div>
          <div className="card-body guide">
            {GUIDE.map((topic) => (
              <details key={topic.title}>
                <summary>{topic.title}</summary>
                <ol>
                  {topic.steps.map((step, i) => (
                    <li key={i}>{step}</li>
                  ))}
                </ol>
              </details>
            ))}
            <h3 style={{ marginTop: 16 }}>Invoices &amp; prices</h3>
            <p className="muted">
              The sidebar follows an invoice through: it is read on <a href="#invoices">Invoices</a>, the agent asks about
              lines it can’t place on <a href="#questions">Questions</a>, each confirmed line adds to the price history on{' '}
              <a href="#items">Items</a>, the weekly findings go out on <a href="#digest">Digest</a>, and the conversation and
              drafts live on <a href="#thread">Conversation</a>.
            </p>
            {PRICE_GUIDE.map((topic) => (
              <details key={topic.title}>
                <summary>{topic.title}</summary>
                <ol>
                  {topic.steps.map((step, i) => (
                    <li key={i}>{step}</li>
                  ))}
                </ol>
              </details>
            ))}
          </div>
        </section>

        {app.canManage && (
          <section className="card" aria-labelledby="data-title">
            <div className="card-header">
              <div>
                <h2 className="card-title" id="data-title">
                  Your data
                </h2>
                <div className="card-subtitle">
                  Everything {app.restaurant.name ?? 'the restaurant'} keeps here — stock and its history, suppliers, the menu and
                  recipes, the team and schedule, sales, recommendations and the change history — as spreadsheet files. Each
                  download is noted in the security log.
                </div>
              </div>
            </div>
            <div className="card-body">
              <button className="btn btn-secondary" disabled={exporting} onClick={exportData}>
                {exporting ? 'Preparing…' : 'Download all data (.zip)'}
              </button>
            </div>
          </section>
        )}

        {app.canManage && (
          <section className="card" aria-labelledby="team-feedback-title">
            <div className="card-header">
              <div>
                <h2 className="card-title" id="team-feedback-title">
                  Feedback from your team
                </h2>
                <div className="card-subtitle">What people have sent with Send feedback.</div>
              </div>
            </div>
            <Loaded load={feedback} what="feedback">
              {(rows) =>
                rows.length === 0 ? (
                  <Empty title="Nothing yet">When anyone sends feedback, it shows up here.</Empty>
                ) : (
                  <ul className="list" aria-label="Feedback">
                    {rows.map((row) => (
                      <li className="list-item" key={row.id}>
                        <div className="list-item-main">
                          <div className="feedback-message">{row.message}</div>
                          <div className="faint small" title={dateTime(row.created_at)}>
                            {row.sent_by}
                            {row.page && ` · on ${row.page}`} · {relativeTime(row.created_at)}
                          </div>
                        </div>
                      </li>
                    ))}
                  </ul>
                )
              }
            </Loaded>
          </section>
        )}
      </div>
    </main>
  )
}
