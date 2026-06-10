/**
 * api.js — thin wrapper around fetch() for all backend calls.
 * Adds JWT auth headers and handles 401 → redirect to /login.
 */

const API_ORIGIN = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '')
const BASE = API_ORIGIN ? `${API_ORIGIN}/api` : '/api'

const TOKEN_KEY = 'studypace.auth.token'
const USER_KEY = 'studypace.auth.user'
const API_CACHE_PREFIX = 'studypace.api.cache.'
const apiCache = new Map()
const inflight = new Map()

export function getToken() {
  const token = localStorage.getItem(TOKEN_KEY)
  syncTokenUser(token)
  return token
}
export function setToken(t, username = '') {
  const nextUser = normalizeAuthUser(username || usernameFromToken(t))
  const currentUser = normalizeAuthUser(localStorage.getItem(USER_KEY) || '')
  if (nextUser && currentUser && nextUser !== currentUser) {
    clearUserScopedStorage()
  }
  localStorage.setItem(TOKEN_KEY, t)
  if (nextUser) localStorage.setItem(USER_KEY, nextUser)
}
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
  clearApiCache()
}

export function clearApiCache(prefix = '') {
  if (!prefix) {
    apiCache.clear()
    inflight.clear()
    clearPersistentApiCache()
    return
  }
  for (const key of [...apiCache.keys()]) {
    if (key.includes(prefix)) apiCache.delete(key)
  }
  for (const key of [...inflight.keys()]) {
    if (key.includes(prefix)) inflight.delete(key)
  }
  clearPersistentApiCache(prefix)
}

function normalizeAuthUser(value = '') {
  return String(value || '').trim().toLowerCase()
}

function usernameFromToken(token = '') {
  try {
    const [, payload] = String(token).split('.')
    if (!payload) return ''
    const normalized = payload.replace(/-/g, '+').replace(/_/g, '/')
    const padded = normalized.padEnd(normalized.length + ((4 - normalized.length % 4) % 4), '=')
    const json = JSON.parse(window.atob(padded))
    return json.sub || ''
  } catch {
    return ''
  }
}

function clearUserScopedStorage() {
  const keep = new Set([TOKEN_KEY, USER_KEY, 'studypace.theme'])
  const keys = []
  for (let index = 0; index < localStorage.length; index += 1) {
    const key = localStorage.key(index)
    if (key?.startsWith('studypace.') && !keep.has(key)) keys.push(key)
  }
  keys.forEach(key => localStorage.removeItem(key))
}

function syncTokenUser(token) {
  if (!token) return
  const nextUser = normalizeAuthUser(usernameFromToken(token))
  if (!nextUser) return
  const currentUser = normalizeAuthUser(localStorage.getItem(USER_KEY) || '')
  if (currentUser && currentUser !== nextUser) clearUserScopedStorage()
  if (!currentUser && nextUser !== 'admin') clearUserScopedStorage()
  localStorage.setItem(USER_KEY, nextUser)
}

async function publicReq(method, path, body) {
  const headers = {}
  if (body) headers['Content-Type'] = 'application/json'

  const res = await fetch(BASE + path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  })

  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(apiErrorMessage(err, res))
  }
  return res.json()
}

function apiErrorMessage(err, res) {
  const detail = err?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  // FastAPI validation errors arrive as a list of {loc, msg} objects.
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0]
    const field = Array.isArray(first?.loc) ? String(first.loc[first.loc.length - 1]).replace(/_/g, ' ') : ''
    if (first?.msg) return field ? `${field}: ${first.msg}` : first.msg
  }
  if (res.status >= 500) return `The server hit an error (${res.status}). Please try again.`
  return res.statusText || `Request failed (${res.status})`
}

