import { type ReactNode, useState } from 'react'
import { downloadExport, fetchSupportInfo, listFeedback } from '../api/client'
import type { AppContext } from '../app/context'
import { FeedbackButton } from '../components/FeedbackButton'
import { Empty, Loaded } from '../components/PageState'
import { dateTime, relativeTime } from '../lib/format'
import { useLoad } from '../lib/useLoad'

/** The everyday jobs, each in a few steps. Kept in step with docs/USER_GUIDE.md. */
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
      <><strong>Approve</strong> carries it out; <strong>Reject</strong> drops it. Nothing changes until someone approves.</>,
      <>Ask it for something any time with <strong>New task</strong>, e.g. “Check stock and reorder anything low”.</>,
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
          <p className="page-subtitle">How to do the everyday things, who to ask, and your data.</p>
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
