import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { anonymizeCourseTitle, courseDisplayName } from '../courseLabels'

const STUDY_PLAN_KEY = 'studypace.studyPlan.active'
const RESCHEDULE_KEY = 'studypace.timetable.rescheduled'
const TARGET_RANGES_KEY = 'studypace.calendar.targetRanges'
const MAX_ASSESSMENT_TARGETS = 24

export default function Timetable() {
  const [activePlan, setActivePlan] = useState(() => readActiveStudyPlan())
  const [blocks, setBlocks] = useState([])
  const [rescheduled, setRescheduled] = useState(() => readRescheduledBlocks())
  const [draggingKey, setDraggingKey] = useState('')
  const [dropDate, setDropDate] = useState('')
  const [courses, setCourses] = useState([])
  const [routeLoading, setRouteLoading] = useState(Boolean(activePlan))
  const [error, setError] = useState('')
  const [quickAdd, setQuickAdd] = useState(() => ({
    title: '',
    courseId: '',
    date: localISODate(),
  }))
  const [quickAddOpen, setQuickAddOpen] = useState(false)
  const [savingQuickAdd, setSavingQuickAdd] = useState(false)

  const today = localISODate()

  useEffect(() => {
    let active = true
    const savedPlan = readActiveStudyPlan()
    setActivePlan(savedPlan)
    setRouteLoading(Boolean(savedPlan))
    setError('')

    const coursesPromise = api.getCourses().catch(() => [])

    coursesPromise
      .then(courseItems => {
        if (!active) return
        setCourses(courseItems)
        setQuickAdd(prev => ({
          ...prev,
          courseId: prev.courseId || String(savedPlan?.courseId || courseItems[0]?.id || ''),
        }))
      })
      .catch(err => {
        if (active) setError(err.message || 'Could not load timetable.')
      })

    if (!savedPlan) {
      setBlocks([])
      setRouteLoading(false)
      return () => {
        active = false
      }
    }

    coursesPromise
      .then(courseItems => loadTimetableBlocks(savedPlan, today, courseItems, partialBlocks => {
        if (!active) return
        setBlocks(mergeBlocks(partialBlocks))
        setRouteLoading(false)
      }))
      .then(blockItems => {
        if (!active) return
        setBlocks(mergeBlocks(blockItems))
      })
      .catch(err => {
        if (active) setError(err.message || 'Could not load timetable.')
      })
      .finally(() => {
        if (active) setRouteLoading(false)
      })

    return () => {
      active = false
    }
  }, [today])

  const grouped = useMemo(() => {
    const map = {}
    for (const block of applyRescheduledDates(blocks, rescheduled)) {
      if (!map[block.date]) map[block.date] = []
      map[block.date].push(block)
    }
    return map
  }, [blocks, rescheduled])

  const dates = Object.keys(grouped).sort()
  const studyBlocks = blocks.filter(block => block.status !== 'assessment' && block.status !== 'adjust' && block.planned_minutes > 0)
  const nextDate = dates.find(date => date >= today) || dates[0] || ''
  const canQuickAdd = courses.length > 0

  async function saveQuickAdd() {
    const title = quickAdd.title.trim()
    const courseId = Number(quickAdd.courseId || courses[0]?.id)
    const course = courses.find(item => Number(item.id) === courseId)
    if (!title || !quickAdd.date || !course || savingQuickAdd) return

    setSavingQuickAdd(true)
    setError('')
    try {
      const saved = await api.createAssessment({
        course_id: course.id,
        title,
        date: quickAdd.date,
        type: storageTypeFromTitle(title),
        notes: 'Added from timetable.',
      })
      const savedPlan = readActiveStudyPlan()
      setActivePlan(savedPlan)
      setRouteLoading(Boolean(savedPlan))
      const refreshed = savedPlan ? await loadTimetableBlocks(savedPlan, today, courses) : []
      setBlocks(mergeBlocks([...refreshed, assessmentMarker(saved, course)]))
      setQuickAdd(prev => ({ ...prev, title: '', date: today }))
      setQuickAddOpen(false)
    } catch (err) {
      setError(err.message || 'Could not add this deadline.')
    } finally {
      setRouteLoading(false)
      setSavingQuickAdd(false)
    }
  }

  function moveBlock(block, nextDate) {
    if (!canMoveBlock(block) || !nextDate) return
    const key = blockStableKey(block)
    if (block.date === nextDate) {
      if (rescheduled[key]) resetMovedBlock(block)
      return
    }
    const next = { ...rescheduled, [key]: nextDate }
    setRescheduled(next)
    saveRescheduledBlocks(next)
    setDraggingKey('')
    setDropDate('')
  }

  function resetMovedBlock(block) {
    const key = blockStableKey(block)
    if (!rescheduled[key]) return
    const next = { ...rescheduled }
    delete next[key]
    setRescheduled(next)
    saveRescheduledBlocks(next)
  }

  if (!activePlan) {
    return (
      <main className="page space-y-4">
        <header>
          <p className="eyebrow text-[var(--accent)]">Route</p>
          <h1 className="screen-title mt-1">No route yet</h1>
          <p className="mt-2 text-sm font-medium leading-6 text-[var(--text-muted)]">
            Choose a destination first, then this becomes the day-by-day path.
          </p>
        </header>
        <section className="journey-panel">
          <div className="journey-panel-head">
            <div>
              <p className="eyebrow">Next step</p>
              <h2>Set the destination</h2>
              <p>The route appears here after you choose an assessment and save the plan.</p>
            </div>
            <Link className="btn-primary" to="/courses?next=plan">Start route</Link>
          </div>
          <div className="journey-steps mt-4">
            <div className="journey-step journey-step-active">
              <span className="journey-dot" />
              <strong>Slides</strong>
              <p>Load decks</p>
            </div>
            <div className="journey-step">
              <span className="journey-dot" />
              <strong>Target</strong>
              <p>Pick date</p>
            </div>
            <div className="journey-step">
              <span className="journey-dot" />
              <strong>Route</strong>
              <p>Study days</p>
            </div>
          </div>
        </section>
      </main>
    )
  }

  return (
    <main className="page space-y-4">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="eyebrow text-[var(--accent)]">Route</p>
          <h1 className="screen-title mt-1">Study journey</h1>
          <p className="mt-2 text-sm font-medium leading-6 text-[var(--text-muted)]">
            {routeLoading
              ? 'Building the route in the background.'
              : `${studyBlocks.length} study block${studyBlocks.length === 1 ? '' : 's'} with review protected before each assessment.`}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="btn-primary self-start sm:self-auto"
            disabled={!canQuickAdd}
            onClick={() => setQuickAddOpen(open => !open)}
          >
            Add deadline
          </button>
          <Link className="btn-ghost self-start sm:self-auto" to="/calendar">Edit plan</Link>
        </div>
      </header>

      <JourneyRouteSummary dates={dates} grouped={grouped} today={today} loading={routeLoading} />

      {quickAddOpen && (
        <section className="surface p-4">
          <div className="grid gap-3 md:grid-cols-[1fr_180px_180px_auto] md:items-end">
            <label className="space-y-1">
              <span className="eyebrow">Thing to add</span>
              <input
                className="input"
                value={quickAdd.title}
                placeholder="HW2 submission"
                onChange={event => setQuickAdd(prev => ({ ...prev, title: event.target.value }))}
                onKeyDown={event => {
                  if (event.key === 'Enter') {
                    event.preventDefault()
                    saveQuickAdd()
                  }
                }}
              />
            </label>
            <label className="space-y-1">
              <span className="eyebrow">Course</span>
              <select
                className="input"
                value={quickAdd.courseId}
                onChange={event => setQuickAdd(prev => ({ ...prev, courseId: event.target.value }))}
              >
                {courses.map(course => (
                  <option key={course.id} value={course.id}>{courseDisplayName(courses, course)}</option>
                ))}
              </select>
            </label>
            <label className="space-y-1">
              <span className="eyebrow">Date</span>
              <input
                className="input"
                type="date"
                value={quickAdd.date}
                onChange={event => setQuickAdd(prev => ({ ...prev, date: event.target.value }))}
              />
            </label>
            <button
              type="button"
              className="btn-primary"
              disabled={savingQuickAdd || !quickAdd.title.trim() || !quickAdd.date || !quickAdd.courseId}
              onClick={saveQuickAdd}
            >
              {savingQuickAdd ? 'Saving...' : 'Save'}
            </button>
          </div>
        </section>
      )}

      {error && (
        <section className="rounded-lg border border-rose-400/30 bg-rose-500/10 p-3 text-sm font-semibold text-rose-200">
          {error}
        </section>
      )}

      {routeLoading ? (
        <TimetableLoadingState />
      ) : dates.length === 0 ? (
        <section className="surface p-8 text-center">
          <h2 className="section-title text-xl">No scheduled study blocks</h2>
          <p className="mx-auto mt-2 max-w-sm text-sm font-medium text-[var(--text-muted)]">
            The active plan exists, but it has nothing scheduled. Adjust the date or slides.
          </p>
          <Link className="btn-primary mt-5" to="/calendar">Edit plan</Link>
        </section>
      ) : (
        <section className="journey-route space-y-3">
          {dates.map(date => (
            <article
              key={date}
              className={`surface journey-day p-4 transition ${
                date === nextDate ? 'border-[var(--border-strong)]' : ''
              } ${
                dropDate === date ? 'border-[var(--accent)]' : ''
              }`}
              style={dropDate === date ? { background: 'color-mix(in srgb, var(--accent) 6%, var(--surface))' } : undefined}
              onDragOver={event => {
                event.preventDefault()
                setDropDate(date)
              }}
              onDragLeave={() => setDropDate(current => current === date ? '' : current)}
              onDrop={event => {
                event.preventDefault()
                const key = event.dataTransfer.getData('text/plain') || draggingKey
                const block = blocks.find(item => blockStableKey(item) === key)
                if (block) moveBlock(block, date)
              }}
            >
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h2 className="section-title text-lg">{formatDate(date)}</h2>
                  <p className="mt-1 text-xs font-semibold text-[var(--text-faint)]">
                    {grouped[date].length} item{grouped[date].length === 1 ? '' : 's'}
                    {dayMinutes(grouped[date]) > 0 ? ` · ${formatStudyTime(dayMinutes(grouped[date]))}` : ''}
                  </p>
                </div>
                {date === today && <span className="badge text-[var(--accent)]">Today</span>}
              </div>
              <div className={`study-list mt-3 md:grid-cols-2 ${grouped[date].length > 4 ? 'max-h-80 overflow-y-auto pr-1' : ''}`}>
                {grouped[date].map((block, index) => (
                  <TimetableBlock
                    key={`${block.date}-${block.title}-${index}`}
                    block={block}
                    courses={courses}
                    moved={Boolean(rescheduled[blockStableKey(block)])}
                    onDragStart={() => setDraggingKey(blockStableKey(block))}
                    onDragEnd={() => {
                      setDraggingKey('')
                      setDropDate('')
                    }}
                    onReset={() => resetMovedBlock(block)}
                  />
                ))}
              </div>
            </article>
          ))}
        </section>
      )}
    </main>
  )
}

