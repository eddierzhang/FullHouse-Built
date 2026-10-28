import { useEffect, useState } from 'react'
import { type QuestionAnswer, answerQuestion, listQuestions } from '../api/client'
import type { AppContext } from '../app/context'
import { Loaded } from '../components/PageState'
import { QuestionCard } from '../components/prices'
import { useLoad } from '../lib/useLoad'

/** Keys typed into a field, or Enter on a focused button or link, belong to that control, not the queue. */
function inField(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el || !el.tagName) return false
  return ['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON', 'A'].includes(el.tagName) || el.isContentEditable
}

/**
 * The agent's questions, by dollar impact. The chrome is the count and
 * nothing else. Answered cards leave; there is no approve-all.
 *
 * From the keyboard: Enter answers the first card with its first option,
 * 1-3 pick that option, Esc skips it to the back of the queue.
 */
export function QuestionsPage({ app }: { app: AppContext }) {
  const questions = useLoad(listQuestions, [])
  const [gone, setGone] = useState<Set<string>>(new Set())
  //: Cards skipped with Esc, in order; they sort after the rest.
  const [skipped, setSkipped] = useState<string[]>([])
  //: The card the keyboard answered and its option, so the card can show it going.
  const [pressed, setPressed] = useState<{ id: string; option: number } | null>(null)

  const answer = async (id: string, body: QuestionAnswer) => {
    await answerQuestion(id, body)
    window.setTimeout(() => {
      setGone((g) => new Set(g).add(id))
      app.refresh()
    }, 240)
  }

  const open = (questions.data ?? [])
    .filter((q) => q.status === 'open' && !gone.has(q.id))
    .sort((a, b) => {
      const ia = skipped.indexOf(a.id)
      const ib = skipped.indexOf(b.id)
      return (ia === -1 ? -1 : ia) - (ib === -1 ? -1 : ib)
    })
  const first = open[0]

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!first || inField(event.target) || event.metaKey || event.ctrlKey || event.altKey) return
      if (event.key === 'Escape') {
        event.preventDefault()
        setSkipped((s) => [...s.filter((id) => id !== first.id), first.id])
        return
      }
      const index = event.key === 'Enter' ? 0 : ['1', '2', '3'].indexOf(event.key)
      if (index === -1) return
      const option = first.options[index]
      if (!option) return
      event.preventDefault()
      setPressed({ id: first.id, option: index })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [first])

  return (
    <main className="pi">
      <div className="pi-page">
        <Loaded load={questions} what="the questions" placeholderHeight={200}>
          {() => (
            <>
              <h1 className="pi-h1" aria-live="polite">
                {open.length} {open.length === 1 ? 'question' : 'questions'}
              </h1>
              {open.length === 0 && <p className="pi-secondary">Nothing to answer this week.</p>}
              {open.length > 0 && <p className="pi-label pi-desktop-only">Enter answers yes · 1–3 pick · Esc skips</p>}
              <div className="pi-stack" aria-label="Questions">
                {open.map((q) => (
                  <QuestionCard
                    key={q.id}
                    question={q}
                    onAnswer={(body) => answer(q.id, body)}
                    pressOption={pressed?.id === q.id ? pressed.option : null}
                    onPressed={() => setPressed(null)}
                  />
                ))}
              </div>
            </>
          )}
        </Loaded>
      </div>
    </main>
  )
}
