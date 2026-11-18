import { type ReactNode, createContext, useContext } from 'react'

/** The section id a page is being shown under when it is one section of a larger page; null when it is the page. */
const Embedded = createContext<string | null>(null)

/** Shows a page as one section of another: its frame becomes a `<section>` and its title a second-level heading. */
export function EmbeddedSection({ id, children }: { id: string; children: ReactNode }) {
  return <Embedded.Provider value={id}>{children}</Embedded.Provider>
}

/** A page's outer frame: `<main>` on its own, a `<section id="sec-…">` inside a larger page. */
export function PageFrame({ className = 'page', children }: { className?: string; children: ReactNode }) {
  const id = useContext(Embedded)
  if (id === null) return <main className={className}>{children}</main>
  const rest = className.split(' ').filter((c) => c !== 'page' && c !== 'ml-page').join(' ')
  return (
    <section className={`page-section ${rest}`.trim()} id={`sec-${id}`}>
      {children}
    </section>
  )
}

/** A page's title. `embedded` is what to call it as a section, when the larger page already has its name. */
export function PageTitle({ children, embedded }: { children: ReactNode; embedded?: string }) {
  const id = useContext(Embedded)
  return id === null ? <h1 className="page-title">{children}</h1> : <h2 className="page-title section-title">{embedded ?? children}</h2>
}
