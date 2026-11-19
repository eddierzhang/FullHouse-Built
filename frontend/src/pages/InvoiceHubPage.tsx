import { useEffect } from 'react'
import { CsvUpload } from '../components/CsvUpload'
import type { AppContext } from '../app/context'
import type { Route } from '../lib/useHashRoute'
import { DigestPage } from './DigestPage'
import { DocumentsPage } from './DocumentsPage'
import { InvoicesPage } from './InvoicesPage'
import { ItemsPage } from './ItemsPage'
import { QuestionsPage } from './QuestionsPage'
import { ThreadPage } from './ThreadPage'

/**
 * Invoices & prices, in one page: this week's findings (the digest), adding an invoice and what was read, the
 * reader's questions, other documents, the items the invoices were matched to, and the conversation with the
 * agent. The older addresses (#digest, #questions, #documents, #items, #thread) open this page at their section.
 */
export function InvoiceHubPage({ app, section }: { app: AppContext; section: Route }) {
  const go = (id: string) => document.getElementById(`sec-${id}`)?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  useEffect(() => {
    if (section === 'invoices' || section === 'thread') return // the top; the conversation scrolls itself to its newest message
    // The sections load on their own; wait a moment for the page to take its height.
    const timer = window.setTimeout(() => document.getElementById(`sec-${section}`)?.scrollIntoView({ block: 'start' }), 350)
    return () => window.clearTimeout(timer)
  }, [section])

  const parts: [string, string][] = [['digest', 'This week'], ['invoices', 'Invoices'], ['questions', 'Questions']]
  if (app.flags.v2_documents) parts.push(['documents', 'Other documents'])
  parts.push(['items', 'Items'], ['thread', 'Conversation'])

  return (
    <main className="pi">
      <div className="pi-page">
        <header className="pi-hub-head">
          <h1 className="pi-h1">Invoices & prices</h1>
          <nav className="jump-links" aria-label="On this page">
            {parts.map(([id, label]) => (
              <button key={id} type="button" onClick={() => go(id)}>
                {label}
              </button>
            ))}
          </nav>
        </header>
        <DigestPage app={app} />
        <CsvUpload app={app} kinds={['invoices', 'suppliers']} what="Invoice lines: supplier, number, date, item, quantity and price" />
        <InvoicesPage app={app} />
        <QuestionsPage app={app} />
        {app.flags.v2_documents && <DocumentsPage app={app} />}
        <ItemsPage app={app} />
        <ThreadPage app={app} active={section === 'thread'} />
      </div>
    </main>
  )
}
