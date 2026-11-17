import { useEffect, useMemo, useRef, useState } from 'react'
import { askAgent } from '../api/client'
import { searchEverything, type SearchHit } from '../api/insights'
import { Icon, type IconName } from './Icon'

export type CommandPage = { label: string; href: string; group: string; icon: IconName; keywords?: string }

type Row = { key: string; title: string; subtitle: string; icon: IconName; run: () => void | Promise<void> }

const KIND_ICON: Record<SearchHit['kind'], IconName> = { stock: 'inventory', price: 'trend', supplier: 'suppliers', invoice: 'suppliers', dish: 'menu' }
const KIND_LABEL: Record<SearchHit['kind'], string> = { stock: 'Stock', price: 'Prices', supplier: 'Supplier', invoice: 'Invoice', dish: 'Dish' }

/**
 * Search from anywhere: type a page, an item, a supplier, an invoice
 * number or a dish and go straight to it; anything else is a question for
 * the assistant. Opened with Ctrl or Cmd K, "/" outside a field, or the
 * search button; arrows move, Enter goes, Esc closes.
 */
export function CommandBar({ open, onClose, pages, canAsk, onError }: { open: boolean; onClose: () => void; pages: CommandPage[]; canAsk: boolean; onError: (message: string) => void }) {
  const [text, setText] = useState('')
  const [hits, setHits] = useState<SearchHit[]>([])
  const [searching, setSearching] = useState(false)
  const [active, setActive] = useState(0)
  const input = useRef<HTMLInputElement>(null)
  const list = useRef<HTMLUListElement>(null)

  useEffect(() => {
    if (!open) return
    setText('')
    setHits([])
    setActive(0)
    // After the dialog has painted, so the field takes focus on phones too.
    const timer = window.setTimeout(() => input.current?.focus(), 20)
    return () => window.clearTimeout(timer)
  }, [open])

  const query = text.trim()
  useEffect(() => {
    if (!open || query.length < 2) {
      setHits([])
      setSearching(false)
      return
    }
    let stale = false
    setSearching(true)
    const timer = window.setTimeout(() => {
      searchEverything(query)
        .then((found) => !stale && setHits(found))
        .catch(() => !stale && setHits([]))
        .finally(() => !stale && setSearching(false))
    }, 160)
    return () => {
      stale = true
      window.clearTimeout(timer)
    }
  }, [open, query])

  const rows = useMemo<Row[]>(() => {
    const go = (href: string) => () => {
      window.location.hash = href
      onClose()
    }
    const needle = query.toLowerCase()
    const matching = pages.filter((p) => !needle || `${p.label} ${p.group} ${p.keywords ?? ''}`.toLowerCase().includes(needle))
    const out: Row[] = hits.map((h, i) => ({ key: `hit-${i}-${h.href}-${h.title}`, title: h.title, subtitle: `${KIND_LABEL[h.kind]} · ${h.subtitle}`, icon: KIND_ICON[h.kind], run: go(h.href) }))
    out.push(...matching.slice(0, needle ? 6 : 8).map((p) => ({ key: `page-${p.href}`, title: p.label, subtitle: `Page · ${p.group}`, icon: p.icon, run: go(p.href) })))
    if (canAsk && query.length >= 4) {
      out.push({
        key: 'ask',
        title: `Ask the assistant: “${query}”`,
        subtitle: 'Answered from your invoices, in Conversation',
        icon: 'chat',
        run: async () => {
          try {
            await askAgent(query)
            window.location.hash = '#thread'
            onClose()
          } catch (err) {
            onError(err instanceof Error ? err.message : String(err))
          }
        },
      })
    }
    return out
  }, [hits, pages, query, canAsk, onClose, onError])

  useEffect(() => setActive(0), [query, hits.length])
  useEffect(() => {
    list.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' })
  }, [active])

  if (!open) return null

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') onClose()
    else if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((a) => Math.min(rows.length - 1, a + 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((a) => Math.max(0, a - 1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      void rows[active]?.run()
    }
  }

  return (
    <>
      <div className="overlay" onClick={onClose} />
      <div className="modal-wrap command-wrap" onClick={(e) => e.target === e.currentTarget && onClose()}>
        <div className="modal command" role="dialog" aria-modal="true" aria-label="Search FullHouse" onKeyDown={onKey}>
          <div className="command-field">
            <Icon name="search" size={18} />
            <input
              ref={input}
              autoFocus
              className="command-input"
              type="text"
              role="combobox"
              aria-expanded="true"
              aria-controls="command-results"
              aria-activedescendant={rows[active] ? `command-${active}` : undefined}
              aria-label="Search for a page, item, supplier or invoice, or ask a question"
              placeholder="Find a page, item, supplier or invoice…"
              value={text}
              onChange={(e) => setText(e.target.value)}
              enterKeyHint="go"
            />
            <button className="btn btn-ghost btn-icon" aria-label="Close search" onClick={onClose}>
              <Icon name="close" size={16} />
            </button>
          </div>
          <ul className="command-results" id="command-results" role="listbox" ref={list} aria-label="Results">
            {rows.map((row, i) => (
              <li key={row.key} id={`command-${i}`} role="option" aria-selected={i === active} className="command-row" onMouseEnter={() => setActive(i)} onClick={() => void row.run()}>
                <Icon name={row.icon} size={18} />
                <span className="command-row-text">
                  <span className="command-row-title">{row.title}</span>
                  <span className="command-row-sub">{row.subtitle}</span>
                </span>
              </li>
            ))}
            {rows.length === 0 && <li className="command-empty">{searching ? 'Searching…' : `Nothing called “${query}”. Try an item, a supplier or a page.`}</li>}
          </ul>
          <div className="command-hint hide-mobile">
            <kbd>↑</kbd>
            <kbd>↓</kbd> move <kbd>Enter</kbd> go <kbd>Esc</kbd> close
          </div>
        </div>
      </div>
    </>
  )
}
