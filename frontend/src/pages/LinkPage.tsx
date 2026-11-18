import { type FormEvent, useEffect, useState } from 'react'
import { type User, acceptInvite, previewInvite, previewReset, resetPassword } from '../api/client'

export type AccountLink = { kind: 'invite' | 'reset'; token: string }

/** "#invite/<token>" or "#reset/<token>" -> the link; anything else -> null. */
export function readAccountLink(): AccountLink | null {
  const match = window.location.hash.match(/^#\/?(invite|reset)\/([A-Za-z0-9_-]+)/)
  return match ? { kind: match[1] as AccountLink['kind'], token: match[2] } : null
}

type Preview =
  | { kind: 'invite'; restaurant: string | null; role: string; email: string | null }
  | { kind: 'reset'; name: string; email: string }

/**
 * Where an invite or password-reset link lands. Either way the person ends
 * up signed in: with a new account in the restaurant, or with their new password.
 */
export function LinkPage({ link, onSignedIn, onCancel }: { link: AccountLink; onSignedIn: (user: User) => void; onCancel: () => void }) {
  const [preview, setPreview] = useState<Preview | null>(null)
  const [dead, setDead] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const load =
      link.kind === 'invite'
        ? previewInvite(link.token).then((p) => {
            setEmail(p.email ?? '')
            return { kind: 'invite' as const, restaurant: p.restaurant_name, role: p.role, email: p.email }
          })
        : previewReset(link.token).then((p) => ({ kind: 'reset' as const, name: p.name, email: p.email }))
    load.then(setPreview).catch((err) => setDead(err instanceof Error ? err.message : String(err)))
  }, [link])

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (password.length < 8) return setError('Use at least 8 characters.')
    if (link.kind === 'invite' && !name.trim()) return setError('Enter your name.')
    setBusy(true)
    setError(null)
    try {
      const user =
        link.kind === 'invite' ? await acceptInvite(link.token, name.trim(), email.trim(), password) : await resetPassword(link.token, password)
      onSignedIn(user)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const title = link.kind === 'invite' ? 'Join your team' : 'Set a new password'

  return (
    <main className="auth-screen">
      <div className="auth-card">
        <div className="brand auth-brand">
          <span className="brand-mark">F</span>
          FullHouse
        </div>
        <h1 className="auth-title">{title}</h1>

        {dead ? (
          <>
            <p className="auth-subtitle">{dead}</p>
            <button className="btn btn-secondary" onClick={onCancel}>
              Go to sign in
            </button>
          </>
        ) : !preview ? (
          <div className="skeleton" style={{ height: 160 }} aria-busy="true" />
        ) : (
          <>
            <p className="auth-subtitle">
              {preview.kind === 'invite'
                ? `You’ve been invited to ${preview.restaurant ?? 'a restaurant'} on FullHouse as ${preview.role === 'admin' ? 'an admin' : preview.role === 'bookkeeper' ? 'their bookkeeper' : 'a team member'}.`
                : `For ${preview.name} (${preview.email}). Every other device signed in to this account will be signed out.`}
            </p>
            <form className="auth-form" onSubmit={onSubmit} noValidate>
              {preview.kind === 'invite' && (
                <>
                  <label className="field">
                    <span className="field-label">Name</span>
                    <input className="input auth-input" autoComplete="name" required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
                  </label>
                  <label className="field">
                    <span className="field-label">Email</span>
                    <input
                      className="input auth-input"
                      type="email"
                      autoComplete="email"
                      required
                      maxLength={254}
                      readOnly={!!preview.email}
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                    />
                  </label>
                </>
              )}
              <label className="field">
                <span className="field-label">{preview.kind === 'invite' ? 'Password' : 'New password'}</span>
                <input
                  className="input auth-input"
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={8}
                  maxLength={256}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <span className="field-hint">At least 8 characters.</span>
              </label>
              {error && (
                <div className="auth-error" role="alert">
                  {error}
                </div>
              )}
              <button className="btn btn-primary auth-submit" type="submit" disabled={busy}>
                {busy ? 'Saving…' : preview.kind === 'invite' ? 'Create account' : 'Save password'}
              </button>
            </form>
          </>
        )}
      </div>
    </main>
  )
}
