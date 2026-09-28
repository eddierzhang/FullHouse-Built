import { type FormEvent, useEffect, useRef, useState } from 'react'
import { type MfaSetup as Setup, enableMfa, startMfaSetup } from '../api/client'

/** Backup codes, shown once, with a way to keep them. */
export function BackupCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const [saved, setSaved] = useState(false)
  return (
    <div className="stack" style={{ gap: 12 }}>
      <p className="muted" style={{ margin: 0 }}>
        If you lose your phone, each of these signs you in once. Keep them somewhere safe, like a password manager. You won’t see them again.
      </p>
      <ol className="mfa-codes mono" aria-label="Backup codes">
        {codes.map((code) => (
          <li key={code}>{code}</li>
        ))}
      </ol>
      <div className="row">
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => navigator.clipboard?.writeText(codes.join('\n')).catch(() => {})}>
          Copy codes
        </button>
      </div>
      <label className="row small">
        <input type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} /> I’ve saved these codes
      </label>
      <div>
        <button type="button" className="btn btn-primary" disabled={!saved} onClick={onDone}>
          Continue
        </button>
      </div>
    </div>
  )
}

/**
 * Scan, confirm with a first code, save the backup codes. `currentCode` is
 * needed when two-step is already on (moving to a new phone).
 */
export function MfaSetup({ currentCode, onDone }: { currentCode?: string; onDone: () => void }) {
  const [setup, setSetup] = useState<Setup | null>(null)
  const [code, setCode] = useState('')
  const [codes, setCodes] = useState<string[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // One request per setup. Each replaces the secret waiting on the server, so
  // two in flight (React runs effects twice in development) could finish in
  // either order and leave the page showing a secret the server no longer
  // expects. The ref outlives that second run; starting setup again mounts a
  // new component, with a new request.
  const request = useRef<Promise<Setup> | null>(null)

  useEffect(() => {
    let current = true
    request.current ??= startMfaSetup(currentCode)
    request.current
      .then((next) => current && setSetup(next))
      .catch((err) => current && setError(err instanceof Error ? err.message : String(err)))
    return () => {
      current = false
    }
  }, [currentCode])

  const confirm = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      setCodes((await enableMfa(code.trim())).codes)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  if (codes) return <BackupCodes codes={codes} onDone={onDone} />
  if (!setup) return error ? <div className="auth-error" role="alert">{error}</div> : <div className="skeleton" style={{ height: 200 }} aria-busy="true" />

  return (
    <form className="stack" style={{ gap: 12 }} onSubmit={confirm} noValidate>
      <ol className="stack small" style={{ gap: 6, margin: 0, paddingLeft: 18 }}>
        <li>Open an authenticator app on your phone: Google Authenticator, Microsoft Authenticator, 1Password or similar.</li>
        <li>Scan this code with it.</li>
        <li>Type the 6-digit code the app shows.</li>
      </ol>
      <img className="mfa-qr" src={setup.qr_svg} alt="QR code for your authenticator app" width={200} height={200} />
      <details className="small">
        <summary>Can’t scan it?</summary>
        <p className="muted" style={{ margin: '6px 0 0' }}>
          Enter this key in the app instead: <code className="mono" aria-label="Setup key">{setup.secret}</code>
        </p>
      </details>
      <label className="field">
        <span className="field-label">6-digit code</span>
        <input
          className="input auth-input mono"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={8}
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
      </label>
      {error && (
        <div className="auth-error" role="alert">
          {error}
        </div>
      )}
      <div>
        <button className="btn btn-primary" type="submit" disabled={busy || code.trim().length < 6}>
          {busy ? 'Checking…' : 'Turn on two-step sign-in'}
        </button>
      </div>
    </form>
  )
}
