import { type FormEvent, useEffect, useState } from 'react'
import { type AuthOptions, type User, fetchAuthOptions, login, loginSecondStep, signup } from '../api/client'
import { Icon } from '../components/Icon'

export type Mode = 'sign-in' | 'sign-up'

type Props = {
  /** Which form to show. It follows the address (#signin, #signup), so the landing page can link to either. */
  mode: Mode
  onMode: (mode: Mode) => void
  /** The session check itself failed: the API is down, not the password wrong. */
  unreachable: boolean
  /** Signed out by the session ending, not by choice. */
  expired?: boolean
  onRetry: () => void
  onSignedIn: (user: User) => void
}

export function LoginPage({ mode, onMode, unreachable, expired = false, onRetry, onSignedIn }: Props) {
  const [options, setOptions] = useState<AuthOptions | null>(null)
  const [restaurantName, setRestaurantName] = useState('')
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

  // A site that isn't taking new restaurants has no sign-up form, whatever the address says.
  const signingUp = mode === 'sign-up' && options?.signup_enabled !== false

  // A message about one form says nothing about the other.
  useEffect(() => setError(null), [mode])

  const submit = async (credentials?: { email: string; password: string }) => {
    setBusy(true)
    setError(null)
    try {
      const result = credentials
        ? await login(credentials.email, credentials.password)
        : signingUp
          ? await signup(name, email, password, restaurantName)
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

  return (
    <main className="auth-screen">
      <div className="auth-card">
        <a className="brand auth-brand" href="#home" aria-label="FullHouse home">
          <span className="brand-mark">F</span>
          FullHouse
        </a>
        <h1 className="auth-title">{signingUp ? 'Create your restaurant’s account' : 'Sign in'}</h1>
        <p className="auth-subtitle">
          {signingUp
            ? 'You’ll be its owner. Setting it up takes a few minutes, and you can invite your team as you go.'
            : 'Welcome back. Sign in to your dashboard.'}
        </p>

        {expired && !unreachable && (
          <div className="callout callout-warning auth-callout" role="status">
            <Icon name="clock" />
            <div>
              <div className="callout-title">You’ve been signed out</div>
              <div className="callout-body">
                Your session ended. Sign in again to carry on. Everything you’d already saved is kept.
              </div>
            </div>
          </div>
        )}

        {unreachable && (
          <div className="callout callout-danger auth-callout">
            <Icon name="alert" />
            <div>
              <div className="callout-title">Can’t reach FullHouse</div>
              <div className="callout-body">
                Check your internet connection, then{' '}
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
              <span className="field-label">Restaurant name</span>
              <input
                className="input auth-input"
                autoComplete="organization"
                required
                maxLength={120}
                value={restaurantName}
                onChange={(e) => setRestaurantName(e.target.value)}
              />
            </label>
          )}
          {signingUp && (
            <label className="field">
              <span className="field-label">Your name</span>
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

        {signingUp && (
          <p className="auth-switch">Joining a restaurant that’s already here? Use the invite link its owner sent you.</p>
        )}
        {options?.signup_enabled === false && (
          <p className="auth-switch">New here? Ask the owner or a manager for an invite link.</p>
        )}
        {options?.signup_enabled !== false && (
          <p className="auth-switch">
            {signingUp ? 'Already have an account?' : 'New to FullHouse?'}{' '}
            <button type="button" className="link-button" onClick={() => onMode(signingUp ? 'sign-in' : 'sign-up')}>
              {signingUp ? 'Sign in' : 'Create an account'}
            </button>
          </p>
        )}
      </div>
    </main>
  )
}