function JourneyRouteSummary({ dates, grouped, today, loading = false }) {
  if (loading) {
    return (
      <section className="journey-panel">
        <div className="journey-panel-head">
          <div>
            <p className="eyebrow">Path ahead</p>
            <h2>Preparing the timetable</h2>
            <p>Your saved target is loaded. Study blocks will appear here in a moment.</p>
          </div>
          <span className="badge text-[var(--accent)]">Working</span>
        </div>
        <div className="journey-steps mt-4">
          <div className="journey-step journey-step-active">
            <span className="journey-dot" />
            <strong>Target</strong>
            <p>Loaded</p>
          </div>
          <div className="journey-step">
            <span className="journey-dot" />
            <strong>Route</strong>
            <p>Building</p>
          </div>
          <div className="journey-step">
            <span className="journey-dot" />
            <strong>Study</strong>
            <p>Ready soon</p>
          </div>
        </div>
      </section>
    )
  }
  if (!dates.length) return null
  const futureDates = dates.filter(date => date >= today)
  const routeDates = futureDates.length ? futureDates : dates
  const startDate = routeDates[0]
  const finishDate = routeDates[routeDates.length - 1]
  const allBlocks = routeDates.flatMap(date => grouped[date] || [])
  const studyCount = allBlocks.filter(block => block.status !== 'assessment' && block.status !== 'adjust').length
  const nextAssessment = allBlocks.find(block => block.status === 'assessment')
  const reviewCount = allBlocks.filter(block => block.status === 'review').length

  return (
    <section className="journey-panel">
      <div className="journey-panel-head">
        <div>
          <p className="eyebrow">Path ahead</p>
          <h2>{formatDate(startDate)} to {formatDate(finishDate)}</h2>
          <p>
            {studyCount} study step{studyCount === 1 ? '' : 's'}
            {reviewCount ? ` · ${reviewCount} review step${reviewCount === 1 ? '' : 's'}` : ''}
          </p>
        </div>
        {nextAssessment && (
          <span className="badge text-[var(--accent)]">
            {displayStudyTitle(nextAssessment.title)} · {formatDate(nextAssessment.date)}
          </span>
        )}
      </div>
      <div className="journey-steps mt-4">
        <div className="journey-step journey-step-active">
          <span className="journey-dot" />
          <strong>Now</strong>
          <p>{formatDate(startDate)}</p>
        </div>
        <div className="journey-step">
          <span className="journey-dot" />
          <strong>Review</strong>
          <p>{reviewCount ? `${reviewCount} protected` : 'Before test'}</p>
        </div>
        <div className="journey-step">
          <span className="journey-dot" />
          <strong>Arrive</strong>
          <p>{formatDate(finishDate)}</p>
        </div>
      </div>
    </section>
  )
}

