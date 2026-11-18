import { useEffect } from 'react'
import type { AppContext } from '../app/context'
import type { Route } from '../lib/useHashRoute'
import { DocumentsPage } from './DocumentsPage'
import { InvoicesPage } from './InvoicesPage'
import { ItemsPage } from './ItemsPage'
import { QuestionsPage } from './QuestionsPage'
import { ThreadPage } from './ThreadPage'

/**
 * Invoices, in one page: add an invoice and see what was read, answer the reader's questions, upload any other
 * document, the items the invoices were matched to, and the conversation with the agent. The older addresses
 * (#questions, #documents, #items, #thread) open this page at their section.
 */
export function InvoiceHubPage({ app, section }: { app: AppContext; section: Route }) {
  useEffect(() => {
    if (section === 'invoices' || section === 'thread') return // the top; the conversation scrolls itself to its newest message
    // The sections load on their own; wait a moment for the page to take its height.
    const timer = window.setTimeout(() => document.getElementById(`sec-${section}`)?.scrollIntoView({ block: 'start' }), 350)
    return () => window.clearTimeout(timer)
  }, [section])

  return (
    <main className="pi">
      <div className="pi-page">
        <InvoicesPage app={app} />
        <QuestionsPage app={app} />
        {app.flags.v2_documents && <DocumentsPage app={app} />}
        <ItemsPage app={app} />
        <ThreadPage app={app} active={section === 'thread'} />
      </div>
    </main>
  )
}
