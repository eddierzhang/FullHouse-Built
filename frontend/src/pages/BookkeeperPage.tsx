import { useState } from 'react'
import { type BookkeeperClient, type QuestionAnswer, type User, answerClientQuestion, listBookkeeperClients, listClientQuestions } from '../api/client'
import { Loaded } from '../components/PageState'
import { Money, QuestionCard } from '../components/prices'
import { Toast, type ToastState } from '../components/Toast'
import { shortDate } from '../lib/format'
import { useLoad } from '../lib/useLoad'

/** One client's open questions, answered here on the owner's behalf. */
function ClientQueue({ client, onAnswered }: { client: BookkeeperClient; onAnswered: () => void }) {
  const questions = useLoad(() => listClientQuestions(client.restaurant_id), [client.restaurant_id])
  const [gone, setGone] = useState<Set<string>>(new Set())

  const answer = async (id: string, body: QuestionAnswer) => {
    await answerClientQuestion(client.restaurant_id, id, body)
    window.setTimeout(() => {
      setGone((g) => new Set(g).add(id))
      onAnswered()
    }, 240)
  }

  return (
    <aside className="pi-clients-pane" aria-label={`${client.name} questions`}>
      <Loaded load={questions} what="the questions" placeholderHeight={160}>
        {(rows) => {
          const open = rows.filter((q) => q.status === 'open' && !gone.has(q.id))
          return (
            <>
              <div className="pi-stack-sm" style={{ gap: 2 }}>
                <h2 className="pi-h1">
                  {client.name}, {open.length} {open.length === 1 ? 'question' : 'questions'}
                </h2>
                <p className="pi-label">Your answers show the owner as resolved.</p>
              </div>
              {open.length === 0 && <p className="pi-secondary">Nothing to answer for {client.name}.</p>}
              {open.map((q) => (
                <QuestionCard key={q.id} question={q} onAnswer={(body) => answer(q.id, body)} />
              ))}
            </>
          )
        }}
      </Loaded>
    </aside>
  )
}

/**
 * One bookkeeper, many clients: who has questions waiting, when each last
 * got a digest, and the dollars flagged this month. Sorted by questions.
 */
export function BookkeeperPage() {
  const clients = useLoad(listBookkeeperClients, [])
  const [selected, setSelected] = useState<string | null>(null)

  return (
    <main className="pi">
      <div className="pi-page pi-wide">
        <Loaded load={clients} what="your clients" placeholderHeight={200}>
          {(rows) => {
            const sorted = [...rows].sort((a, b) => b.questions_open - a.questions_open || a.name.localeCompare(b.name))
            const current = sorted.find((c) => c.restaurant_id === selected) ?? null
            return (
              <>
                <h1 className="pi-h1">
                  {sorted.length} {sorted.length === 1 ? 'client' : 'clients'}
                </h1>
                <div className="pi-clients">
                  <section className="pi-clients-table" aria-label="Clients">
                    <div className="pi-client-head" role="row">
                      <span>Client</span>
                      <span className="num sorted">
                        Questions
                        <svg aria-hidden="true" width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5">
                          <path d="M6 2v8M3 7l3 3 3-3" />
                        </svg>
                      </span>
                      <span>Last digest</span>
                      <span className="num">Flagged this month</span>
                    </div>
                    {sorted.length === 0 && <p className="pi-secondary" style={{ padding: '12px' }}>No clients yet. An owner invites you from their team page.</p>}
                    {sorted.map((c) => (
                      <button key={c.restaurant_id} type="button" className="pi-client-row" aria-current={c.restaurant_id === selected ? 'true' : undefined} onClick={() => setSelected(c.restaurant_id)}>
                        <span>{c.name}</span>
                        <span className="num pi-money">{c.questions_open}</span>
                        <span className="pi-money">{c.last_digest ? shortDate(c.last_digest) : 'none yet'}</span>
                        <span className="num">
                          <Money value={c.flagged_month} />
                        </span>
                      </button>
                    ))}
                  </section>
                  {current && <ClientQueue key={current.restaurant_id} client={current} onAnswered={() => void clients.reload()} />}
                </div>
              </>
            )
          }}
        </Loaded>
      </div>
    </main>
  )
}

/** The bookkeeper's whole app: the brand, a way out, and the clients. No ask field: they answer, they don't ask. */
export function BookkeeperApp({ user, onSignOut }: { user: User; onSignOut: () => void }) {
  const [toast] = useState<ToastState>(null)
  return (
    <div className="pi" style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
      <header className="topbar" style={{ background: 'var(--bg)' }}>
        <a className="brand" href="#bookkeeper" style={{ padding: 0 }}>
          <span className="brand-mark">F</span>
          <span className="brand-text">
            <span className="brand-name">FullHouse</span>
            <span className="brand-sub">Bookkeeper</span>
          </span>
        </a>
        <div className="topbar-spacer" />
        <span className="pi-secondary hide-mobile">{user.email}</span>
        <button type="button" className="pi-btn-o" onClick={onSignOut}>
          Sign out
        </button>
      </header>
      <BookkeeperPage />
      <Toast toast={toast} />
    </div>
  )
}