function TimetableLoadingState() {
  return (
    <section className="journey-route space-y-3" aria-live="polite" aria-busy="true">
      {[0, 1].map(index => (
        <article key={index} className="surface journey-day p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="space-y-2">
              <div className="h-5 w-36 rounded bg-[var(--surface-raised)]" />
              <div className="h-3 w-24 rounded bg-[var(--surface-raised)]" />
            </div>
            <span className="badge text-[var(--text-faint)]">Building</span>
          </div>
          <div className="study-list mt-3 md:grid-cols-2">
            {[0, 1].map(item => (
              <div key={item} className="study-row">
                <div className="space-y-2">
                  <div className="h-4 w-48 max-w-full rounded bg-[var(--surface-raised)]" />
                  <div className="h-3 w-64 max-w-full rounded bg-[var(--surface-raised)]" />
                </div>
              </div>
            ))}
          </div>
        </article>
      ))}
    </section>
  )
}

function TimetableBlock({ block, courses, moved = false, onDragStart, onDragEnd, onReset }) {
  const title = displayStudyTitle(block.title, courses, block.course_id || block.course_name)
  const movable = canMoveBlock(block)
  const rowClass = `study-row ${movable ? 'cursor-grab active:cursor-grabbing' : ''} ${
    moved ? 'border-[var(--accent)]' : ''
  }`
  const rowStyle = moved
    ? { background: 'color-mix(in srgb, var(--accent) 7%, var(--surface))' }
    : undefined
  const content = (
    <>
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="min-w-0 truncate text-sm font-semibold text-[var(--text)]">{title}</div>
          <div className="mt-1 text-xs font-medium text-[var(--text-faint)]">{blockMeta(block, courses)}</div>
        </div>
        {moved && (
          <span className="badge shrink-0 text-[var(--accent)]">Moved</span>
        )}
      </div>
      {moved && (
        <button
          type="button"
          className="mt-3 text-xs font-semibold text-[var(--text-faint)] transition hover:text-[var(--text)]"
          onClick={event => {
            event.preventDefault()
            event.stopPropagation()
            onReset?.()
          }}
        >
          Reset to original day
        </button>
      )}
    </>
  )
  const dragProps = movable
    ? {
      draggable: true,
      onDragStart: event => {
        event.dataTransfer.effectAllowed = 'move'
        event.dataTransfer.setData('text/plain', blockStableKey(block))
        onDragStart?.()
      },
      onDragEnd,
      title: 'Drag to another day',
    }
    : {}

  if (block.status === 'assessment') {
    return (
      <div className="study-row border-amber-300/20 bg-amber-300/5">
        {content}
      </div>
    )
  }

  if (block.status === 'review') {
    return (
      <div
        className="study-row border-[var(--accent)]"
        style={{ background: 'color-mix(in srgb, var(--accent) 6%, var(--surface))' }}
      >
        {content}
      </div>
    )
  }

  if (block.status === 'adjust') {
    return (
      <div className="study-row border-amber-300/25 bg-amber-300/10">
        {content}
      </div>
    )
  }

  if (!block.lecture_id) {
    return <div className={rowClass} style={rowStyle} {...dragProps}>{content}</div>
  }

  return (
    <div className={rowClass} style={rowStyle} {...dragProps}>
      {content}
      <Link
        className="mt-3 inline-flex text-xs font-semibold text-[var(--accent)] transition hover:text-[var(--accent-strong)]"
        to={`/lesson/${block.lecture_id}`}
      >
        Open slides
      </Link>
    </div>
  )
}

