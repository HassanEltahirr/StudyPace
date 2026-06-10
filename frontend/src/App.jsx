import { lazy, Suspense, useEffect, useState } from 'react'
import { Navigate, Routes, Route, NavLink, useLocation, useParams } from 'react-router-dom'
import { getToken, clearToken } from './api'
import Brand from './components/Brand'

const Assessments = lazy(() => import('./pages/Assessments'))
const Calendar = lazy(() => import('./pages/Calendar'))
const Courses = lazy(() => import('./pages/Courses'))
const Dashboard = lazy(() => import('./pages/Dashboard'))
const Grades = lazy(() => import('./pages/Grades'))
const Instructor = lazy(() => import('./pages/Instructor'))
const Landing = lazy(() => import('./pages/Landing'))
const Lesson = lazy(() => import('./pages/Lesson'))
const Login = lazy(() => import('./pages/Login'))
const OAuthCallback = lazy(() => import('./pages/OAuthCallback'))
const Timetable = lazy(() => import('./pages/Timetable'))

const nav = [
  { to: '/today', label: 'Today', icon: 'clock' },
  { to: '/courses', label: 'Courses', icon: 'book' },
  { to: '/calendar', label: 'Plan', icon: 'bars' },
  { to: '/timetable', label: 'Timetable', icon: 'calendar' },
]

function RequireAuth({ children }) {
  const location = useLocation()
  if (!getToken()) {
    return <Navigate to="/login" replace state={{ from: location }} />
  }
  return children
}

export default function App() {
  const [theme, setTheme] = useState(() => {
    const saved = localStorage.getItem('studypace.theme')
    if (saved === 'light' || saved === 'dark') return saved
    return 'dark'
  })
  const location = useLocation()
  const hasToken = Boolean(getToken())
  const isLogin = location.pathname === '/login'
  const isLanding = location.pathname === '/' && !hasToken
  const usePageHeader = location.pathname.startsWith('/grades')

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    document.documentElement.classList.toggle('light', theme === 'light')
    localStorage.setItem('studypace.theme', theme)
  }, [theme])

  function logout() {
    clearToken()
    window.location.href = '/login'
  }

  const showAppChrome = !isLogin && !isLanding && hasToken

  return (
    <div className="min-h-screen bg-[var(--bg)] text-[var(--text)] md:flex">
      {showAppChrome && (
        <aside className="app-sidebar hidden md:flex">
          <NavLink to="/today" className="app-sidebar-brand">
            <Brand />
          </NavLink>
          <nav className="app-sidebar-nav" aria-label="Primary">
            {nav.map(({ to, label, icon }) => (
              <NavLink
                key={to}
                to={to}
                end={to === '/'}
                className={({ isActive }) => `app-sidebar-link ${isActive ? 'active' : ''}`}
              >
                <Icon name={icon} />
                <span>{label}</span>
              </NavLink>
            ))}
          </nav>
          <div className="app-sidebar-actions">
            <button
              className="app-icon-button"
              aria-label={theme === 'dark' ? 'Use light mode' : 'Use dark mode'}
              title={theme === 'dark' ? 'Use light mode' : 'Use dark mode'}
              onClick={() => setTheme(value => value === 'dark' ? 'light' : 'dark')}
            >
              <Icon name={theme === 'dark' ? 'sun' : 'moon'} />
            </button>
            <button
              className="app-icon-button danger"
              aria-label="Sign out"
              title="Sign out"
              onClick={logout}
            >
              <Icon name="logout" />
            </button>
          </div>
        </aside>
      )}

      {!isLogin && !isLanding && !usePageHeader && (
        <header className="app-header">
          <div className="app-header-inner">
            <NavLink to={hasToken ? '/today' : '/'} className="app-brand-link">
              <Brand />
            </NavLink>
            {hasToken && (
              <div className="hidden md:flex items-center gap-1">
                {nav.map(({ to, label }) => (
                  <NavLink
                    key={to}
                    to={to}
                    end={to === '/'}
                    className={({ isActive }) =>
                      `px-3 py-1.5 rounded-md text-sm font-semibold transition ${
                        isActive
                          ? 'bg-[var(--surface-raised)] text-[var(--accent)]'
                          : 'text-[var(--text-faint)] hover:bg-[var(--surface-raised)] hover:text-[var(--text-muted)]'
                      }`
                    }
                  >
                    {label}
                  </NavLink>
                ))}
              </div>
            )}
            <div className="app-header-actions">
              <button
                className="grid h-9 w-9 place-items-center rounded-lg border border-[var(--border)] bg-[var(--surface-raised)] text-[var(--text-muted)] transition hover:border-[var(--border-strong)] hover:text-[var(--text)]"
                aria-label={theme === 'dark' ? 'Use light mode' : 'Use dark mode'}
                title={theme === 'dark' ? 'Use light mode' : 'Use dark mode'}
                onClick={() => setTheme(value => value === 'dark' ? 'light' : 'dark')}
              >
                <Icon name={theme === 'dark' ? 'sun' : 'moon'} />
              </button>
              {hasToken && (
                <button
                  className="grid h-9 w-9 place-items-center rounded-lg border border-[var(--border)] bg-[var(--surface-raised)] text-[var(--text-muted)] transition hover:border-[var(--border-strong)] hover:text-[var(--danger)]"
                  aria-label="Sign out"
                  title="Sign out"
                  onClick={logout}
                >
                  <Icon name="logout" />
                </button>
              )}
            </div>
          </div>
        </header>
      )}

      <main className={`app-main ${showAppChrome ? 'app-main-with-sidebar' : ''} ${isLanding ? 'app-main-landing' : ''} ${usePageHeader ? 'pt-4' : 'pt-4 sm:pt-5'}`}>
        <Suspense fallback={<RouteFallback />}>
          <Routes>
            <Route path="/" element={hasToken ? <Navigate to="/today" replace /> : <Landing />} />
            <Route path="/login" element={<Login />} />
            <Route path="/*" element={
              <RequireAuth>
                <Routes>
                  <Route path="/today" element={<Dashboard />} />
                  <Route path="/courses" element={<Courses />} />
                  <Route path="/courses/:courseId" element={<Courses />} />
                  <Route path="/lesson/:lectureId" element={<Lesson />} />
                  <Route path="/practice/:lectureId" element={<PracticeRedirect />} />
                  <Route path="/quiz" element={<Navigate to="/courses" replace />} />
                  <Route path="/review" element={<Navigate to="/" replace />} />
                  <Route path="/calendar" element={<Calendar />} />
                  <Route path="/progress" element={<Navigate to="/timetable" replace />} />
                  <Route path="/timetable" element={<Timetable />} />
                  <Route path="/grades" element={<Grades />} />
                  <Route path="/tutor" element={<Navigate to="/courses" replace />} />
                  <Route path="/dashboard" element={<Dashboard />} />
                  <Route path="/instructor" element={<Instructor />} />
                  <Route path="/assessments" element={<Assessments />} />
                  <Route path="/blackboard-import" element={<Navigate to="/courses" replace />} />
                  <Route path="/auth/callback" element={<OAuthCallback />} />
                </Routes>
              </RequireAuth>
            } />
          </Routes>
        </Suspense>
      </main>

      {!isLogin && !isLanding && hasToken && (
        <nav className="fixed inset-x-0 bottom-0 z-40 border-t border-[var(--border)] bg-[var(--bg)] px-3 py-2 backdrop-blur md:hidden">
          <div className="mx-auto grid max-w-lg grid-cols-4 gap-1">
            {nav.map(({ to, label, icon }) => (
              <NavLink
                key={to}
                to={to}
                end={to === '/'}
                className={({ isActive }) =>
                  `flex flex-col items-center justify-center gap-1 rounded-lg px-2 py-1.5 text-[11px] font-semibold transition ${
                    isActive ? 'text-[var(--accent)]' : 'text-[var(--text-faint)] hover:bg-[var(--surface-raised)] hover:text-[var(--text-muted)]'
                  }`
                }
              >
                <Icon name={icon} />
                <span>{label}</span>
              </NavLink>
            ))}
          </div>
        </nav>
      )}
    </div>
  )
}

