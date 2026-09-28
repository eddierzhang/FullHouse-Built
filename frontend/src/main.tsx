import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { ApiError, NetworkError, reportClientError } from './api/client'

// Crashes in the browser are reported to the server, which alerts on them
// like its own errors. Each distinct one once per visit, and only a few, so a
// loop can't flood anything.
const reported = new Set<string>()
function report(message: string, stack?: string) {
  if (reported.size >= 20 || reported.has(message)) return
  reported.add(message)
  reportClientError(message, stack)
}
window.addEventListener('error', (event) => report(event.message || 'Script error', event.error?.stack))
window.addEventListener('unhandledrejection', (event) => {
  const reason = event.reason
  // A failed request is already shown to the person where it happened; only real crashes are reported.
  if (reason instanceof ApiError || reason instanceof NetworkError) return
  report(reason instanceof Error ? reason.message : String(reason), reason instanceof Error ? reason.stack : undefined)
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
