import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api'

export default function OAuthCallback() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const [error, setError] = useState('')

  useEffect(() => {
    const code = searchParams.get('code')
    const state = searchParams.get('state')
    const errorParam = searchParams.get('error')
    const errorDesc = searchParams.get('error_description')

    if (errorParam) {
      setError(errorDesc || errorParam)
      return
    }

    if (!code) {
      setError('No authorization code returned from Microsoft.')
      return
    }

    const savedState = sessionStorage.getItem('oauth_state')
    const codeVerifier = sessionStorage.getItem('oauth_code_verifier')
    const returnTo = sessionStorage.getItem('oauth_return_to') || '/courses'

    sessionStorage.removeItem('oauth_state')
    sessionStorage.removeItem('oauth_code_verifier')
    sessionStorage.removeItem('oauth_return_to')

    if (state !== savedState) {
      setError('Invalid state parameter — possible CSRF. Please try again.')
      return
    }

    if (!codeVerifier) {
      setError('Missing code verifier. Please try again.')
      return
    }

    const redirectUri = `${window.location.origin}/auth/callback`

    api.bbOAuthExchange({ code, redirect_uri: redirectUri, code_verifier: codeVerifier })
      .then(data => {
        // Pass token + courses to the import page via sessionStorage
        sessionStorage.setItem('bb_token', data.bb_token)
        sessionStorage.setItem('bb_courses', JSON.stringify(data.courses || []))
        navigate(returnTo, { replace: true })
      })
      .catch(err => setError(err.message))
  }, [])

  if (error) {
    return (
      <div className="page mx-auto max-w-md space-y-4 pt-16 text-center">
        <p className="text-lg font-semibold text-rose-400">Sign-in failed</p>
        <p className="text-sm text-[var(--text-muted)]">{error}</p>
        <button className="btn-primary px-6" onClick={() => navigate('/courses')}>
          Back to courses
        </button>
      </div>
    )
  }

  return (
    <div className="page mx-auto max-w-md pt-24 text-center">
      <p className="text-sm font-semibold text-[var(--text-muted)]">Signing you in…</p>
    </div>
  )
}
