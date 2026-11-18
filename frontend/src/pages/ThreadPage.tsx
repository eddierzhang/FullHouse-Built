import { useEffect, useState } from 'react'
import { type ThreadMessage, discardDraft, fetchThread } from '../api/client'
import type { AppContext } from '../app/context'
import { Loaded } from '../components/PageState'
import { AgentMessage, SourceChips, messageTime } from '../components/prices'
import { useLoad } from '../lib/useLoad'

/** How often to look again while the agent is still answering. */
const PENDING_POLL_MS = 1500

/** Copy a draft's subject and body as text. */
async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}

/** A draft the agent wrote, inside the thread. */
function DraftInThread({ message, app, onChanged }: { message: ThreadMessage; app: AppContext; onChanged: () => void }) {
  const draft = message.draft!
  const [busy, setBusy] = useState(false)
  const discard = async () => {
    setBusy(true)
    try {
      await discardDraft(draft.id)
      onChanged()
    } catch (err) {
      app.toast('Couldn’t discard', err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="pi-draft">
      <p style={{ fontWeight: 600 }}>{draft.subject}</p>
      <p>
        To {draft.to_name ?? 'the rep'}, {draft.supplier.name}
      </p>
      <p style={{ whiteSpace: 'pre-line' }}>{message.text}</p>
      {draft.status === 'draft' ? (
        <div className="pi-actions" style={{ justifyContent: 'flex-start' }}>
          <a className="pi-btn-o" href={`#draft/${draft.id}`}>
            Open draft
          </a>
          <button
            type="button"
            className="pi-link"
            onClick={() => void copyText(`${draft.subject}\n\n${message.text}`).then((ok) => app.toast(ok ? 'Copied' : 'Couldn’t copy', ok ? undefined : 'Open the draft and copy it there.'))}
          >
            Copy
          </button>
          <button type="button" className="pi-link" disabled={busy} onClick={() => void discard()}>
            Discard
          </button>
        </div>
      ) : (
        <p className="pi-label">{draft.status === 'sent' ? 'Sent' : 'Discarded'}</p>
      )}
    </div>
  )
}

/**
 * One conversation: the agent's questions, your questions, its answers, the
 * drafts it wrote, and the sources behind each. Oldest first.
 */
export function ThreadPage({ app, active = false }: { app: AppContext; active?: boolean }) {
  const thread = useLoad(fetchThread, [])
  const pending = thread.data?.some((m) => m.status === 'pending') ?? false

  useEffect(() => {
    if (!pending) return
    const timer = window.setInterval(() => void thread.reload(), PENDING_POLL_MS)
    return () => window.clearInterval(timer)
  }, [pending, thread.reload]) // eslint-disable-line react-hooks/exhaustive-deps

  // Newest at the bottom, where the field is: the conversation is the last section of the Invoices page.
  useEffect(() => {
    if (active && thread.status === 'ready') window.scrollTo({ top: document.body.scrollHeight })
  }, [active, thread.status, thread.data?.length])

  // A question answered in the Questions section above shows as resolved here.
  useEffect(() => {
    const onAnswered = () => void thread.reload()
    window.addEventListener('fullhouse:question-answered', onAnswered)
    return () => window.removeEventListener('fullhouse:question-answered', onAnswered)
  }, [thread.reload]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <section className="pi-section" id="sec-thread" aria-label="Conversation">
        <h2 className="pi-h1">Conversation</h2>
        <Loaded load={thread} what="the conversation" placeholderHeight={200}>
          {(messages) =>
            messages.length === 0 ? (
              <p className="pi-secondary">Nothing asked yet. Use the field below.</p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 28 }} aria-label="Conversation" role="log">
                {messages.map((m) =>
                  m.role === 'you' ? (
                    <div key={m.id} className="pi-you">
                      <p className="pi-label">You, {messageTime(m.created_at)}</p>
                      <p>{m.text}</p>
                    </div>
                  ) : m.question ? (
                    <div key={m.id} className="pi-agent">
                      <p className="pi-label">{messageTime(m.created_at)}</p>
                      {m.question.status === 'open' ? (
                        // The card itself is in the Questions section of this page: one place to answer.
                        <>
                          <p>{m.text || m.question.text}</p>
                          <p className="pi-label">
                            Open. <a href="#questions">Answer it above</a>
                          </p>
                        </>
                      ) : (
                        <>
                          <p>{m.text || m.question.text}</p>
                          <p className="pi-label">{m.question.status === 'answered' ? `Resolved${m.question.answer ? `: ${m.question.answer}` : ''}` : 'Held'}</p>
                        </>
                      )}
                      <SourceChips sources={m.sources} />
                    </div>
                  ) : m.draft ? (
                    <AgentMessage key={m.id} label={messageTime(m.created_at)} sources={m.sources}>
                      <DraftInThread message={m} app={app} onChanged={() => void thread.reload()} />
                    </AgentMessage>
                  ) : (
                    <AgentMessage key={m.id} label={messageTime(m.created_at)} sources={m.status === 'ok' ? m.sources : undefined} pending={m.status !== 'ok'}>
                      {m.status === 'pending' ? (
                        <p aria-busy="true">Reading the invoices…</p>
                      ) : (
                        <p style={{ whiteSpace: 'pre-line' }}>{m.text}</p>
                      )}
                    </AgentMessage>
                  ),
                )}
              </div>
            )
          }
        </Loaded>
    </section>
  )
}
