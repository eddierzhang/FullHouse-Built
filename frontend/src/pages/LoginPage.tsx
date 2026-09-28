import { type FormEvent, useEffect, useState } from 'react'
import { type AuthOptions, type User, fetchAuthOptions, login, loginSecondStep, signup } from '../api/client'
import { Icon } from '../components/Icon'

type Mode = 'sign-in' | 'sign-up'

type Props = {
  /** The session check itself failed: the API is down, not the password wrong. */
  unreachable: boolean
  onRetry: () => void
  onSignedIn: (user: User) => void
}

export function LoginPage({ unreachable, onRetry, onSignedIn }: Props) {
  const [mode, setMode] = useState<Mode>('sign-in')
  const [options, setOptions] = useState<AuthOptions | null>(null)
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // The password was right and the account uses two-step sign-in: a code comes next.
  const [needsCode, setNeedsCode] = useState(false)
  const [code, setCode] = useState('')

  useEffect(() => {
    fetchAuthOptions().then(setOptions).catch(() => {})
  }, [unreachable])

  const signingUp = mode === 'sign-up'

  const submit = async (credentials?: { email: string; password: string }) => {
    setBusy(true)
    setError(null)
    try {
      const result = credentials
        ? await login(credentials.email, credentials.password)
        : signingUp
          ? await signup(name, email, password)
          : await login(email, password)
      if ('mfa_required' in result) {
        setNeedsCode(true)
        setBusy(false)
        return
      }
      onSignedIn(result)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    void submit()
  }

  const submitCode = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      onSignedIn(await loginSecondStep(code.trim()))
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      setError(message)
      setBusy(false)
      // The attempt ended (timed out, or too many wrong codes): start again from the password.
      if (/Sign in again/.test(message)) {
        setNeedsCode(false)
        setCode('')
      }
    }
  }

  if (needsCode) {
    return (
      <main className="auth-screen">
        <div className="auth-card">
          <div className="brand auth-brand">
            <span className="brand-mark">F</span>
            FullHouse
          </div>
          <h1 className="auth-title">Two-step sign-in</h1>
          <p className="auth-subtitle">Enter the 6-digit code from your authenticator app, or one of your backup codes.</p>
          <form className="auth-form" onSubmit={submitCode} noValidate>
            <label className="field">
              <span className="field-label">Code</span>
              <input
                className="input auth-input mono"
                autoFocus
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={32}
                value={code}
                onChange={(e) => setCode(e.target.value)}
              />
            </label>
            {error && (
              <div className="auth-error" role="alert">
                {error}
              </div>
            )}
            <button className="btn btn-primary auth-submit" type="submit" disabled={busy || !code.trim()}>
              {busy ? 'Checking…' : 'Sign in'}
            </button>
          </form>
          <p className="auth-switch">
            Lost your phone and codes? Ask the owner to reset your two-step sign-in.{' '}
            <button
              type="button"
              className="link-button"
              onClick={() => {
                setNeedsCode(false)
                setCode('')
                setError(null)
              }}
            >
              Back
            </button>
          </p>
        </div>
      </main>
    )
  }

  const switchMode = (next: Mode) => {
    setMode(next)
    setError(null)
  }

  return (
    <main className="auth-screen">
      <div className="auth-card">
        <div className="brand auth-brand">
          <span className="brand-mark">F</span>
          FullHouse
        </div>
        <h1 className="auth-title">{signingUp ? 'Create your account' : 'Sign in'}</h1>
        <p className="auth-subtitle">
          {signingUp ? 'Start running your restaurant with FullHouse.' : 'Welcome back. Sign in to your dashboard.'}
        </p>

        {unreachable && (
          <div className="callout callout-danger auth-callout">
            <Icon name="alert" />
            <div>
              <div className="callout-title">Can’t reach the backend</div>
              <div className="callout-body">
                Check the API server is running, then{' '}
                <button type="button" className="link-button" onClick={onRetry}>
                  try again
                </button>
                .
              </div>
            </div>
          </div>
        )}

        {options?.demo && !signingUp && (
          <div className="auth-demo">
            <div>
              <div className="auth-demo-title">Trying the demo?</div>
              <dl className="auth-demo-creds">
                <dt>Email</dt>
                <dd>
                  <code>{options.demo.email}</code>
                </dd>
                <dt>Password</dt>
                <dd>
                  <code>{options.demo.password}</code>
                </dd>
              </dl>
            </div>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              disabled={busy}
              onClick={() => {
                setEmail(options.demo!.email)
                setPassword(options.demo!.password)
                void submit(options.demo!)
              }}
            >
              Use demo account
            </button>
          </div>
        )}

        <form className="auth-form" onSubmit={onSubmit} noValidate>
          {signingUp && (
            <label className="field">
              <span className="field-label">Name</span>
              <input
                className="input auth-input"
                autoComplete="name"
                required
                maxLength={100}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
          )}
          <label className="field">
            <span className="field-label">Email</span>
            <input
              className="input auth-input"
              type="email"
              autoComplete="email"
              required
              maxLength={254}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <label className="field">
            <span className="field-label">Password</span>
            <input
              className="input auth-input"
              type="password"
              autoComplete={signingUp ? 'new-password' : 'current-password'}
              required
              minLength={signingUp ? 8 : undefined}
              maxLength={256}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            {signingUp && <span className="field-hint">At least 8 characters.</span>}
          </label>

          {error && (
            <div className="auth-error" role="alert">
              {error}
            </div>
          )}

          <button className="btn btn-primary auth-submit" type="submit" disabled={busy}>
            {busy ? (signingUp ? 'Creating account…' : 'Signing in…') : signingUp ? 'Create account' : 'Sign in'}
          </button>
        </form>

        {options?.signup_enabled === false && !signingUp && (
          <p className="auth-switch">New here? Ask the owner or a manager for an invite link.</p>
        )}
        {options?.signup_enabled !== false && (
          <p className="auth-switch">
            {signingUp ? 'Already have an account?' : 'New to FullHouse?'}{' '}
            <button type="button" className="link-button" onClick={() => switchMode(signingUp ? 'sign-in' : 'sign-up')}>
              {signingUp ? 'Sign in' : 'Create an account'}
            </button>
          </p>
        )}
      </div>
    </main>
  )
}