async function req(method, path, body, options = {}) {
  const timeoutMs = options.timeoutMs || 15000
  const cacheMs = method === 'GET' ? Number(options.cacheMs) || 0 : 0
  const token = getToken()
  const cacheKey = cacheMs ? `${token || 'public'}:${method}:${path}` : ''
  if (cacheKey) {
    const cached = apiCache.get(cacheKey)
    if (cached && cached.expires > Date.now()) return cached.value
    const persisted = readPersistentApiCache(cacheKey)
    if (persisted) {
      apiCache.set(cacheKey, persisted)
      return persisted.value
    }
    if (inflight.has(cacheKey)) return inflight.get(cacheKey)
  }

  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)

  const headers = {}
  if (body) headers['Content-Type'] = 'application/json'
  if (token) headers['Authorization'] = `Bearer ${token}`

  const run = (async () => {
    let res
    try {
      res = await fetch(BASE + path, {
        method,
        headers,
        body: body ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      })
    } catch (error) {
      if (error?.name === 'AbortError') {
        throw new Error('The local service did not respond in time, so I stopped waiting instead of freezing the app.')
      }
      throw error
    } finally {
      clearTimeout(timer)
    }

    if (res.status === 401) {
      clearToken()
      window.location.href = '/login'
      throw new Error('Session expired. Please log in again.')
    }

    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(apiErrorMessage(err, res))
    }
    const value = res.status === 204 ? null : await res.json()
    if (cacheKey) {
      const cachedValue = { value, expires: Date.now() + cacheMs }
      apiCache.set(cacheKey, cachedValue)
      writePersistentApiCache(cacheKey, cachedValue)
    }
    if (method !== 'GET' && options.invalidate !== false) clearApiCache()
    return value
  })()

  if (cacheKey) {
    inflight.set(cacheKey, run)
    run.finally(() => inflight.delete(cacheKey)).catch(() => {})
  }
  return run
}

function persistentApiKey(cacheKey) {
  let hash = 0
  for (let index = 0; index < cacheKey.length; index += 1) {
    hash = ((hash << 5) - hash + cacheKey.charCodeAt(index)) | 0
  }
  return `${API_CACHE_PREFIX}${Math.abs(hash)}`
}

function readPersistentApiCache(cacheKey) {
  try {
    const raw = localStorage.getItem(persistentApiKey(cacheKey))
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (!parsed || parsed.rawKey !== cacheKey || parsed.expires <= Date.now()) {
      localStorage.removeItem(persistentApiKey(cacheKey))
      return null
    }
    return { value: parsed.value, expires: parsed.expires }
  } catch {
    return null
  }
}

function writePersistentApiCache(cacheKey, cachedValue) {
  try {
    localStorage.setItem(persistentApiKey(cacheKey), JSON.stringify({
      rawKey: cacheKey,
      value: cachedValue.value,
      expires: cachedValue.expires,
    }))
  } catch {
    // Local storage can fill up with large slide payloads; memory cache still works.
  }
}

function clearPersistentApiCache(prefix = '') {
  const keys = []
  for (let index = 0; index < localStorage.length; index += 1) {
    const key = localStorage.key(index)
    if (!key?.startsWith(API_CACHE_PREFIX)) continue
    if (!prefix) {
      keys.push(key)
      continue
    }
    try {
      const parsed = JSON.parse(localStorage.getItem(key) || '{}')
      if (String(parsed.rawKey || '').includes(prefix)) keys.push(key)
    } catch {
      keys.push(key)
    }
  }
  keys.forEach(key => localStorage.removeItem(key))
}