async function loadTimetableBlocks(activePlan, today, cachedCourses = null, onPartial = null) {
  const passes = activePlanPasses(activePlan)
  if (activePlan.includeAllAssessments) {
    const [assessments, courses] = await Promise.all([
      api.getAssessments().catch(() => []),
      Array.isArray(cachedCourses) ? Promise.resolve(cachedCourses) : api.getCourses().catch(() => []),
    ])
    const targets = buildAssessmentTargets(assessments, courses, today)
      .slice(0, MAX_ASSESSMENT_TARGETS)

    if (!targets.length) {
      if (onPartial) onPartial([])
      return []
    }

    const targetRanges = readTargetRanges()
    const loadTarget = target => {
      const range = targetLectureRange(target, targetRanges)
      return api.getCalendar({
        days: Math.max(1, daysBetween(today, target.date) + 1),
        assessmentId: target.synthetic ? undefined : target.id,
        courseId: target.course_id,
        assessmentType: assessmentTypeValue(target),
        assessmentDate: target.date,
        lectureStart: range?.start || undefined,
        lectureEnd: range?.end || undefined,
        passes,
      }).catch(() => [])
    }

    const firstResults = await Promise.all(targets.slice(0, 1).map(loadTarget))
    const firstBlocks = balanceCombinedTimetable(firstResults.flat(), today)
    if (onPartial) onPartial(firstBlocks)

    const restResults = await Promise.all(targets.slice(1).map(loadTarget))
    return balanceCombinedTimetable([...firstBlocks, ...restResults.flat()], today)
  }

  const blocks = await api.getCalendar({
    days: Math.max(1, Number(activePlan.days) || 30),
    assessmentId: activePlan.assessmentId || undefined,
    courseId: activePlan.courseId || undefined,
    assessmentType: activePlan.assessmentType || undefined,
    assessmentDate: activePlan.assessmentDate || undefined,
    lectureStart: activePlan.lectureStart || undefined,
    lectureEnd: activePlan.lectureEnd || undefined,
    passes,
  })
  if (onPartial) onPartial(blocks)
  return blocks
}

