import { useCallback, useEffect, useState } from 'react'
import {
  type Restaurant,
  SIGNED_OUT_EVENT,
  type User,
  canEditRestaurant,
  fetchCurrentUser,
  fetchRestaurant,
  logout,
} from './api/client'
import { AppShell } from './app/AppShell'
import { type AccountLink, LinkPage, readAccountLink } from './pages/LinkPage'
import { LoginPage } from './pages/LoginPage'
import { SetupPage } from './pages/SetupPage'

type AuthState =
  | { status: 'checking' }
  | { status: 'error' }
  | { status: 'signed-out' }
  | { status: 'signed-in'; user: User; restaurant: Restaurant | null }

/**
 * Nothing behind the login is rendered, or fetched, until the server has
 * confirmed a session. The server is what actually enforces it: every data
 * route answers 401 without one, and that sends the app back here.
 *
 * Signed in, an owner whose restaurant isn't set up yet goes through setup
 * before the dashboard.
 */
export default function App() {
  const [auth, setAuth] = useState<AuthState>({ status: 'checking' })
  // An invite or password-reset link opens its own page, signed in or not.
  const [link, setLink] = useState<AccountLink | null>(readAccountLink)

  useEffect(() => {
    const onChange = () => setLink(readAccountLink())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  const leaveLink = useCallback(() => {
    window.location.hash = '#overview'
    setLink(null)
  }, [])

  const enter = useCallback(async (user: User) => {
    setAuth({ status: 'checking' })
    try {
      setAuth({ status: 'signed-in', user, restaurant: await fetchRestaurant() })
    } catch {
      setAuth({ status: 'error' })
    }
  }, [])

  const check = useCallback(() => {
    setAuth({ status: 'checking' })
    fetchCurrentUser()
      .then((user) => (user ? enter(user) : setAuth({ status: 'signed-out' })))
      .catch(() => setAuth({ status: 'error' }))
  }, [enter])

  useEffect(check, [check])

  useEffect(() => {
    const onSignedOut = () => setAuth({ status: 'signed-out' })
    window.addEventListener(SIGNED_OUT_EVENT, onSignedOut)
    return () => window.removeEventListener(SIGNED_OUT_EVENT, onSignedOut)
  }, [])

  const signOut = useCallback(async () => {
    await logout().catch(() => {})
    setAuth({ status: 'signed-out' })
  }, [])

  const setRestaurant = useCallback(
    (restaurant: Restaurant) => setAuth((a) => (a.status === 'signed-in' ? { ...a, restaurant } : a)),
    [],
  )

  if (link && auth.status !== 'checking') {
    return (
      <LinkPage
        link={link}
        onSignedIn={(user) => {
          leaveLink()
          void enter(user)
        }}
        onCancel={leaveLink}
      />
    )
  }

  switch (auth.status) {
    case 'checking':
      return <div className="auth-screen" aria-busy="true" />
    case 'signed-in':
      if (!auth.restaurant?.setup_completed_at && !canEditRestaurant(auth.user)) {
        // A member can't set the restaurant up, so there's nothing to do but wait.
        return (
          <main className="auth-screen">
            <div className="auth-card">
              <h1 className="auth-title">Almost ready</h1>
              <p className="auth-subtitle">
                The owner is still setting up {auth.restaurant?.name ?? 'the restaurant'}. Check back once they’ve finished.
              </p>
              <div className="row">
                <button className="btn btn-secondary" onClick={check}>
                  Check again
                </button>
                <button className="btn btn-ghost" onClick={signOut}>
                  Sign out
                </button>
              </div>
            </div>
          </main>
        )
      }
      if (!auth.restaurant?.setup_completed_at) {
        return (
          <SetupPage
            user={auth.user}
            restaurant={auth.restaurant}
            onSaved={setRestaurant}
            onFinished={(restaurant) => {
              setRestaurant(restaurant)
              window.location.hash = '#overview'
            }}
            onSignOut={signOut}
          />
        )
      }
      return <AppShell user={auth.user} restaurant={auth.restaurant} setRestaurant={setRestaurant} onSignOut={signOut} />
    default:
      return (
        <LoginPage unreachable={auth.status === 'error'} onRetry={check} onSignedIn={(user) => void enter(user)} />
      )
  }
}
