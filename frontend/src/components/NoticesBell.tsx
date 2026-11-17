import { useEffect, useRef, useState } from 'react'
import { markNoticesRead, type Notices } from '../api/notifications'
import { relativeTime } from '../lib/format'
import { Icon } from './Icon'

/**
 * The bell: what FullHouse has told the restaurant lately. An order about
 * to go out by itself, a delivery that came in short, an item that will run
 * out. Opening one goes to where it can be dealt with and marks it read.
 */
export function NoticesBell({ notices, onChange }: { notices: Notices; onChange: (next: Notices) => void }) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false)
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', away)
    window.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', away)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])

  const read = (ids?: string[]) => void markNoticesRead(ids).then(onChange).catch(() => {})
  return (
    <div className="bell" ref={box}>
      <button className="btn btn-ghost btn-icon bell-button" aria-label={notices.unread ? `Notifications, ${notices.unread} unread` : 'Notifications'} aria-expanded={open} onClick={() => setOpen(!open)}>
        <Icon name="bell" />
        {notices.unread > 0 && <span className="bell-count" aria-hidden="true">{notices.unread > 9 ? '9+' : notices.unread}</span>}
      </button>
      {open && (
        <div className="bell-panel" role="dialog" aria-label="Notifications">
          <div className="bell-head">
            <span className="strong">Notifications</span>
            {notices.unread > 0 && (
              <button className="link-button" onClick={() => read()}>
                Mark all read
              </button>
            )}
          </div>
          {notices.items.length === 0 ? (
            <div className="bell-empty">Nothing yet. Orders about to go, late or short deliveries and items running out show here.</div>
          ) : (
            <ul className="bell-list">
              {notices.items.map((n) => (
                <li key={n.id}>
                  <a
                    className={`bell-item${n.read ? '' : ' unread'}`}
                    href={n.link ?? '#overview'}
                    onClick={() => {
                      if (!n.read) read([n.id])
                      setOpen(false)
                    }}
                  >
                    <span className="bell-title">{n.title}</span>
                    {n.body && <span className="bell-body">{n.body}</span>}
                    <span className="bell-when">{relativeTime(n.created_at)}</span>
                  </a>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
