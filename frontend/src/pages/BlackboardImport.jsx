import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api'

const MS_TENANT    = '08fe1c0a-19f5-4f24-a662-fdd5dd460025'
const MS_CLIENT_ID = '0801541c-a1d6-40ad-a943-19fa62be722f'
const SCOPES       = 'User.Read openid profile offline_access'

const STEP_LOGIN = 'login'
const STEP_PICK  = 'pick'
const STEP_DONE  = 'done'

// ── PKCE helpers ──────────────────────────────────────────────────────────────

function randomBase64url(len) {
  const buf = crypto.getRandomValues(new Uint8Array(len))
  return btoa(String.fromCharCode(...buf)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '')
}

async function sha256Base64url(str) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(str))
  return btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '')
}

async function startOAuth(returnTo) {
  const verifier  = randomBase64url(48)
  const challenge = await sha256Base64url(verifier)
  const state     = randomBase64url(16)

  sessionStorage.setItem('oauth_code_verifier', verifier)
  sessionStorage.setItem('oauth_state', state)
  sessionStorage.setItem('oauth_return_to', returnTo)

  const redirectUri = `${window.location.origin}/auth/callback`
  const url = new URL(`https://login.microsoftonline.com/${MS_TENANT}/oauth2/v2.0/authorize`)
  url.searchParams.set('client_id',              MS_CLIENT_ID)
  url.searchParams.set('response_type',          'code')
  url.searchParams.set('redirect_uri',           redirectUri)
  url.searchParams.set('scope',                  SCOPES)
  url.searchParams.set('code_challenge',         challenge)
  url.searchParams.set('code_challenge_method',  'S256')
  url.searchParams.set('state',                  state)
  url.searchParams.set('prompt',                 'select_account')
  window.location.href = url.toString()
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function BlackboardImport() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const preselectedCourseId = searchParams.get('course_id') || ''

  const [step, setStep]         = useState(STEP_LOGIN)
  const [bbToken, setBbToken]   = useState('')
  const [bbCourses, setBbCourses] = useState([])

  const [selectedBbCourse, setSelectedBbCourse] = useState('')
  const [loadingFiles, setLoadingFiles]         = useState(false)
  const [filesError, setFilesError]             = useState('')
  const [files, setFiles]                       = useState([])
  const [checkedFiles, setCheckedFiles]         = useState(new Set())

  const [studypaceCourses, setStudypaceCourses] = useState([])
  const [targetCourseId, setTargetCourseId]     = useState(preselectedCourseId)

  const [importing, setImporting] = useState(false)
  const [results, setResults]     = useState([])

  // Restore token + courses if we just came back from OAuth
  useEffect(() => {
    const token   = sessionStorage.getItem('bb_token')
    const courses = sessionStorage.getItem('bb_courses')
    if (token) {
      sessionStorage.removeItem('bb_token')
      sessionStorage.removeItem('bb_courses')
      setBbToken(token)
      setBbCourses(JSON.parse(courses || '[]'))
      setStep(STEP_PICK)
    }
    api.getCourses().then(setStudypaceCourses).catch(() => {})
  }, [])

  async function handleSelectBbCourse(courseId) {
    setSelectedBbCourse(courseId)
    setFiles([])
    setCheckedFiles(new Set())
    setFilesError('')
    if (!courseId) return
    setLoadingFiles(true)
    try {
      const data = await api.bbListFiles(courseId, bbToken)
      setFiles(data.files || [])
      setCheckedFiles(new Set((data.files || []).map(f => f.attachment_id)))
    } catch (err) {
      setFilesError(err.message)
    } finally {
      setLoadingFiles(false)
    }
  }

  function toggleFile(id) {
    setCheckedFiles(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  async function handleImport() {
    if (!targetCourseId || checkedFiles.size === 0) return
    const filesToImport = files
      .filter(f => checkedFiles.has(f.attachment_id))
      .map(f => ({
        course_id_ext: selectedBbCourse,
        content_id:    f.content_id,
        filename:      f.filename,
        download_url:  f.download_url || '',
      }))
    setImporting(true)
    setFilesError('')
    try {
      const data = await api.bbImport({
        bb_token:  bbToken,
        course_id: parseInt(targetCourseId, 10),
        files:     filesToImport,
      })
      setResults(data.results || [])
      setStep(STEP_DONE)
    } catch (err) {
      setFilesError(err.message)
    } finally {
      setImporting(false)
    }
  }

  const okCount  = results.filter(r => r.status === 'ok').length
  const errCount = results.filter(r => r.status === 'error').length
  const returnUrl = targetCourseId ? `/courses/${targetCourseId}` : '/courses'

  return (
    <div className="page mx-auto max-w-md space-y-4">
      <div className="flex items-center gap-3">
        <button className="text-sm font-semibold text-[var(--text-muted)] hover:text-[var(--text)]"
          onClick={() => navigate(returnUrl)}>← Back</button>
        <h1 className="screen-title text-2xl">Import from E-Learn</h1>
      </div>

      {/* ── Step 1: Sign in ── */}
      {step === STEP_LOGIN && (
        <section className="surface p-6 space-y-5 text-center">
          <div>
            <h2 className="section-title text-xl">Sign in with KU Connect</h2>
            <p className="mt-2 text-sm text-[var(--text-muted)]">
              Uses your existing KU Microsoft account — same login as E-Learn.
            </p>
          </div>

          <button
            className="btn-primary mx-auto flex items-center gap-3 px-6 py-3 text-base"
            onClick={() => startOAuth(`/blackboard-import${preselectedCourseId ? `?course_id=${preselectedCourseId}` : ''}`)}
          >
            <MsLogo />
            Sign in with KU Connect
          </button>

          <p className="text-xs text-[var(--text-faint)]">
            You'll be redirected to Microsoft, then brought straight back.
            Your KU password never touches StudyPace.
          </p>
        </section>
      )}

      {/* ── Step 2: Pick files ── */}
      {step === STEP_PICK && (
        <section className="surface p-5 space-y-4">
          <h2 className="section-title">Pick files to import</h2>

          <div className="space-y-1">
            <label className="block text-xs font-semibold text-[var(--text-muted)]">E-Learn course</label>
            <select className="input w-full" value={selectedBbCourse}
              onChange={e => handleSelectBbCourse(e.target.value)}>
              <option value="">Select a course...</option>
              {bbCourses.map(c => (
                <option key={c.bb_course_id} value={c.bb_course_id}>
                  {c.code ? `${c.code} — ` : ''}{c.name}
                </option>
              ))}
            </select>
          </div>

          {loadingFiles && <p className="text-sm text-[var(--text-muted)]">Loading files…</p>}

          {files.length > 0 && (
            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <label className="text-xs font-semibold text-[var(--text-muted)]">Files</label>
                <div className="flex gap-3">
                  <button className="text-xs text-[var(--accent)] hover:underline"
                    onClick={() => setCheckedFiles(new Set(files.map(f => f.attachment_id)))}>All</button>
                  <button className="text-xs text-[var(--text-faint)] hover:underline"
                    onClick={() => setCheckedFiles(new Set())}>None</button>
                </div>
              </div>
              <div className="surface-soft rounded-lg divide-y divide-[var(--border)]">
                {files.map(f => (
                  <label key={f.attachment_id}
                    className="flex cursor-pointer items-start gap-3 p-3 hover:bg-[var(--surface-hover)]">
                    <input type="checkbox" className="mt-0.5 shrink-0"
                      checked={checkedFiles.has(f.attachment_id)}
                      onChange={() => toggleFile(f.attachment_id)} />
                    <div className="min-w-0">
                      <p className="text-sm font-semibold truncate">{f.content_title || f.filename}</p>
                      <p className="text-xs text-[var(--text-faint)] truncate">{f.filename}</p>
                    </div>
                  </label>
                ))}
              </div>
            </div>
          )}

          {files.length === 0 && selectedBbCourse && !loadingFiles && !filesError && (
            <p className="text-sm text-[var(--text-muted)]">No importable files in this course (PDF, PPTX, DOCX).</p>
          )}

          <div className="space-y-1">
            <label className="block text-xs font-semibold text-[var(--text-muted)]">Add to StudyPace course</label>
            <select className="input w-full" value={targetCourseId}
              onChange={e => setTargetCourseId(e.target.value)}>
              <option value="">Select a course...</option>
              {studypaceCourses.map(c => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          </div>

          {filesError && <p className="text-sm font-semibold text-rose-400">{filesError}</p>}

          <button className="btn-primary w-full"
            disabled={checkedFiles.size === 0 || !targetCourseId || importing}
            onClick={handleImport}>
            {importing
              ? `Importing ${checkedFiles.size} file${checkedFiles.size !== 1 ? 's' : ''}…`
              : `Import ${checkedFiles.size} selected`}
          </button>
          {importing && (
            <p className="text-xs text-center text-[var(--text-faint)]">
              Downloading and processing — this may take a minute…
            </p>
          )}
        </section>
      )}

      {/* ── Step 3: Done ── */}
      {step === STEP_DONE && (
        <section className="surface p-5 space-y-4">
          <h2 className="section-title">
            {okCount} file{okCount !== 1 ? 's' : ''} imported
            {errCount > 0 ? `, ${errCount} failed` : ''}
          </h2>
          <div className="surface-soft rounded-lg divide-y divide-[var(--border)]">
            {results.map((r, i) => (
              <div key={i} className="flex items-start gap-3 p-3">
                <span className={`mt-0.5 text-xs font-bold ${r.status === 'ok' ? 'text-emerald-400' : 'text-rose-400'}`}>
                  {r.status === 'ok' ? '✓' : '✗'}
                </span>
                <div className="min-w-0">
                  <p className="text-sm font-semibold truncate">{r.filename}</p>
                  {r.status === 'error' && <p className="text-xs text-rose-400">{r.message}</p>}
                </div>
              </div>
            ))}
          </div>
          <button className="btn-primary w-full" onClick={() => navigate(returnUrl)}>
            Go to course
          </button>
        </section>
      )}
    </div>
  )
}

function MsLogo() {
  return (
    <svg width="18" height="18" viewBox="0 0 21 21" fill="none" aria-hidden="true">
      <rect x="1"  y="1"  width="9" height="9" fill="#f25022"/>
      <rect x="11" y="1"  width="9" height="9" fill="#7fba00"/>
      <rect x="1"  y="11" width="9" height="9" fill="#00a4ef"/>
      <rect x="11" y="11" width="9" height="9" fill="#ffb900"/>
    </svg>
  )
}