function RouteFallback() {
  return <div className="min-h-[40vh]" aria-live="polite" aria-busy="true" />
}

function PracticeRedirect() {
  const { lectureId } = useParams()
  return <Navigate to={lectureId ? `/lesson/${lectureId}` : '/courses'} replace />
}

function Icon({ name }) {
  const common = {
    className: 'h-6 w-6',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: '2.2',
    strokeLinecap: 'round',
    strokeLinejoin: 'round',
    viewBox: '0 0 24 24',
    'aria-hidden': 'true',
  }

  if (name === 'clock') return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>
  if (name === 'book') return <svg {...common}><path d="M5 4h10a4 4 0 0 1 4 4v12H9a4 4 0 0 0-4-4V4Z" /><path d="M5 16V4" /><path d="M9 8h6M9 12h5" /></svg>
  if (name === 'bars') return <svg {...common}><path d="M5 20V10" /><path d="M12 20V4" /><path d="M19 20v-7" /></svg>
  if (name === 'calendar') return <svg {...common}><path d="M8 2v4M16 2v4" /><rect x="3" y="5" width="18" height="16" rx="3" /><path d="M3 10h18" /><path d="M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01" /></svg>
  if (name === 'sun') return <svg {...common}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" /></svg>
  if (name === 'logout') return <svg {...common}><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" /></svg>
  return <svg {...common}><path d="M21 14.5A8.5 8.5 0 0 1 9.5 3 7 7 0 1 0 21 14.5Z" /></svg>
}