export const api = {
  // Auth
  authConfig: () => publicReq('GET', '/auth/config'),
  login: (data) => publicReq('POST', '/auth/login', data),
  register: (data) => publicReq('POST', '/auth/register', data),
  googleLogin: (data) => publicReq('POST', '/auth/google', data),
  me: () => req('GET', '/auth/me', null, { cacheMs: 30000 }),

  // Courses
  getCourses: () => req('GET', '/courses/', null, { cacheMs: 300000 }),
  createCourse: (data) => req('POST', '/courses/', data),
  updateCourse: (id, data) => req('PUT', `/courses/${id}`, data),
  deleteCourse: (id) => req('DELETE', `/courses/${id}`),

  // Topics
  getTopics: (courseId) => req('GET', `/topics/${courseId ? `?course_id=${courseId}` : ''}`, null, { cacheMs: 30000 }),
  createTopic: (data) => req('POST', '/topics/', data),
  updateTopic: (id, data) => req('PUT', `/topics/${id}`, data),
  deleteTopic: (id) => req('DELETE', `/topics/${id}`),

  // Daily plan
  getTodayPlan: () => req('GET', '/plan/today'),
  getPlanForDate: (d) => req('GET', `/plan/${d}`),
  markDayOff: (d, reason) => req('POST', `/plan/day-off/${d}?reason=${encodeURIComponent(reason)}`),
  unmarkDayOff: (d) => req('DELETE', `/plan/day-off/${d}`),

  // Sessions
  getSessions: (options = {}) => {
    const params = new URLSearchParams()
    if (options.fromDate) params.set('from_date', options.fromDate)
    if (options.toDate) params.set('to_date', options.toDate)
    const query = params.toString()
    return req('GET', `/sessions/${query ? `?${query}` : ''}`, null, { cacheMs: 5000 })
  },
  createSession: (data) => req('POST', '/sessions/', data),
  completeSession: (id, data) => req('POST', `/sessions/${id}/complete`, data),

  // Quiz
  getQuestions: (topicId) => req('GET', `/quiz/${topicId}/questions`),
  submitQuiz: (data) => req('POST', '/quiz/submit', data),
  getAttempts: (topicId) => req('GET', `/quiz/attempts${topicId ? `?topic_id=${topicId}` : ''}`),

  // Assessments
  getAssessments: () => req('GET', '/assessments/', null, { cacheMs: 300000 }),
  getUpcoming: (days = 14) => req('GET', `/assessments/upcoming?days=${days}`, null, { cacheMs: 300000 }),
  createAssessment: (data) => req('POST', '/assessments/', data),
  updateAssessment: (id, data) => req('PUT', `/assessments/${id}`, data),
  deleteAssessment: (id) => req('DELETE', `/assessments/${id}`),

  // Settings
  getSettings: () => req('GET', '/settings/', null, { cacheMs: 300000 }),
  updateSettings: (data) => req('PATCH', '/settings/', data),

  // Learning
  getLearningOverview: () => req('GET', '/learning/overview', null, { cacheMs: 60000 }),
  getCourseDetail: (courseId) => req('GET', `/learning/courses/${courseId}`, null, { cacheMs: 120000 }),
  uploadSyllabus: (courseId, data) => req('POST', `/learning/courses/${courseId}/syllabus`, data, { timeoutMs: 120000 }),
  uploadLecture: (courseId, data) => req('POST', `/learning/courses/${courseId}/upload`, data, { timeoutMs: 120000 }),
  updateGradeItem: (itemId, data) => req('PATCH', `/learning/grade-items/${itemId}`, data),
  getLesson: (lectureId) => req('GET', `/learning/lectures/${lectureId}`, null, { cacheMs: 15000 }),
  deleteLecture: (lectureId) => req('DELETE', `/learning/lectures/${lectureId}`),
  completeLesson: (lectureId) => req('POST', `/learning/lectures/${lectureId}/complete`),
  getLessonQuestions: (lectureId) => req('GET', `/learning/lectures/${lectureId}/questions`, null, { cacheMs: 15000 }),
  checkLessonQuestion: (lectureId, questionId, data) => req('POST', `/learning/lectures/${lectureId}/questions/${questionId}/check`, data),
  submitLessonQuiz: (lectureId, data) => req('POST', `/learning/lectures/${lectureId}/quiz`, data),
  getReviewQueue: () => req('GET', '/learning/review'),
  getCalendar: (options = {}) => {
    const params = new URLSearchParams()
    if (typeof options === 'number') params.set('days', String(options))
    else {
      if (options.days) params.set('days', String(options.days))
      if (options.assessmentId) params.set('assessment_id', String(options.assessmentId))
      if (options.courseId) params.set('course_id', String(options.courseId))
      if (options.assessmentType) params.set('assessment_type', String(options.assessmentType))
      if (options.assessmentDate) params.set('assessment_date', String(options.assessmentDate))
      if (options.lectureStart) params.set('lecture_start', String(options.lectureStart))
      if (options.lectureEnd) params.set('lecture_end', String(options.lectureEnd))
      if (options.passes) params.set('passes', String(options.passes))
    }
    const query = params.toString()
    return req('GET', `/learning/calendar${query ? `?${query}` : ''}`, null, { cacheMs: 120000 })
  },
  regeneratePlan: () => req('POST', '/learning/calendar/regenerate'),
  getRetention: () => req('GET', '/learning/retention'),
  getBlackboardConnector: () => req('GET', '/learning/connectors/blackboard'),

  // E-Learn / Blackboard import
  bbOAuthExchange: (data) => req('POST', '/blackboard/oauth/exchange', data, { timeoutMs: 30000 }),
  bbListFiles: (bb_course_id, bb_token) => req('POST', `/blackboard/courses/${bb_course_id}/files`, { bb_token }, { timeoutMs: 30000 }),
  bbImport: (data) => req('POST', '/blackboard/import', data, { timeoutMs: 120000 }),
  syncKuAiInstructor: (data) => req('POST', '/learning/connectors/ku-ai-instructor/sync', data),
  getInstructorDashboard: () => req('GET', '/learning/instructor'),
}
