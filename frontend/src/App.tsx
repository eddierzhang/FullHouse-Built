import { useCallback, useEffect, useState } from 'react'
import {
  type AuthOptions,
  type Restaurant,
  SIGNED_OUT_EVENT,
  type User,
  canEditRestaurant,
  fetchAuthOptions,
  fetchCurrentUser,
  fetchRestaurant,
  logout,
} from './api/client'
import { AppShell } from './app/AppShell'
import { BookkeeperApp } from './pages/BookkeeperPage'
import { MfaSetup } from './components/MfaSetup'
import { LandingPage } from './pages/LandingPage'
import { type AccountLink, LinkPage, readAccountLink } from './pages/LinkPage'
import { LoginPage } from './pages/LoginPage'
import { SetupPage } from './pages/SetupPage'

type AuthState =
  | { status: 'checking' }
  | { status: 'error' }
  /** `expired`: the session ended by itself -- say so, rather than just showing the login page. */
  | { status: 'signed-out'; expired?: boolean }
  | { status: 'signed-in'; user: User; restaurant: Restaurant | null }

/** What a signed-out visitor sees, from the address. */
type Front = 'landing' | 'sign-in' | 'sign-up'

/**
 * The bare address is the landing page. Anything else -- #signin, or a link
 * to a page inside the app -- is someone on their way in: the sign-in form.
 */
function readFront(): Front {
  const hash = window.location.hash.replace(/^#\/?/, '')
  if (hash === '' || hash === 'home') return 'landing'
  return hash === 'signup' ? 'sign-up' : 'sign-in'
}

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
  const [front, setFront] = useState<Front>(readFront)
  // What the landing page may offer: whether new restaurants can sign up, and the demo's account.
  const [options, setOptions] = useState<AuthOptions | null>(null)

  useEffect(() => {
    const onChange = () => {
      setLink(readAccountLink())
      setFront(readFront())
      window.scrollTo({ top: 0 })
    }
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  const leaveLink = useCallback(() => {
    window.location.hash = '#overview'
    setLink(null)
  }, [])

  const enter = useCallback(async (user: User) => {
    // The address that led to the form isn't a page inside the app.
    if (/^#\/?(home|signin|signup)?$/.test(window.location.hash)) {
      window.history.replaceState(null, '', window.location.pathname + window.location.search)
      setFront('landing')
    }
    setAuth({ status: 'checking' })
    try {
      // Until two-step is set up, the server answers nothing else -- the restaurant included.
      setAuth({ status: 'signed-in', user, restaurant: user.mfa_setup_required ? null : await fetchRestaurant() })
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

  const signedOut = auth.status === 'signed-out'
  useEffect(() => {
    if (signedOut) fetchAuthOptions().then(setOptions).catch(() => {})
  }, [signedOut])

  useEffect(() => {
    // A data request found the session gone: it expired, or access was turned off.
    const onSignedOut = () => setAuth((a) => ({ status: 'signed-out', expired: a.status === 'signed-in' }))
    window.addEventListener(SIGNED_OUT_EVENT, onSignedOut)
    return () => window.removeEventListener(SIGNED_OUT_EVENT, onSignedOut)
  }, [])

  const signOut = useCallback(async () => {
    await logout().catch(() => {})
    // To the sign-in form, not the landing page: they know what this is. A page
    // they were on stays in the address, so signing back in returns to it.
    if (readFront() === 'landing') window.location.hash = '#signin'
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
      if (auth.user.mfa_setup_required) {
        // The server refuses everything else until this is done.
        return (
          <main className="auth-screen">
            <div className="auth-card">
              <h1 className="auth-title">Set up two-step sign-in</h1>
              <p className="auth-subtitle">
                Owners and admins sign in with a password and a code from their phone, so a stolen password alone can’t get in. It takes a minute.
              </p>
              <MfaSetup onDone={check} />
              <p className="auth-switch">
                <button type="button" className="link-button" onClick={signOut}>
                  Sign out
                </button>
              </p>
            </div>
          </main>
        )
      }
      if (auth.user.role === 'bookkeeper') {
        // Many clients, no restaurant of their own: the clients table is their whole app.
        return <BookkeeperApp user={auth.user} onSignOut={signOut} />
      }
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
      // A session that ended, or a server that can't be reached, is explained on the sign-in form.
      if (front === 'landing' && auth.status === 'signed-out' && !auth.expired) {
        return <LandingPage options={options} />
      }
      return (
        <LoginPage
          mode={front === 'sign-up' ? 'sign-up' : 'sign-in'}
          onMode={(mode) => {
            window.location.hash = mode === 'sign-up' ? '#signup' : '#signin'
          }}
          unreachable={auth.status === 'error'}
          expired={auth.status === 'signed-out' && Boolean(auth.expired)}
          onRetry={check}
          onSignedIn={(user) => void enter(user)}
        />
      )
  }
}
