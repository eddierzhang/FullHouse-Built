import { useEffect, useRef, useState } from 'react'

/**
 * The rendered width of an element, kept current as it resizes.
 *
 * Charts draw at their real pixel width rather than scaling a fixed
 * viewBox, which would shrink axis text to illegibility in a narrow card.
 */
export function useElementWidth<T extends HTMLElement>(fallback = 640) {
  const ref = useRef<T>(null)
  const [width, setWidth] = useState(fallback)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const measure = () => setWidth(Math.max(240, Math.round(el.getBoundingClientRect().width)))
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  return [ref, width] as const
}