function activePlanPasses(activePlan = {}) {
  // The active plan is the source of truth; the loose localStorage value is
  // only a fallback for plans saved before passes were stored on the plan.
  if (activePlan.passes !== undefined && activePlan.passes !== null) return clampPasses(activePlan.passes)
  return clampPasses(localStorage.getItem('studypace.calendar.passes') || 1)
}

function readTargetRanges() {
  try {
    const saved = JSON.parse(localStorage.getItem(TARGET_RANGES_KEY) || '{}')
    return saved && typeof saved === 'object' && !Array.isArray(saved) ? saved : {}
  } catch {
    return {}
  }
}

function targetLectureRange(target = {}, ranges = {}) {
  const saved = (!target.synthetic && ranges[`assessment:${target.id}`]) || ranges[`course:${target.course_id}`]
  if (!saved) return null
  const start = Math.max(1, Math.round(Number(saved.start) || 1))
  const end = Math.max(start, Math.round(Number(saved.end) || start))
  return { start, end }
}

function clampPasses(value) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return 1
  return Math.max(1, Math.min(3, Math.round(parsed)))
}

function buildAssessmentTargets(assessments, courses, today) {
  const realTargets = assessments
    .filter(item => item.date >= today && item.title?.trim().length >= 3)
    .map(item => ({ ...item, synthetic: false }))

  const courseTargets = courses
    .filter(course => course.exam_date && course.exam_date >= today)
    .filter(course => !realTargets.some(item => item.course_id === course.id))
    .map(course => ({
      id: `course-${course.id}`,
      synthetic: true,
      course_id: course.id,
      title: 'Final Exam',
      date: course.exam_date,
      type: 'exam',
    }))

  return [...realTargets, ...courseTargets].sort((a, b) => a.date.localeCompare(b.date))
}

