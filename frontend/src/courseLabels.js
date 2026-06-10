const COURSE_LABEL_OVERRIDES_KEY = 'studypace.courseLabelOverrides'
const STUDY_PLAN_KEY = 'studypace.studyPlan.active'

export function shortCourseName(name = '') {
  return String(name || '').split(' - ')[0].replace(/^COSC\s*/i, 'COSC ').trim()
}

export function courseDisplayName(courses = [], courseLike = null) {
  const list = Array.isArray(courses) ? courses : []
  const index = findCourseIndex(list, courseLike)
  if (index >= 0) {
    const override = courseLabelOverride(list[index].id)
    return nonGenericCourseName(override) || nonGenericCourseName(list[index].name) || `Course ${index + 1}`
  }
  return 'Course'
}

export function courseDisplayNameFromText(courses = [], text = '') {
  return courseDisplayName(courses, text)
}

export function setCourseDisplayNameOverride(courseId, name = '') {
  if (!canUseStorage()) return
  const id = String(courseId || '').trim()
  const cleaned = String(name || '').replace(/\s+/g, ' ').trim()
  if (!id || cleaned.length < 2) return

  const overrides = readCourseLabelOverrides()
  overrides[id] = cleaned
  window.localStorage.setItem(COURSE_LABEL_OVERRIDES_KEY, JSON.stringify(overrides))
  updateActivePlanCourseName(id, cleaned)
}

export function anonymizeCourseTitle(title = '', courses = [], courseLike = null) {
  const cleaned = String(title || '').trim()
  if (!cleaned) return 'Lecture'
  const index = findCourseIndex(courses, courseLike || cleaned)
  const looksLikeCourseCode = /^COSC\s*\d+/i.test(cleaned)
  const matchesCourseName = (courses || []).some(course => {
    const short = shortCourseName(course.name)
    return cleaned.toLowerCase() === short.toLowerCase() || cleaned.toLowerCase() === String(course.name || '').toLowerCase()
  })
  if ((looksLikeCourseCode || matchesCourseName) && index >= 0) return courseDisplayName(courses, courses[index])

  let display = cleaned
  for (let i = 0; i < (courses || []).length; i += 1) {
    const course = courses[i]
    const short = shortCourseName(course.name)
    if (!short) continue
    const pattern = new RegExp(`\\b${escapeRegExp(short).replace(/\\s+/g, '\\\\s*')}\\b`, 'ig')
    if (pattern.test(display)) {
      display = display.replace(pattern, ' ')
      display = display.replace(/\bKhalifa University\b/ig, ' ')
      display = display.replace(/\s+/g, ' ').trim()
      return display || courseDisplayName(courses, course)
    }
  }
  return cleaned
}

function courseLabelOverride(courseId) {
  const value = readCourseLabelOverrides()[String(courseId || '')]
  return String(value || '').trim()
}

function nonGenericCourseName(value = '') {
  const cleaned = String(value || '').replace(/\s+/g, ' ').trim()
  if (!cleaned) return ''
  return /^course\s+\d+$/i.test(cleaned) ? '' : cleaned
}

function readCourseLabelOverrides() {
  if (!canUseStorage()) return {}
  try {
    const saved = JSON.parse(window.localStorage.getItem(COURSE_LABEL_OVERRIDES_KEY) || '{}')
    return saved && typeof saved === 'object' ? saved : {}
  } catch {
    return {}
  }
}

function updateActivePlanCourseName(courseId, name) {
  try {
    const saved = JSON.parse(window.localStorage.getItem(STUDY_PLAN_KEY) || 'null')
    if (!saved?.active || String(saved.courseId || '') !== String(courseId)) return
    window.localStorage.setItem(STUDY_PLAN_KEY, JSON.stringify({ ...saved, courseName: name }))
  } catch {
    // Ignore corrupt local plan state; the next saved plan will rewrite it.
  }
}

function canUseStorage() {
  return typeof window !== 'undefined' && Boolean(window.localStorage)
}

function escapeRegExp(value = '') {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function findCourseIndex(courses = [], courseLike = null) {
  if (!Array.isArray(courses) || !courses.length || courseLike === null || courseLike === undefined) return -1

  if (typeof courseLike === 'object') {
    const id = Number(courseLike.id ?? courseLike.course_id)
    if (Number.isFinite(id)) return courses.findIndex(course => Number(course.id) === id)
    if (courseLike.name) return findCourseIndex(courses, courseLike.name)
  }

  const asNumber = Number(courseLike)
  if (Number.isFinite(asNumber) && String(courseLike).trim() !== '') {
    const byId = courses.findIndex(course => Number(course.id) === asNumber)
    if (byId >= 0) return byId
  }

  const text = String(courseLike || '').trim().toLowerCase()
  if (!text) return -1
  const genericMatch = text.match(/^course\s+(\d+)$/)
  if (genericMatch) {
    const index = Number(genericMatch[1]) - 1
    if (index >= 0 && index < courses.length) return index
  }
  return courses.findIndex(course => {
    const name = String(course.name || '').trim().toLowerCase()
    const short = shortCourseName(course.name).toLowerCase()
    return text === name || text === short
  })
}
