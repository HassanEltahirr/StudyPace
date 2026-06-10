import { useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { api, setToken } from '../api'
import Brand from '../components/Brand'

const GOOGLE_SCRIPT_SRC = 'https://accounts.google.com/gsi/client'
let googleScriptPromise = null

export default function Login() {
  const location = useLocation()
  const searchParams = new URLSearchParams(location.search)
  const requestedMode = searchParams.get('mode')
  const resetToken = searchParams.get('token') || ''
  const isResetPath = location.pathname === '/reset-password'
  const [mode, setMode] = useState(isResetPath ? 'reset' : requestedMode === 'register' ? 'register' : 'login')
  const [firstName, setFirstName] = useState('')
  const [lastName, setLastName] = useState('')
  const [email, setEmail] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [googleLoading, setGoogleLoading] = useState(false)
  const [googleClientId, setGoogleClientId] = useState('')
  const [googleReady, setGoogleReady] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const googleButtonRef = useRef(null)
  const navigate = useNavigate()
  const redirectTo = location.state?.from?.pathname && location.state.from.pathname !== '/'
    ? `${location.state.from.pathname}${location.state.from.search || ''}`
    : '/today'

  useEffect(() => {
    setMode(isResetPath ? 'reset' : requestedMode === 'register' ? 'register' : 'login')
  }, [isResetPath, requestedMode])

  useEffect(() => {
    let cancelled = false
    api.authConfig()
      .then(config => {
        if (!cancelled && config.google_enabled && config.google_client_id) {
          setGoogleClientId(config.google_client_id)
        }
      })
      .catch(() => {
        if (!cancelled) setGoogleClientId('')
      })

    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!googleClientId || !googleButtonRef.current || mode === 'forgot' || mode === 'reset') return undefined

    let cancelled = false
    setGoogleReady(false)
    loadGoogleScript()
      .then(() => {
        if (cancelled || !window.google?.accounts?.id || !googleButtonRef.current) return
        window.google.accounts.id.initialize({
          client_id: googleClientId,
          callback: response => handleGoogleCredential(response?.credential),
        })
        googleButtonRef.current.innerHTML = ''
        window.google.accounts.id.renderButton(googleButtonRef.current, {
          theme: 'outline',
          size: 'large',
          shape: 'rectangular',
          text: 'continue_with',
          width: Math.max(280, googleButtonRef.current.clientWidth || 320),
        })
        setGoogleReady(true)
      })
      .catch(() => {
        if (!cancelled) setError('Google sign-in is not available right now.')
      })

    return () => {
      cancelled = true
    }
  }, [googleClientId, mode])

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setNotice('')
    if (mode === 'forgot') {
      setLoading(true)
      try {
        await api.forgotPassword({ email })
        setNotice('If an account exists for that email, a reset link has been sent.')
      } catch (e) {
        setError(e.message || 'Could not send reset link.')
      } finally {
        setLoading(false)
      }
      return
    }
    if (mode === 'reset') {
      if (!resetToken) {
        setError('This reset link is missing a token.')
        return
      }
      if (password !== confirmPassword) {
        setError('Passwords do not match.')
        return
      }
      setLoading(true)
      try {
        const { access_token } = await api.resetPassword({ token: resetToken, password })
        setToken(access_token)
        navigate(redirectTo, { replace: true })
      } catch (e) {
        setError(e.message || 'Could not reset password.')
      } finally {
        setLoading(false)
      }
      return
    }
    if (mode === 'register' && password !== confirmPassword) {
      setError('Passwords do not match.')
      return
    }
    if (mode === 'register' && (!firstName.trim() || !lastName.trim())) {
      setError('Enter your first and last name.')
      return
    }
    setLoading(true)
    try {
      const action = mode === 'register' ? api.register : api.login
      const payload = mode === 'register'
        ? { username, email, password, first_name: firstName, last_name: lastName }
        : { username, password }
      const { access_token } = await action(payload)
      setToken(access_token)
      navigate(redirectTo, { replace: true })
    } catch (e) {
      setError(e.message || (mode === 'register' ? 'Could not create account' : 'Invalid credentials'))
    } finally {
      setLoading(false)
    }
  }

  function switchMode(nextMode) {
    setMode(nextMode)
    setError('')
    setNotice('')
    setPassword('')
    setConfirmPassword('')
    if (nextMode === 'login') {
      setFirstName('')
      setLastName('')
    }
  }

  async function handleGoogleCredential(credential) {
    if (!credential || googleLoading) return
    setError('')
    setGoogleLoading(true)
    try {
      const { access_token } = await api.googleLogin({ credential })
      setToken(access_token)
      navigate(redirectTo, { replace: true })
    } catch (e) {
      setError(e.message || 'Google sign-in failed')
    } finally {
      setGoogleLoading(false)
    }
  }

  return (
    <div className="auth-shell">
      <section className="auth-intro" aria-label="What StudyPace offers">
        <p className="eyebrow">What it does</p>
        <h1>Turn your slides into a study route.</h1>
        <p>
          StudyPace takes your lecture decks, assessment dates, and course list, then builds the next thing to study each day.
        </p>
        <div className="auth-benefits">
          <span>Add slides</span>
          <span>Choose quiz, midterm, final, or assignment</span>
          <span>Follow one timetable across courses</span>
          <span>Adjust when plans change</span>
        </div>
        <Link className="auth-learn-link" to="/">
          See the overview
        </Link>
      </section>

      <div className="card auth-card">
        <div className="flex items-center gap-3 mb-8">
          <Brand size="lg" />
        </div>

        <h1 className="mb-1 text-lg font-black text-[var(--text)]">
          {authTitle(mode)}
        </h1>
        <p className="text-sm text-slate-500 mb-6">
          {authSubtitle(mode, googleClientId)}
        </p>

        {googleClientId && mode !== 'forgot' && mode !== 'reset' && (
          <>
            <div className="mb-5">
              <div
                ref={googleButtonRef}
                className="grid min-h-11 place-items-center rounded-lg border border-[var(--border)] bg-[var(--surface-raised)]"
                aria-hidden={!googleReady}
              />
              {(!googleReady || googleLoading) && (
                <p className="mt-2 text-center text-xs font-semibold text-[var(--text-faint)]">
                  {googleLoading ? 'Signing in with Google...' : 'Loading Google sign-in...'}
                </p>
              )}
            </div>

            <div className="mb-5 flex items-center gap-3">
              <div className="h-px flex-1 bg-[var(--border)]" />
              <span className="text-xs font-semibold uppercase tracking-wide text-[var(--text-faint)]">or</span>
              <div className="h-px flex-1 bg-[var(--border)]" />
            </div>
          </>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          {mode === 'register' && (
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className="text-xs font-black uppercase tracking-wide text-slate-400">First name</span>
                <input
                  className="input mt-1"
                  value={firstName}
                  onChange={e => setFirstName(e.target.value)}
                  placeholder="First name"
                  autoComplete="given-name"
                  required
                />
              </label>
              <label className="block">
                <span className="text-xs font-black uppercase tracking-wide text-slate-400">Last name</span>
                <input
                  className="input mt-1"
                  value={lastName}
                  onChange={e => setLastName(e.target.value)}
                  placeholder="Last name"
                  autoComplete="family-name"
                  required
                />
              </label>
            </div>
          )}

          {mode === 'register' && (
            <label className="block">
              <span className="text-xs font-black uppercase tracking-wide text-slate-400">Email</span>
              <input
                className="input mt-1"
                type="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                placeholder="you@example.com"
                autoComplete="email"
                required
              />
            </label>
          )}

          {mode === 'forgot' && (
            <label className="block">
              <span className="text-xs font-black uppercase tracking-wide text-slate-400">Email</span>
              <input
                className="input mt-1"
                type="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                placeholder="you@example.com"
                autoComplete="email"
                autoFocus
                required
              />
            </label>
          )}

          {mode !== 'forgot' && mode !== 'reset' && (
          <label className="block">
            <span className="text-xs font-black uppercase tracking-wide text-slate-400">Username</span>
            <input
              className="input mt-1"
              value={username}
              onChange={e => setUsername(e.target.value)}
              placeholder="Username"
              autoComplete="username"
              autoFocus
              required
            />
          </label>
          )}

          {mode !== 'forgot' && (
          <label className="block">
            <span className="text-xs font-black uppercase tracking-wide text-slate-400">
              {mode === 'reset' ? 'New password' : 'Password'}
            </span>
            <input
              className="input mt-1"
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              placeholder="••••••••"
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              required
            />
          </label>
          )}

          {mode === 'login' && (
            <button
              type="button"
              className="-mt-2 text-sm font-semibold text-[var(--accent)] transition hover:text-[var(--text)]"
              onClick={() => switchMode('forgot')}
            >
              Forgot password?
            </button>
          )}

          {(mode === 'register' || mode === 'reset') && (
            <label className="block">
              <span className="text-xs font-black uppercase tracking-wide text-slate-400">Confirm password</span>
              <input
                className="input mt-1"
                type="password"
                value={confirmPassword}
                onChange={e => setConfirmPassword(e.target.value)}
                placeholder="••••••••"
                autoComplete="new-password"
                required
              />
            </label>
          )}

          {notice && (
            <p className="rounded-2xl bg-emerald-50 border-2 border-emerald-100 p-3 text-sm font-semibold text-emerald-700 dark:bg-emerald-900/20 dark:border-emerald-800 dark:text-emerald-300">
              {notice}
            </p>
          )}

          {error && (
            <p className="rounded-2xl bg-rose-50 border-2 border-rose-100 p-3 text-sm font-semibold text-rose-700 dark:bg-rose-900/20 dark:border-rose-800 dark:text-rose-300">
              {error}
            </p>
          )}

          <button
            type="submit"
            className="btn-primary w-full"
            disabled={submitDisabled({ loading, mode, username, email, password, firstName, lastName, confirmPassword })}
          >
            {loading ? loadingLabel(mode) : submitLabel(mode)}
          </button>
        </form>

        <button
          className="mt-5 w-full rounded-lg px-3 py-2 text-sm font-semibold text-[var(--text-muted)] transition hover:bg-[var(--surface-raised)] hover:text-[var(--text)]"
          type="button"
          onClick={() => switchMode(mode === 'register' ? 'login' : 'register')}
        >
          {mode === 'login' ? 'Create a new account' : 'Back to sign in'}
        </button>
      </div>
    </div>
  )
}

function authTitle(mode) {
  if (mode === 'register') return 'Create account'
  if (mode === 'forgot') return 'Reset password'
  if (mode === 'reset') return 'Choose a new password'
  return 'Sign in'
}

function authSubtitle(mode, googleClientId) {
  if (mode === 'register') return 'Create a private account for your study workspace.'
  if (mode === 'forgot') return 'Enter your email and StudyPace will send a reset link.'
  if (mode === 'reset') return 'Set a new password for your StudyPace account.'
  return googleClientId ? 'Continue with Google, or use your local account.' : 'Enter your username and password.'
}

function submitDisabled({ loading, mode, username, email, password, firstName, lastName, confirmPassword }) {
  if (loading) return true
  if (mode === 'forgot') return !email.trim()
  if (mode === 'reset') return !password || !confirmPassword
  if (mode === 'register') {
    return !username.trim() || !email.trim() || !password || !firstName.trim() || !lastName.trim() || !confirmPassword
  }
  return !username.trim() || !password
}

function submitLabel(mode) {
  if (mode === 'register') return 'Create account'
  if (mode === 'forgot') return 'Send reset link'
  if (mode === 'reset') return 'Reset password'
  return 'Sign in'
}

function loadingLabel(mode) {
  if (mode === 'register') return 'Creating account...'
  if (mode === 'forgot') return 'Sending link...'
  if (mode === 'reset') return 'Resetting password...'
  return 'Signing in...'
}

function loadGoogleScript() {
  if (window.google?.accounts?.id) return Promise.resolve()
  if (googleScriptPromise) return googleScriptPromise

  googleScriptPromise = new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[src="${GOOGLE_SCRIPT_SRC}"]`)
    if (existing) {
      existing.addEventListener('load', resolve, { once: true })
      existing.addEventListener('error', reject, { once: true })
      return
    }

    const script = document.createElement('script')
    script.src = GOOGLE_SCRIPT_SRC
    script.async = true
    script.defer = true
    script.onload = resolve
    script.onerror = reject
    document.head.appendChild(script)
  })

  return googleScriptPromise
}