function assessmentMarker(assessment, course) {
  const type = assessmentTypeValue(assessment)
  return {
    assessment_id: assessment.id,
    course_id: course.id,
    course_name: course.name || '',
    course_color: course.color || '',
    assessment_title: assessment.title,
    assessment_type: type,
    days_until_assessment: null,
    date: assessment.date,
    title: assessment.title,
    planned_minutes: 0,
    status: 'assessment',
    priority: type,
  }
}

function storageTypeFromTitle(title = '') {
  const lower = String(title).toLowerCase()
  if (lower.includes('quiz')) return 'quiz'
  if (lower.includes('midterm') || lower.includes('mid-term')) return 'midterm'
  if (lower.includes('final')) return 'final'
  if (lower.includes('hw') || lower.includes('homework') || lower.includes('assignment') || lower.includes('project') || lower.includes('submission')) return 'assignment'
  return 'assignment'
}

function mergeBlocks(blocks) {
  const seen = new Set()
  const merged = []
  for (const block of blocks || []) {
    const key = [
      block.date,
      block.status,
      block.assessment_id || '',
      block.lecture_id || '',
      block.topic_id || '',
      block.title || '',
      block.course_name || '',
      block.assessment_type || '',
      block.pass_number || '',
      block.pass_total || '',
    ].join('|')
    if (seen.has(key)) continue
    seen.add(key)
    merged.push(block)
  }
  return merged.sort((a, b) => (
    String(a.date).localeCompare(String(b.date)) ||
    statusSort(a.status) - statusSort(b.status) ||
    String(a.course_name || '').localeCompare(String(b.course_name || '')) ||
    String(a.title || '').localeCompare(String(b.title || ''))
  ))
}

