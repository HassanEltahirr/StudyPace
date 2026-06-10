import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { courseDisplayName } from '../courseLabels'

const A_TARGET = 90
const SETTINGS_KEY = 'studypace.grades.gpaSettings'
const SCORES_KEY = 'studypace.grades.scores'
const TARGETS_KEY = 'studypace.grades.targets'

export default function Grades() {
  const [courses, setCourses] = useState([])
  const [courseId, setCourseId] = useState('')
  const [details, setDetails] = useState({})
  const [scores, setScores] = useState(() => safeParse(localStorage.getItem(SCORES_KEY)) || {})
  const [targets, setTargets] = useState(() => safeParse(localStorage.getItem(TARGETS_KEY)) || {})
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settings, setSettings] = useState(() => ({
    currentCgpa: 4,
    completedCredits: 0,
    courseCredits: {},
    ...(safeParse(localStorage.getItem(SETTINGS_KEY)) || {}),
  }))
  const [savingId, setSavingId] = useState(null)
  const [coursesLoading, setCoursesLoading] = useState(true)
  const [detailsLoading, setDetailsLoading] = useState(false)

  useEffect(() => {
    let active = true
    api.getCourses()
      .then(loaded => {
        if (!active) return
        setCourses(loaded)
        setCoursesLoading(false)

        if (!loaded.length) {
          setDetailsLoading(false)
          return
        }

        let remaining = loaded.length
        setDetailsLoading(true)
        for (const course of loaded) {
          api.getCourseDetail(course.id)
            .then(detail => {
              if (!active || !detail) return
              setDetails(prev => ({ ...prev, [course.id]: detail }))
              setScores(prev => mergePersistedScores(prev, { [course.id]: detail }))
            })
            .catch(() => {})
            .finally(() => {
              remaining -= 1
              if (active && remaining <= 0) setDetailsLoading(false)
            })
        }
      })
      .catch(() => {
        if (!active) return
        setCourses([])
        setCoursesLoading(false)
        setDetailsLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  useEffect(() => {
    localStorage.setItem(SCORES_KEY, JSON.stringify(scores))
  }, [scores])

  useEffect(() => {
    localStorage.setItem(TARGETS_KEY, JSON.stringify(targets))
  }, [targets])

  useEffect(() => {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings))
  }, [settings])

  const summaries = useMemo(() => buildCourseSummaries(courses, details, scores, settings), [courses, details, scores, settings])
  const gpa = useMemo(() => calculateGpa(summaries, settings), [summaries, settings])
  const selectedSummary = summaries.find(summary => String(summary.course.id) === String(courseId))
  const selectedCourse = selectedSummary?.course
  const selectedItems = selectedCourse ? details[selectedCourse.id]?.grade_items || [] : []
  const selectedTarget = Number(targets[selectedCourse?.id] ?? A_TARGET)
  const selectedCalc = useMemo(() => calculate(selectedItems, scores, selectedTarget), [selectedItems, scores, selectedTarget])

  async function saveScore(itemId, rawValue) {
    setSavingId(itemId)
    try {
      await api.updateGradeItem(itemId, { current_score: rawValue === '' ? null : Number(rawValue) })
    } finally {
      setSavingId(null)
    }
  }

  function setSetting(key, value) {
    setSettings(prev => ({ ...prev, [key]: value }))
  }

  function setCourseCredits(id, value) {
    setSettings(prev => ({
      ...prev,
      courseCredits: { ...prev.courseCredits, [id]: value },
    }))
  }

  function setCourseTarget(id, value) {
    setTargets(prev => ({ ...prev, [id]: clamp(Number(value), 0, 100) }))
  }

  if (courseId && selectedCourse) {
    return (
      <CourseGradeView
        courses={courses}
        course={selectedCourse}
        items={selectedItems}
        scores={scores}
        target={selectedTarget}
        calc={selectedCalc}
        savingId={savingId}
        onBack={() => setCourseId('')}
        onTargetChange={value => setCourseTarget(selectedCourse.id, value)}
        onScoreChange={(itemId, value) => setScores(prev => ({ ...prev, [itemId]: value }))}
        onScoreBlur={saveScore}
      />
    )
  }

  return (
    <main className="mx-auto min-h-[calc(100vh-7rem)] max-w-2xl px-1 pb-8 text-white">
      <div className="space-y-6" style={{ paddingTop: 'max(0.75rem, env(safe-area-inset-top))' }}>
        <header className="flex items-center justify-between px-1">
          <h1 className="text-[2.65rem] font-black leading-none tracking-normal">Grades</h1>
          <button
            aria-label="GPA setup"
            className="grid h-11 w-11 place-items-center rounded-full border border-white/10 bg-[#1c1c1e] text-sky-400 active:scale-95"
            onClick={() => setSettingsOpen(open => !open)}
          >
            <SettingsGlyph />
          </button>
        </header>

        <section className="rounded-[1.45rem] border border-white/10 bg-[#1c1c1e] p-5 shadow-[0_10px_30px_rgba(0,0,0,0.22)]">
          <div className="flex items-center justify-between gap-4">
            <div className="grid h-14 w-14 shrink-0 place-items-center rounded-full bg-[#6b7280] text-white">
              <Icon name="university" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-2xl font-black">Spring 2026</div>
              <div className="mt-1 text-sm font-bold text-[#8e8e93]">{gpa.termCredits} credits</div>
            </div>
            <div className="text-right">
              <div className="text-4xl font-black leading-none">{gpa.display}</div>
              <div className="mt-1 text-sm font-bold text-[#8e8e93]">{gpa.label}</div>
            </div>
          </div>
          <div className="mt-5 grid grid-cols-3 divide-x divide-white/10 border-t border-white/10 pt-4 text-center">
            <div>
              <div className="text-xl font-black">{summaries.length}</div>
              <div className="mt-1 text-[11px] font-black uppercase tracking-wide text-[#8e8e93]">Classes</div>
            </div>
            <div>
              <div className="text-xl font-black">{gpa.knownCourses}</div>
              <div className="mt-1 text-[11px] font-black uppercase tracking-wide text-[#8e8e93]">Graded</div>
            </div>
            <div>
              <div className="text-xl font-black">{gpa.termDisplay}</div>
              <div className="mt-1 text-[11px] font-black uppercase tracking-wide text-[#8e8e93]">Term</div>
            </div>
          </div>
        </section>

        {settingsOpen && (
          <section className="overflow-hidden rounded-[1.25rem] border border-white/10 bg-[#1c1c1e]">
            <label className="flex items-center justify-between gap-4 border-b border-white/10 px-4 py-3">
              <span className="font-bold">Current CGPA</span>
              <input
                className="w-24 rounded-xl border border-white/10 bg-black px-3 py-2 text-right text-lg font-black text-white outline-none focus:border-sky-400"
                type="number"
                min="0"
                max="4"
                step="0.01"
                value={settings.currentCgpa}
                onChange={e => setSetting('currentCgpa', clamp(Number(e.target.value), 0, 4))}
              />
            </label>
            <label className="flex items-center justify-between gap-4 border-b border-white/10 px-4 py-3">
              <span className="font-bold">Completed credits</span>
              <input
                className="w-24 rounded-xl border border-white/10 bg-black px-3 py-2 text-right text-lg font-black text-white outline-none focus:border-sky-400"
                type="number"
                min="0"
                max="180"
                step="1"
                value={settings.completedCredits}
                onChange={e => setSetting('completedCredits', clamp(Number(e.target.value), 0, 180))}
              />
            </label>
            {summaries.map(summary => (
              <label key={summary.course.id} className="flex items-center justify-between gap-4 border-b border-white/10 px-4 py-3 last:border-b-0">
                <span className="min-w-0 truncate font-bold">{courseDisplayName(courses, summary.course)} credits</span>
                <input
                  className="w-20 rounded-xl border border-white/10 bg-black px-3 py-2 text-right text-lg font-black text-white outline-none focus:border-sky-400"
                  type="number"
                  min="0"
                  max="6"
                  step="1"
                  value={settings.courseCredits?.[summary.course.id] ?? 3}
                  onChange={e => setCourseCredits(summary.course.id, clamp(Number(e.target.value), 0, 6))}
                />
              </label>
            ))}
          </section>
        )}

        <section className="space-y-3">
          <div className="flex items-center justify-between px-1">
            <h2 className="text-3xl font-black tracking-normal">My Classes</h2>
            <Link
              aria-label="Add course material"
              to="/courses"
              className="grid h-11 w-11 place-items-center rounded-full bg-sky-500 text-white active:scale-95"
            >
              <PlusGlyph />
            </Link>
          </div>

          {coursesLoading || (detailsLoading && summaries.length === 0) ? (
            <section className="rounded-[1.25rem] border border-white/10 bg-[#1c1c1e] p-6">
              <div className="text-sm font-black uppercase tracking-wide text-[#8e8e93]">Checking grade weights</div>
              <div className="mt-4 space-y-3">
                {(courses.length ? courses : [1, 2, 3]).map((course, index) => (
                  <div key={course.id || index} className="h-16 rounded-2xl border border-white/10 bg-white/[0.03]" />
                ))}
              </div>
            </section>
          ) : summaries.length === 0 ? (
            <section className="rounded-[1.25rem] border border-white/10 bg-[#1c1c1e] p-6 text-center">
              <h2 className="text-xl font-black">No grade weights yet</h2>
              <p className="mt-2 text-sm font-semibold text-[#8e8e93]">Open a course and add its syllabus.</p>
              <Link className="btn-primary mt-5" to="/courses">Open courses</Link>
            </section>
          ) : (
            <div className="overflow-hidden rounded-[1.25rem] border border-white/10 bg-[#1c1c1e]">
              {summaries.map((summary, index) => (
                <button
                  key={summary.course.id}
                  className="flex w-full items-center gap-3 border-b border-white/10 px-4 py-4 text-left transition active:bg-white/10 last:border-b-0"
                  onClick={() => setCourseId(String(summary.course.id))}
                >
                  <div className={`grid h-12 w-12 shrink-0 place-items-center rounded-full ${iconTone(index)}`}>
                    <Icon name={iconName(index)} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xl font-bold tracking-normal">{courseDisplayName(courses, summary.course)}</div>
                    <div className="mt-1 text-sm font-semibold text-[#8e8e93]">{summary.credits} credits · {summary.knownWeight}% graded</div>
                  </div>
                  <div className="shrink-0 text-right">
                    <div className="text-2xl font-black">{summary.known ? `${formatGrade(summary.estimated)}%` : '4.00'}</div>
                    <div className={`mt-0.5 text-xs font-black ${summary.estimated >= A_TARGET ? 'text-[var(--accent)]' : 'text-amber-400'}`}>
                      {summary.known ? summary.letter : 'Projected'}
                    </div>
                  </div>
                  <ChevronRight />
                </button>
              ))}
            </div>
          )}
        </section>
      </div>
    </main>
  )
}

function CourseGradeView({ courses, course, items, scores, target, calc, savingId, onBack, onTargetChange, onScoreChange, onScoreBlur }) {
  const accent = courseAccent(course.name)
  const current = calc.knownWeight ? calc.estimated : 0

  return (
    <main className="mx-auto min-h-[calc(100vh-7rem)] max-w-2xl px-1 pb-8 text-white">
      <div className="space-y-6" style={{ paddingTop: 'max(0.75rem, env(safe-area-inset-top))' }}>
        <header className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 border-b border-white/10 pb-4">
          <button className="flex items-center gap-1 justify-self-start text-lg font-semibold text-sky-400 active:scale-95" onClick={onBack}>
            <BackChevron />
            Classes
          </button>
          <h1 className="max-w-[11rem] truncate text-center text-lg font-black sm:max-w-xs">{courseDisplayName(courses, course)}</h1>
          <Link className="justify-self-end text-lg font-semibold text-sky-400" to={`/courses/${course.id}`}>Course</Link>
        </header>

        <section className="border-b border-white/10 px-1 pb-7">
          <div className="grid grid-cols-2">
            <div className="border-r border-white/10 pr-5 text-center">
              <p className="text-lg font-semibold text-[#8e8e93]">current grade</p>
              <div className="mt-3 text-[4.25rem] font-black leading-none tracking-normal" style={{ color: accent }}>
                {formatGrade(current)}
                <span className="text-3xl">%</span>
              </div>
            </div>
            <label className="pl-5 text-center">
              <span className="text-lg font-semibold text-[#8e8e93]">target grade</span>
              <span className="mt-3 flex items-end justify-center">
                <input
                  className="w-28 border-b-4 bg-transparent text-center text-[4.25rem] font-black leading-none tracking-normal text-white outline-none"
                  style={{ borderColor: accent }}
                  type="number"
                  min="0"
                  max="100"
                  step="0.1"
                  value={target}
                  onChange={e => onTargetChange(e.target.value)}
                />
                <span className="pb-1 text-3xl font-black text-white">%</span>
              </span>
            </label>
          </div>
        </section>

        <section className="rounded-[1.25rem] border border-white/10 bg-[#1c1c1e] px-4 py-3">
          <div className="flex items-center justify-between gap-3">
            <span className="font-bold text-[#8e8e93]">Needed on remaining work</span>
            <span className="text-2xl font-black">{calc.remainingWeight > 0 ? `${calc.required}/100` : 'Done'}</span>
          </div>
          <div className="mt-2 flex items-center justify-between gap-3 text-sm font-semibold text-[#8e8e93]">
            <span>Best {calc.best}%</span>
            <span>Worst {calc.worst}%</span>
            <span>{calc.knownWeight}% known</span>
          </div>
        </section>

        {items.length === 0 ? (
          <section className="rounded-[1.25rem] border border-white/10 bg-[#1c1c1e] p-6 text-center">
            <h2 className="text-xl font-black">No grade components</h2>
            <p className="mt-2 text-sm font-semibold text-[#8e8e93]">Add the syllabus for this course to extract weights.</p>
            <Link className="btn-primary mt-5" to={`/courses/${course.id}`}>Open course</Link>
          </section>
        ) : (
          <section className="overflow-hidden rounded-[1.25rem] border border-white/10 bg-[#1c1c1e]">
            {items.map(item => (
              <label key={item.id} className="flex items-center justify-between gap-4 border-b border-white/10 px-4 py-4 last:border-b-0">
                <span className="min-w-0">
                  <span className="block truncate text-2xl font-semibold tracking-normal">{displayItemTitle(item.title)}</span>
                  <span className="mt-1 block text-sm font-semibold text-[#8e8e93]">{round(Number(item.weight_pct) || 0)}% weight · {weightedContribution(item, scores[item.id])}</span>
                </span>
                <span className="shrink-0 rounded-2xl border border-white/10 bg-black px-4 py-2 text-right">
                  <span className="block text-xs font-black lowercase text-[#8e8e93]">got</span>
                  <span className="flex items-baseline justify-end">
                    <input
                      className="w-16 bg-transparent text-right text-2xl font-black text-[#8e8e93] outline-none focus:text-white"
                      type="number"
                      min="0"
                      max="100"
                      step="0.1"
                      placeholder="--"
                      value={scores[item.id] ?? ''}
                      onChange={e => onScoreChange(item.id, e.target.value)}
                      onBlur={e => onScoreBlur(item.id, e.target.value)}
                    />
                    <span className="text-xl font-black text-[#8e8e93]">/100</span>
                  </span>
                  {savingId === item.id && <span className="mt-1 block text-[11px] font-black text-sky-400">saving</span>}
                </span>
              </label>
            ))}
          </section>
        )}
      </div>
    </main>
  )
}

function SettingsGlyph() {
  return (
    <svg aria-hidden="true" className="h-5 w-5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.4" viewBox="0 0 24 24">
      <path d="M4 7h10" />
      <path d="M18 7h2" />
      <path d="M16 5v4" />
      <path d="M4 17h2" />
      <path d="M10 17h10" />
      <path d="M8 15v4" />
    </svg>
  )
}

function PlusGlyph() {
  return (
    <svg aria-hidden="true" className="h-6 w-6" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="2.7" viewBox="0 0 24 24">
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </svg>
  )
}

function ChevronRight() {
  return (
    <svg aria-hidden="true" className="h-5 w-5 shrink-0 text-[#636366]" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.7" viewBox="0 0 24 24">
      <path d="m9 18 6-6-6-6" />
    </svg>
  )
}

function BackChevron() {
  return (
    <svg aria-hidden="true" className="h-6 w-6" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.7" viewBox="0 0 24 24">
      <path d="m15 18-6-6 6-6" />
    </svg>
  )
}

function Snapshot({ label, value }) {
  return (
    <div className="rounded-2xl bg-white/5 p-3 text-center">
      <div className="text-2xl font-black">{value}</div>
      <div className="text-[11px] font-black uppercase tracking-wide text-slate-500">{label}</div>
    </div>
  )
}

function buildCourseSummaries(courses, details, scores, settings) {
  return courses.map(course => {
    const items = details[course.id]?.grade_items || []
    const calc = calculate(items, scores, A_TARGET)
    const point = gradePoint(calc.estimated)
    return {
      course,
      ...calc,
      known: calc.knownWeight > 0,
      credits: Number(settings.courseCredits?.[course.id] ?? 3),
      letter: point.letter,
      points: point.points,
      aPossible: calc.best >= A_TARGET,
    }
  }).filter(summary => summary.totalWeight > 0)
}

function calculateGpa(summaries, settings) {
  const known = summaries.filter(summary => summary.known)
  const termCredits = known.reduce((sum, summary) => sum + summary.credits, 0)
  const allCredits = summaries.reduce((sum, summary) => sum + summary.credits, 0)
  const termPoints = known.reduce((sum, summary) => sum + summary.points * summary.credits, 0)
  const termGpa = termCredits ? termPoints / termCredits : 0
  const completedCredits = Number(settings.completedCredits) || 0
  const currentCgpa = clamp(Number(settings.currentCgpa), 0, 4)
  const projected = completedCredits && termCredits
    ? ((currentCgpa * completedCredits) + termPoints) / (completedCredits + termCredits)
    : termGpa

  return {
    termGpa,
    termCredits: allCredits || termCredits,
    knownCourses: known.length,
    display: termCredits ? projected.toFixed(2) : '4.00',
    termDisplay: termCredits ? termGpa.toFixed(2) : '4.00',
    label: completedCredits ? 'Projected CGPA' : 'Estimated term GPA',
  }
}

function calculate(items, scores, target) {
  let earned = 0
  let knownWeight = 0
  let totalWeight = 0

  for (const item of items) {
    const weight = Number(item.weight_pct) || 0
    totalWeight += weight
    const raw = scores[item.id]
    const hasScore = raw !== undefined && raw !== ''
    if (hasScore) {
      const score = clamp(Number(raw), 0, 100)
      earned += score * weight / 100
      knownWeight += weight
    }
  }

  const remainingWeight = Math.max(0, totalWeight - knownWeight)
  const current = knownWeight ? earned / knownWeight * 100 : 0
  const estimatedPoints = knownWeight ? earned + (current * remainingWeight / 100) : 0
  const estimated = totalWeight ? estimatedPoints / totalWeight * 100 : 0
  const best = totalWeight ? (earned + remainingWeight) / totalWeight * 100 : 0
  const worst = totalWeight ? earned / totalWeight * 100 : 0
  const required = remainingWeight ? ((target * totalWeight / 100) - earned) / remainingWeight * 100 : 0
  const requiredForA = remainingWeight ? ((A_TARGET * totalWeight / 100) - earned) / remainingWeight * 100 : 0

  return {
    current: round(current),
    estimated: round(estimated),
    best: round(best),
    worst: round(worst),
    knownWeight: round(knownWeight),
    remainingWeight: round(remainingWeight),
    totalWeight: round(totalWeight),
    required: round(clamp(required, 0, 100)),
    requiredForA: round(clamp(requiredForA, 0, 100)),
  }
}

function gradePoint(score) {
  if (score >= 90) return { letter: 'A', points: 4.0 }
  if (score >= 87) return { letter: 'A-', points: 3.7 }
  if (score >= 84) return { letter: 'B+', points: 3.3 }
  if (score >= 80) return { letter: 'B', points: 3.0 }
  if (score >= 77) return { letter: 'B-', points: 2.7 }
  if (score >= 74) return { letter: 'C+', points: 2.3 }
  if (score >= 70) return { letter: 'C', points: 2.0 }
  if (score >= 60) return { letter: 'D', points: 1.0 }
  return { letter: 'F', points: 0.0 }
}

function weightedContribution(item, raw) {
  if (raw === undefined || raw === '') return 'not entered'
  const score = clamp(Number(raw), 0, 100)
  const weight = Number(item.weight_pct) || 0
  return `${round(score * weight / 100)} pts`
}

function mergePersistedScores(scores, details) {
  const merged = { ...scores }
  for (const detail of Object.values(details)) {
    for (const item of detail?.grade_items || []) {
      if ((merged[item.id] === undefined || merged[item.id] === '') && item.current_score !== null && item.current_score !== undefined) {
        merged[item.id] = String(item.current_score)
      }
    }
  }
  return merged
}

function Icon({ name }) {
  const common = {
    'aria-hidden': 'true',
    className: 'h-7 w-7',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: '2.2',
    strokeLinecap: 'round',
    strokeLinejoin: 'round',
    viewBox: '0 0 24 24',
  }
  if (name === 'university') return <svg {...common}><path d="M3 10h18" /><path d="M5 10V8l7-4 7 4v2" /><path d="M6 10v8M10 10v8M14 10v8M18 10v8" /><path d="M4 18h16M3 21h18" /></svg>
  if (name === 'rocket') return <svg {...common}><path d="M4.5 16.5c-1 1-1.5 3-1.5 3s2-.5 3-1.5" /><path d="M9 15 4 10l6-1 5-5c3-3 6-2 6-2s1 3-2 6l-5 5-1 6-5-5Z" /><path d="M15 9h.01" /></svg>
  if (name === 'atom') return <svg {...common}><circle cx="12" cy="12" r="1.5" /><path d="M12 21c4.4 0 8-4 8-9s-3.6-9-8-9-8 4-8 9 3.6 9 8 9Z" transform="rotate(60 12 12)" /><path d="M12 21c4.4 0 8-4 8-9s-3.6-9-8-9-8 4-8 9 3.6 9 8 9Z" transform="rotate(-60 12 12)" /></svg>
  return <svg {...common}><path d="M12 3v18" /><path d="m6 8 6-5 6 5" /><path d="m6 16 6 5 6-5" /></svg>
}

function iconName(index) {
  return ['robot', 'atom', 'rocket'][index % 3]
}

function iconTone(index) {
  return ['bg-slate-500 text-white', 'bg-fuchsia-500 text-white', 'bg-violet-500 text-white'][index % 3]
}

function courseAccent(name = '') {
  const lower = name.toLowerCase()
  if (lower.includes('operating')) return '#c66be8'
  if (lower.includes('ai')) return '#8b6ce8'
  return '#64748b'
}

function displayItemTitle(title = '') {
  return title
    .replace(/homework/ig, 'Hw')
    .replace(/assignment/ig, 'Hw')
    .replace(/\s+/g, ' ')
    .trim()
}

function formatGrade(value) {
  const rounded = round(value)
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1)
}

function clamp(value, min, max) {
  if (Number.isNaN(value)) return min
  return Math.max(min, Math.min(max, value))
}

function round(value) {
  return Math.round(value * 10) / 10
}

function safeParse(value) {
  try {
    return value ? JSON.parse(value) : null
  } catch {
    return null
  }
}