// Combining independent per-assessment plans can pile blocks onto the same
// days. Keep every block on the date the backend plan assigned (so the
// timetable matches Today and the plan exactly) and move only the overflow
// from days that exceed the daily capacity.
function balanceCombinedTimetable(blocks = [], today = localISODate()) {
  const base = mergeBlocks((blocks || []).filter(block => block.status !== 'adjust'))
  const fixedBlocks = base.filter(block => block.status === 'assessment' || block.status === 'review')
  const studyBlocks = base.filter(block => block.status !== 'assessment' && block.status !== 'review')
  if (studyBlocks.length < 2) return base

  const latestDate = base
    .map(block => block.date)
    .filter(Boolean)
    .sort()
    .at(-1) || today
  const horizon = enumerateDates(today, latestDate)
  const dailyLimit = Math.max(4, Math.min(7, Math.ceil(studyBlocks.length / Math.max(1, horizon.length)) + 1))
  const dayLoads = new Map(horizon.map(date => [date, 0]))

  for (const block of fixedBlocks) {
    if (block.status === 'review' && dayLoads.has(block.date)) {
      dayLoads.set(block.date, (dayLoads.get(block.date) || 0) + 1)
    }
  }

  const byDate = new Map()
  for (const block of studyBlocks) {
    const date = block.date || today
    if (!byDate.has(date)) byDate.set(date, [])
    byDate.get(date).push(block)
  }

  const kept = []
  const overflow = []
  for (const [date, dateBlocks] of [...byDate.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
    const capacity = Math.max(1, dailyLimit - (dayLoads.get(date) || 0))
    const ordered = [...dateBlocks].sort((a, b) => dueDateForBlock(a).localeCompare(dueDateForBlock(b)))
    const staying = ordered.slice(0, capacity)
    kept.push(...staying)
    overflow.push(...ordered.slice(capacity))
    dayLoads.set(date, (dayLoads.get(date) || 0) + staying.length)
  }

  const moved = overflow
    .sort((a, b) => dueDateForBlock(a).localeCompare(dueDateForBlock(b)))
    .map(block => {
      const due = dueDateForBlock(block)
      const windowEnd = latestStudyDateBeforeDue(due, latestDate)
      const candidates = horizon.filter(date => date >= today && date <= windowEnd)
      const pool = candidates.length ? candidates : horizon
      const origin = block.date || today
      const bestDate = [...pool].sort((a, b) => {
        const aFull = (dayLoads.get(a) || 0) >= dailyLimit ? 1 : 0
        const bFull = (dayLoads.get(b) || 0) >= dailyLimit ? 1 : 0
        return (
          aFull - bFull ||
          Math.abs(daysBetween(origin, a)) - Math.abs(daysBetween(origin, b)) ||
          (dayLoads.get(a) || 0) - (dayLoads.get(b) || 0) ||
          a.localeCompare(b)
        )
      })[0] || origin
      dayLoads.set(bestDate, (dayLoads.get(bestDate) || 0) + 1)
      return {
        ...block,
        date: bestDate,
        days_until_assessment: Number.isFinite(Number(block.days_until_assessment))
          ? Math.max(0, daysBetween(bestDate, due))
          : block.days_until_assessment,
      }
    })

  return mergeBlocks([...kept, ...moved, ...fixedBlocks])
}

function enumerateDates(start, end) {
  const dates = []
  const total = Math.max(0, daysBetween(start, end))
  for (let index = 0; index <= total; index += 1) {
    dates.push(addDaysISO(start, index))
  }
  return dates.length ? dates : [start]
}

function dueDateForBlock(block = {}) {
  if (Number.isFinite(Number(block.days_until_assessment))) {
    return addDaysISO(block.date, Number(block.days_until_assessment))
  }
  return block.date || localISODate()
}

function latestStudyDateBeforeDue(dueDate, fallbackDate) {
  if (!dueDate) return fallbackDate
  const protectedReviewDate = addDaysISO(dueDate, -1)
  const latest = addDaysISO(protectedReviewDate, -1)
  return latest >= localISODate() ? latest : protectedReviewDate
}

function passSort(block = {}) {
  return Number(block.pass_number) || (String(block.status || '').includes('pass 2') ? 2 : 1)
}

function narrowDatesForPass(dates, passNumber, passTotal) {
  if (!dates.length || passTotal <= 1) return dates
  const count = dates.length
  const startIndex = Math.max(0, Math.min(count - 1, Math.floor((passNumber - 1) * count / passTotal)))
  const endIndex = Math.max(startIndex + 1, Math.min(count, Math.ceil(passNumber * count / passTotal)))
  return dates.slice(startIndex, endIndex)
}

function dateScore(date, block, state) {
  const {
    dayLoads,
    dayMinutes,
    dayCourseLoads,
    dayTopicKeys,
    dailyLimit,
    topicKey,
  } = state
  const load = dayLoads.get(date) || 0
  const minutes = dayMinutes.get(date) || 0
  const courseLoads = dayCourseLoads.get(date) || new Map()
  const topicKeys = dayTopicKeys.get(date) || new Set()
  const courseKey = block.course_id || block.course_name || 'course'
  const sameCourse = courseLoads.get(courseKey) || 0
  const sameTopicPenalty = topicKeys.has(topicKey) ? 80 : 0
  const overloadPenalty = Math.max(0, load - dailyLimit + 1) * 40
  return load * 16 + sameCourse * 9 + Math.floor(minutes / 60) * 3 + sameTopicPenalty + overloadPenalty
}

function blockTopicKey(block = {}) {
  return block.topic_id
    ? `topic:${block.topic_id}`
    : block.lecture_id
      ? `lecture:${block.lecture_id}`
      : `${block.title || ''}:${block.course_name || ''}`
}

function statusSort(status = '') {
  if (status === 'adjust') return 0
  if (status === 'assessment') return 3
  if (status === 'review') return 2
  return 1
}

function blockMeta(block, courses) {
  if (block.status === 'assessment') return `${assessmentTypeValue(block)} · assessment day`
  if (block.status === 'adjust') return 'Workload warning'
  const course = courseDisplayName(courses, block.course_id || block.course_name)
  const status = displayStatus(block.status)
  const countdown = block.days_until_assessment === null || block.days_until_assessment === undefined
    ? ''
    : ` · ${daysLeftLabel(block.days_until_assessment)}`
  return `${block.planned_minutes || 0} min · ${status} · ${course}${countdown}`
}

function displayStatus(status = '') {
  if (!status || status === 'new') return 'learn'
  if (status === 'done') return 'done'
  if (status === 'review') return 'review'
  return status.replace(/\bpractice\b/gi, 'review')
}

function assessmentTypeValue(item = {}) {
  const text = `${item.assessment_type || ''} ${item.type || ''} ${item.title || ''}`.toLowerCase()
  if (text.includes('final')) return 'final'
  if (text.includes('midterm') || text.includes('mid-term') || text.includes('semester')) return 'midterm'
  if (text.includes('quiz')) return 'quiz'
  if (text.includes('assignment') || text.includes('project')) return 'assignment'
  return item.type || item.assessment_type || 'assessment'
}

function displayStudyTitle(value = '', courses = [], courseLike = null) {
  const cleaned = cleanTimetableTitle(value)
  return anonymizeCourseTitle(cleaned || 'Lecture', courses, courseLike)
}

function cleanTimetableTitle(value = '') {
  let cleaned = String(value || '')
    .replace(/\s+/g, ' ')
    .replace(/^[*•]\s*/g, '')
    .replace(/^please refer to clause\s+\d+\.?\s*/i, '')
    .replace(/^for\s+/i, '')
    .replace(/\s*[,;:]\s*$/g, '')
    .replace(/\s+(and|or)\s*$/i, '')
    .trim()

  if (/^please refer to clause\s+\d+/i.test(cleaned)) {
    cleaned = cleaned.replace(/^please refer to clause\s+\d+\.?\s*/i, '').replace(/^for\s+/i, '').trim()
  }

  if (cleaned.length > 110) {
    cleaned = cleaned.slice(0, 110).replace(/\s+\S*$/, '').trim()
  }

  return cleaned
}

function applyRescheduledDates(blocks = [], moves = {}) {
  return (blocks || []).map(block => {
    const key = blockStableKey(block)
    const nextDate = moves[key]
    if (!nextDate || !canMoveBlock(block)) return block
    const moved = { ...block, date: nextDate, rescheduled_from: block.date }
    if (Number.isFinite(Number(block.days_until_assessment))) {
      const assessmentDate = addDaysISO(block.date, Number(block.days_until_assessment))
      moved.days_until_assessment = daysBetween(nextDate, assessmentDate)
    }
    return moved
  })
}

function canMoveBlock(block = {}) {
  if (!block || block.status === 'assessment' || block.status === 'review' || block.status === 'adjust') return false
  return Boolean(block.lecture_id || block.topic_id || block.title)
}

function blockStableKey(block = {}) {
  return [
    block.assessment_id || '',
    block.lecture_id || '',
    block.topic_id || '',
    block.status || '',
    block.title || '',
    block.course_id || block.course_name || '',
    block.assessment_type || '',
    block.pass_number || '',
  ].join('|')
}

function dayMinutes(blocks) {
  return blocks
    .filter(block => block.status !== 'assessment' && block.status !== 'adjust')
    .reduce((sum, block) => sum + (block.planned_minutes || 0), 0)
}

function formatStudyTime(minutes) {
  const value = Math.max(0, Number(minutes) || 0)
  const hours = Math.floor(value / 60)
  const mins = value % 60
  if (!hours) return `${mins} min`
  if (!mins) return `${hours} ${hours === 1 ? 'hour' : 'hours'}`
  return `${hours}h ${mins}m`
}

function daysLeftLabel(days) {
  const value = Math.max(0, Number(days) || 0)
  if (value === 0) return 'today'
  if (value === 1) return 'tomorrow'
  return `${value} days left`
}

function daysBetween(start, end) {
  const a = new Date(`${start}T00:00:00`)
  const b = new Date(`${end}T00:00:00`)
  return Math.round((b - a) / 86400000)
}

function addDaysISO(value, days) {
  const date = new Date(`${value}T00:00:00`)
  date.setDate(date.getDate() + days)
  return date.toISOString().slice(0, 10)
}

function formatDate(value) {
  return new Intl.DateTimeFormat(undefined, { weekday: 'long', month: 'short', day: 'numeric' }).format(new Date(`${value}T00:00:00`))
}

function localISODate() {
  const now = new Date()
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000)
  return local.toISOString().slice(0, 10)
}

function readActiveStudyPlan() {
  try {
    const saved = JSON.parse(localStorage.getItem(STUDY_PLAN_KEY) || 'null')
    return saved?.active ? saved : null
  } catch {
    return null
  }
}

function readRescheduledBlocks() {
  try {
    const saved = JSON.parse(localStorage.getItem(RESCHEDULE_KEY) || '{}')
    return saved && typeof saved === 'object' && !Array.isArray(saved) ? saved : {}
  } catch {
    return {}
  }
}

function saveRescheduledBlocks(value) {
  try {
    localStorage.setItem(RESCHEDULE_KEY, JSON.stringify(value || {}))
  } catch {
    // Local schedule tweaks are optional; the generated plan still works without storage.
  }
}
