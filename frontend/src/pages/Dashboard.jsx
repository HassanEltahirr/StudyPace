import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { anonymizeCourseTitle, courseDisplayName } from '../courseLabels'
import { flameLabel, identityForXp, readStreakFreezes, weeklyStudyRecap } from '../gamification'

const COMPLETED_TASK_PREFIX = 'studypace.today.completed'
const PLAN_SNAPSHOT_PREFIX = 'studypace.today.snapshot'
const RECOVERY_PREFIX = 'studypace.today.recovered'
const MASTER_SNAPSHOT_MODE = 'master-v3'
const SINGLE_SNAPSHOT_MODE = 'single'
const MAX_TODAY_TASKS = 18

export default function Dashboard() {
  const [overview, setOverview] = useState(() => emptyOverview())
  const [courses, setCourses] = useState([])
  const [profile, setProfile] = useState(null)
  const [plan, setPlan] = useState(null)
  const [activePlan, setActivePlan] = useState(() => readActiveStudyPlan())
  const [completed, setCompleted] = useState(new Set())
  const [nextStudyDay, setNextStudyDay] = useState({ loading: false, date: '', items: [] })
  const [showNextStudyDay, setShowNextStudyDay] = useState(false)
  const [recoveryNotice, setRecoveryNotice] = useState(null)
  const [planLoading, setPlanLoading] = useState(false)
  const [loadError, setLoadError] = useState('')
  const navigate = useNavigate()

  useEffect(() => {
    let cancelled = false
    const savedPlan = readActiveStudyPlan()
    const todaySnapshot = savedPlan ? snapshotPlanForToday(savedPlan, localISODate()) : null
    setActivePlan(savedPlan)
    setPlan(todaySnapshot)
    setLoadError('')
    setPlanLoading(Boolean(savedPlan && !todaySnapshot))

    api.me()
      .then(value => { if (!cancelled) setProfile(value) })
      .catch(() => {})

    api.getCourses()
      .then(value => { if (!cancelled) setCourses(value) })
      .catch(() => {})

    api.getLearningOverview()
      .then(value => { if (!cancelled) setOverview(value) })
      .catch(error => {
        if (!cancelled) setLoadError(error?.message || 'Could not load your dashboard.')
      })

    if (savedPlan) {
      loadTodayFromActivePlan(savedPlan)
        .then(value => { if (!cancelled) setPlan(value) })
        .catch(error => {
          if (!cancelled) setLoadError(error?.message || 'Could not load today’s route.')
        })
        .finally(() => {
          if (!cancelled) setPlanLoading(false)
        })
    }

    return () => {
      cancelled = true
    }
  }, [])

  const planIsActive = Boolean(activePlan)
  const tasks = useMemo(() => planIsActive ? normalizeTasks(plan, overview, courses) : [], [plan, overview, courses, planIsActive])
  const doneCount = tasks.filter(task => completed.has(task.key)).length
  const totalMinutes = tasks.reduce((sum, task) => sum + task.minutes, 0)
  const completedMinutes = tasks.filter(task => completed.has(task.key)).reduce((sum, task) => sum + task.minutes, 0)
  const remainingTasks = useMemo(() => tasks.filter(task => !completed.has(task.key)), [tasks, completed])
  const doneTasks = useMemo(() => tasks.filter(task => completed.has(task.key)), [tasks, completed])
  const remainingCount = remainingTasks.length
  const pct = tasks.length ? Math.round(doneCount / tasks.length * 100) : 0
  const streakCount = Math.max(0, Number(overview?.streak) || 0)
  const identity = identityForXp()
  const freezes = readStreakFreezes()
  const weeklyRecap = weeklyStudyRecap()

  useEffect(() => {
    if (!plan?.date || !tasks.length) {
      setCompleted(new Set())
      return
    }

    let cancelled = false
    const saved = readCompletedTaskKeys(plan.date)
    api.getSessions({ fromDate: plan.date, toDate: plan.date })
      .then(sessions => {
        if (!cancelled) setCompleted(completedSetFromTasks(tasks, sessions, saved))
      })
      .catch(() => {
        if (!cancelled) setCompleted(completedSetFromTasks(tasks, [], saved))
      })

    return () => {
      cancelled = true
    }
  }, [plan?.date, tasks])

  useEffect(() => {
    if (!plan?.date) return undefined
    const refreshCompleted = () => {
      const saved = readCompletedTaskKeys(plan.date)
      setCompleted(prev => new Set([...prev, ...saved]))
    }
    window.addEventListener('studypace:completion', refreshCompleted)
    window.addEventListener('storage', refreshCompleted)
    return () => {
      window.removeEventListener('studypace:completion', refreshCompleted)
      window.removeEventListener('storage', refreshCompleted)
    }
  }, [plan?.date])

  useEffect(() => {
    if (!activePlan || !plan?.date || !tasks.length) return
    rememberPlanSnapshot(plan.date, activePlan, tasks)
    const recovery = findMissedPlanRecovery(activePlan, plan.date)
    if (!recovery) return
    recoverPlanQuietly(recovery)
  }, [activePlan, plan?.date, tasks])

  useEffect(() => {
    if (!activePlan || !plan?.date || !tasks.length || remainingCount > 0) {
      setNextStudyDay({ loading: false, date: '', items: [] })
      setShowNextStudyDay(false)
      return undefined
    }

    let cancelled = false
    setNextStudyDay({ loading: true, date: '', items: [] })
    setShowNextStudyDay(false)
    loadNextStudyDay(activePlan, plan.date)
      .then(day => {
        if (!cancelled) setNextStudyDay({ loading: false, ...(day || { date: '', items: [] }) })
      })
      .catch(() => {
        if (!cancelled) setNextStudyDay({ loading: false, date: '', items: [] })
      })

    return () => {
      cancelled = true
    }
  }, [activePlan, plan?.date, tasks.length, remainingCount])

  async function markDone(task) {
    if (completed.has(task.key)) return
    try {
      if (task.topicId && plan?.date) {
        const session = await api.createSession({
          topic_id: task.topicId,
          date: plan.date,
          planned_minutes: task.minutes,
        })
        await api.completeSession(session.id, { actual_minutes: task.minutes })
      }
      if (task.lectureId) {
        await api.completeLesson(task.lectureId)
      }
      if (plan?.date) rememberCompletedTask(plan.date, task)
      setCompleted(prev => new Set([...prev, task.key]))
    } catch (e) {
      alert(e.message)
    }
  }

  function recoverPlanQuietly(recovery) {
    if (!recovery || !activePlan) return
    const refreshedPlan = {
      ...activePlan,
      recoveredAt: new Date().toISOString(),
      recoveredFrom: recovery.date,
    }
    markPlanRecovered(recovery)
    localStorage.setItem(STUDY_PLAN_KEY, JSON.stringify(refreshedPlan))
    setActivePlan(refreshedPlan)
    setRecoveryNotice({
      count: recovery.missed.length,
      minutes: recovery.minutes,
    })
  }

  if (planIsActive && loadError && !plan && !planLoading) {
    return (
      <main className="reading-page">
        <section className="surface p-8 text-center">
          <h1 className="section-title text-xl">Could not open StudyPace</h1>
          <p className="mx-auto mt-2 max-w-sm text-sm font-medium text-[var(--text-muted)]">{loadError}</p>
          <button className="btn-primary mt-5" onClick={() => window.location.reload()}>Retry</button>
        </section>
      </main>
    )
  }

  if (!planIsActive) {
    return <NoPlanDashboard overview={overview} courses={courses} />
  }

  const nextTask = remainingTasks[0]
  const laterTasks = remainingTasks.slice(1)
  const remainingMinutes = remainingTasks.reduce((sum, task) => sum + task.minutes, 0)

  return (
    <main className="today-focus-page">
      <header className="today-focus-hero">
        <p className="eyebrow">{formatTodayLong()}</p>
        <h1 className="screen-title">
          {remainingCount > 0 ? `${timeGreeting()}.` : "You're done for today."}
        </h1>
        <div className="today-focus-stats">
          <span><i />{streakCount}-day<br className="sm:hidden" /> streak</span>
          <span>{identity?.xp ?? 0}<br className="sm:hidden" /> XP</span>
          <span className="today-focus-progress">
            <span className="today-progress-track"><span style={{ width: `${pct}%` }} /></span>
            <span>{pct}% of plan</span>
          </span>
        </div>
      </header>

      {recoveryNotice && <RecoveryNotice notice={recoveryNotice} />}

      {planLoading && tasks.length === 0 ? (
        <TodayRouteLoadingState />
      ) : tasks.length === 0 ? (
        <section className="today-empty-card">
          <p className="eyebrow">No blocks today</p>
          <h2>Nothing is queued for today.</h2>
          <p>Adjust the target or study window in Plan.</p>
          <Link className="btn-primary mt-5" to="/calendar">Update plan</Link>
        </section>
      ) : remainingCount === 0 ? (
        <>
          <section className="today-empty-card">
            <p className="eyebrow">Today complete</p>
            <h2>Congratulations, you finished today's plan.</h2>
            <p>{doneTasks.length} slide deck{doneTasks.length === 1 ? '' : 's'} finished. You can rest, or preview the next study day.</p>
            {nextStudyDay.loading ? (
              <button className="btn-primary mt-5" disabled>Finding next day...</button>
            ) : nextStudyDay.items?.length ? (
              <button className="btn-primary mt-5" type="button" onClick={() => setShowNextStudyDay(value => !value)}>
                {showNextStudyDay ? 'Hide next day' : 'Show next day'}
              </button>
            ) : (
              <Link className="btn-primary mt-5" to="/calendar">View plan</Link>
            )}
          </section>
          {showNextStudyDay && <NextStudyDayPlan day={nextStudyDay} courses={courses} />}
          <FinishedSlides tasks={doneTasks} />
        </>
      ) : (
        <>
          <section className="today-queue-section">
            <p className="eyebrow">Up next</p>
            <TodayUpNextCard task={nextTask} onStart={() => navigate(nextTask.href)} />
          </section>

          {laterTasks.length > 0 && (
            <section className="today-queue-section">
              <div className="today-section-head">
                <p className="eyebrow">Then today</p>
                <span>{formatMinutesLong(remainingMinutes)} left</span>
              </div>
              <div className="today-queue-list">
                {laterTasks.map(task => (
                  <TodayQueueRow key={task.key} task={task} onOpen={() => navigate(task.href)} />
                ))}
              </div>
            </section>
          )}

          {doneTasks.length > 0 && <FinishedSlides tasks={doneTasks} />}
        </>
      )}

      <p className="today-quote">“Little by little, one travels far.”</p>
    </main>
  )
}

function TodayJourneyPanel({ tasks, doneCount, remainingTasks, pct, loading = false }) {
  if (loading) {
    return (
      <section className="journey-panel">
        <div className="journey-panel-head">
          <div>
            <p className="eyebrow">Current leg</p>
            <h2>Preparing today&apos;s route</h2>
            <p>Your plan is loaded. Study blocks will appear here in a moment.</p>
          </div>
          <span className="badge text-[var(--accent)]">Working</span>
        </div>
        <div className="journey-steps mt-4">
          <div className="journey-step journey-step-active">
            <span className="journey-dot" />
            <strong>Plan</strong>
            <p>Loaded</p>
          </div>
          <div className="journey-step">
            <span className="journey-dot" />
            <strong>Today</strong>
            <p>Building</p>
          </div>
          <div className="journey-step">
            <span className="journey-dot" />
            <strong>Start</strong>
            <p>Ready soon</p>
          </div>
        </div>
      </section>
    )
  }

  const nextTask = remainingTasks[0]
  const hasTasks = tasks.length > 0
  const complete = hasTasks && remainingTasks.length === 0
  const title = complete
    ? 'Today reached'
    : nextTask?.title
      ? cleanStudyTitle(nextTask.title)
      : 'No route for today'
  const detail = complete
    ? 'You finished the current leg. The next study day is waiting.'
    : nextTask
      ? `${nextTask.minutes} min · ${nextTask.course}`
      : 'Open Plan to choose the next destination.'

  return (
    <section className="journey-panel">
      <div className="journey-panel-head">
        <div>
          <p className="eyebrow">Current leg</p>
          <h2>{title}</h2>
          <p>{detail}</p>
        </div>
        <span className="badge text-[var(--accent)]">{pct}%</span>
      </div>
      <div className="journey-steps mt-4">
        <div className={`journey-step ${doneCount > 0 || complete ? 'journey-step-done' : 'journey-step-active'}`}>
          <span className="journey-dot" />
          <strong>Begin</strong>
          <p>{tasks.length} deck{tasks.length === 1 ? '' : 's'}</p>
        </div>
        <div className={`journey-step ${complete ? 'journey-step-done' : remainingTasks.length ? 'journey-step-active' : ''}`}>
          <span className="journey-dot" />
          <strong>Move</strong>
          <p>{remainingTasks.length} left</p>
        </div>
        <div className={`journey-step ${complete ? 'journey-step-active' : ''}`}>
          <span className="journey-dot" />
          <strong>Arrive</strong>
          <p>{complete ? 'Done today' : 'Finish the leg'}</p>
        </div>
      </div>
    </section>
  )
}

function TodayRouteLoadingState() {
  return (
    <section className="space-y-3" aria-live="polite" aria-busy="true">
      <div className="flex items-center justify-between px-1">
        <h2 className="section-title">Next slides</h2>
        <span className="text-xs font-semibold text-[var(--text-faint)]">Building</span>
      </div>
      <div className="study-list">
        {[0, 1].map(item => (
          <div key={item} className="study-row">
            <div className="flex items-center gap-4">
              <span className="h-2.5 w-2.5 shrink-0 rounded-full bg-[var(--surface-hover)] ring-1 ring-[var(--border-strong)]" />
              <div className="min-w-0 flex-1 space-y-2">
                <div className="h-4 w-52 max-w-full rounded bg-[var(--surface-raised)]" />
                <div className="h-3 w-72 max-w-full rounded bg-[var(--surface-raised)]" />
              </div>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

function GamificationPanel({ identity, streakCount, freezes, weeklyRecap, nextTask }) {
  const title = identity?.title || 'Study Rookie'
  const progress = identity?.progress ?? 0
  const xp = identity?.xp ?? 0
  const nextXp = identity?.nextXp
  const nextLine = nextTask?.title
    ? `Next: ${cleanStudyTitle(nextTask.title)}`
    : 'Next: build the plan, then keep the chain alive.'

  return (
    <section className="gamification-panel">
      <div className="gamification-hero">
        <div>
          <p className="eyebrow">Progress identity</p>
          <h2>{title}</h2>
          <p>{nextXp ? `${xp} XP · ${nextXp - xp} XP to next title` : `${xp} XP · max title unlocked`}</p>
        </div>
        <div className="gamification-flame" aria-label={`${streakCount} day streak`}>
          <span aria-hidden="true">🔥</span>
          <strong>{streakCount}</strong>
        </div>
      </div>

      <div className="gamification-meter" aria-label={`${progress}% toward next title`}>
        <span style={{ width: `${Math.max(0, Math.min(100, progress))}%` }} />
      </div>

      <div className="gamification-grid">
        <div>
          <span>{flameLabel(streakCount)}</span>
          <p>{freezes} streak freeze{freezes === 1 ? '' : 's'} saved</p>
        </div>
        <div>
          <span>This week</span>
          <p>{weeklyRecap.decks} deck{weeklyRecap.decks === 1 ? '' : 's'} done{weeklyRecap.bonusXp ? ` · +${weeklyRecap.bonusXp} bonus XP` : ''}</p>
        </div>
        <div>
          <span>Unfinished pull</span>
          <p>{nextLine}</p>
        </div>
      </div>
    </section>
  )
}

function NoPlanDashboard({ overview, courses }) {
  const courseCount = courses.length || Number(overview?.courses_count) || 0
  const lectureCount = Number(overview?.lectures_count) || 0
  const hasSlides = lectureCount > 0

  return (
    <main className="today-page space-y-4">
      <section className="surface p-5">
        <p className="eyebrow text-[var(--accent)]">{formatToday()}</p>
        <div className="mt-2 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div className="max-w-xl">
            <h1 className="screen-title text-3xl">Start the study journey</h1>
            <p className="mt-2 text-sm font-medium leading-6 text-[var(--text-muted)]">
              Add the slides, choose the destination, and StudyPace will turn the route into today&apos;s work.
            </p>
          </div>
          <Link className="btn-primary self-start sm:self-auto" to={hasSlides ? '/calendar' : '/courses?next=plan'}>
            {hasSlides ? 'Open Plan' : 'Add slides'}
          </Link>
        </div>
      </section>

      <section className="journey-panel">
        <div className="journey-panel-head">
          <div>
            <p className="eyebrow">First route</p>
            <h2>{hasSlides ? 'Choose the first destination' : 'Begin with your slides'}</h2>
            <p>Once the route exists, Today becomes the next step instead of a blank dashboard.</p>
          </div>
          <span className="badge text-[var(--accent)]">{hasSlides ? 'Ready' : 'Start'}</span>
        </div>
        <div className="journey-steps mt-4">
          <div className={`journey-step ${hasSlides ? 'journey-step-done' : 'journey-step-active'}`}>
            <span className="journey-dot" />
            <strong>Load</strong>
            <p>{hasSlides ? `${lectureCount} decks` : 'Add slides'}</p>
          </div>
          <div className={`journey-step ${hasSlides ? 'journey-step-active' : ''}`}>
            <span className="journey-dot" />
            <strong>Plan</strong>
            <p>Pick assessment</p>
          </div>
          <div className="journey-step">
            <span className="journey-dot" />
            <strong>Walk</strong>
            <p>Do today&apos;s leg</p>
          </div>
        </div>
      </section>

      <section className="surface p-4">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-sm font-semibold text-[var(--text)]">Setup</h2>
          <span className="text-xs font-semibold text-[var(--text-faint)]">
            {courseCount} course{courseCount === 1 ? '' : 's'}
          </span>
        </div>
        <div className="mt-3 overflow-hidden rounded-lg border border-[var(--border)]">
          <SetupStep
            title="Load slides"
            detail={hasSlides ? `${lectureCount} slide deck${lectureCount === 1 ? '' : 's'} loaded` : 'Upload lecture slides from Courses'}
            state={hasSlides ? 'Ready' : 'Needed'}
            href="/courses?next=plan"
          />
          <SetupStep
            title="Choose destination"
            detail="Pick quiz, midterm, final, or assignment date"
            state={hasSlides ? 'Next' : 'Waiting'}
            href="/calendar"
          />
          <SetupStep
            title="Follow route"
            detail="The timetable appears after you save the plan"
            state="Waiting"
            href="/timetable"
          />
        </div>
      </section>
    </main>
  )
}

function SetupStep({ title, detail, state, href }) {
  return (
    <Link
      className="flex items-center justify-between gap-4 border-b border-[var(--border)] bg-[var(--surface)] px-3 py-3 text-left last:border-b-0 transition hover:bg-[var(--surface-hover)]"
      to={href}
    >
      <div className="min-w-0">
        <div className="text-sm font-semibold text-[var(--text)]">{title}</div>
        <div className="mt-1 truncate text-xs font-medium text-[var(--text-faint)]">{detail}</div>
      </div>
      <span className={`badge shrink-0 ${state === 'Ready' || state === 'Next' ? 'text-[var(--accent)]' : ''}`}>
        {state}
      </span>
    </Link>
  )
}

function profileGreetingName(profile = {}) {
  const firstName = String(profile?.first_name || '').trim()
  if (firstName) return firstName
  const username = String(profile?.username || '').trim()
  if (username) return username.split('@')[0]
  return 'there'
}

function TodayTaskCard({ task, done, priority = false, onDone, onStart }) {
  return (
    <article
      className={`study-row ${done ? 'opacity-70' : ''} ${priority ? 'border-[var(--accent)]' : ''}`}
      style={priority ? { background: 'color-mix(in srgb, var(--accent) 6%, var(--surface))' } : undefined}
    >
      <div className="flex items-center gap-4">
        <span
          className={`h-2.5 w-2.5 shrink-0 rounded-full ${done ? 'bg-[var(--accent)]' : priority ? 'bg-[var(--accent)]' : 'bg-[var(--surface-hover)] ring-1 ring-[var(--border-strong)]'}`}
          aria-hidden="true"
        />
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2">
            <h2 className="truncate text-base font-semibold text-[var(--text)]">{task.title}</h2>
            {priority && <span className="badge shrink-0 text-[var(--accent)]">Up next</span>}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs font-medium text-[var(--text-faint)]">
            <span className="badge">{task.kind}</span>
            <span>{task.minutes} min</span>
            <span>{task.course}</span>
          </div>
        </div>
        <button
          className={`${priority ? 'btn-primary' : 'btn-ghost'} min-h-8 px-3 py-1.5 text-xs`}
          onClick={onStart}
        >
          Start
        </button>
        <button
          className="btn-ghost min-h-8 px-3 py-1.5 text-xs"
          onClick={onDone}
          disabled={done}
        >
          Done
        </button>
      </div>
    </article>
  )
}

function TodayUpNextCard({ task, onStart }) {
  if (!task) return null
  return (
    <article className="today-upnext-card">
      <div className="min-w-0 flex-1">
        <p className="today-task-meta">{task.course} · {task.kind}</p>
        <h2>{task.title}</h2>
        <div className="today-upnext-stats">
          <span>{task.minutes} min</span>
          <span>{task.kind}</span>
        </div>
      </div>
      <button className="btn-primary today-start-button" type="button" onClick={onStart}>Start</button>
    </article>
  )
}

function TodayQueueRow({ task, onOpen }) {
  return (
    <button className="today-queue-row" type="button" onClick={onOpen}>
      <span className="min-w-0">
        <strong>{task.title}</strong>
        <span>{task.course} · {task.minutes} min · {task.kind}</span>
      </span>
      <em>Open ›</em>
    </button>
  )
}

function RecoveryNotice({ notice }) {
  return (
    <section className="surface p-5">
      <p className="eyebrow text-[var(--accent)]">Back on track</p>
      <h2 className="mt-1 section-title text-xl">Here&apos;s today. You&apos;re still on track.</h2>
      <p className="mt-2 text-sm font-medium leading-6 text-[var(--text-muted)]">
        StudyPace moved {notice.count} unfinished slide deck{notice.count === 1 ? '' : 's'} forward and rebuilt the plan around today.
      </p>
    </section>
  )
}

function FinishedSlides({ tasks }) {
  if (!tasks.length) return null
  return (
    <section className="today-queue-section">
      <p className="eyebrow">Finished today</p>
      <div className="today-finished-list">
        {tasks.map(task => (
          <div key={task.key} className="today-finished-row">
            <span>✓</span>
            <strong>{task.title}</strong>
            <em>{task.course}</em>
          </div>
        ))}
      </div>
    </section>
  )
}

function NextStudyDayPlan({ day, courses }) {
  const items = day?.items || []
  if (!items.length) return null
  const totalMinutes = items.reduce((sum, item) => sum + (Number(item.minutes) || 0), 0)

  return (
    <section className="surface p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="eyebrow text-[var(--accent)]">Next study day</p>
          <h2 className="mt-1 text-sm font-semibold text-[var(--text)]">{formatPlanDate(day.date)}</h2>
        </div>
        <span className="badge">{totalMinutes} min</span>
      </div>
      <div className={`study-list mt-3 ${items.length > 4 ? 'max-h-80 overflow-y-auto pr-1' : ''}`}>
        {items.map(item => (
          <Link key={item.key} className="study-row" to={item.href}>
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-[var(--text)]">
                  {anonymizeCourseTitle(cleanStudyTitle(item.title), courses, item.courseId || item.courseName)}
                </div>
                <div className="mt-1 text-xs font-medium text-[var(--text-faint)]">
                  {item.minutes} min · {item.kind} · {courseDisplayName(courses, item.courseId || item.courseName)}
                </div>
              </div>
              <span className="btn-ghost min-h-8 shrink-0 px-3 py-1.5 text-xs">Start</span>
            </div>
          </Link>
        ))}
      </div>
    </section>
  )
}

function todayFocusLine({ planIsActive, tasks, remainingTasks, totalMinutes, loading = false }) {
  if (!planIsActive) return 'Make a study plan to unlock Today'
  if (loading) return "Building today's route from your saved plan"
  if (!tasks.length) return 'No study blocks are scheduled today'
  if (!remainingTasks.length) return "Today's plan is done"
  const next = remainingTasks[0]
  return `Start with ${next.title}. Today's plan is ${totalMinutes} min total.`
}

function completedSetFromTasks(tasks, sessions, savedKeys) {
  const completedTopics = new Set(
    (sessions || [])
      .filter(session => session.completed)
      .map(session => String(session.topic_id))
  )
  const result = new Set()
  for (const task of tasks) {
    if (isPassSpecificKey(task.key)) {
      if (savedKeys.has(task.key)) result.add(task.key)
      continue
    }
    if (
      savedKeys.has(task.key) ||
      (task.topicId && savedKeys.has(`topic:${task.topicId}`)) ||
      (task.lectureId && savedKeys.has(`lecture:${task.lectureId}`)) ||
      (task.lectureId && savedKeys.has(`task:${task.lectureId}`)) ||
      (task.topicId && completedTopics.has(String(task.topicId)))
    ) {
      result.add(task.key)
    }
  }
  return result
}

function completedStorageKey(date) {
  return `${COMPLETED_TASK_PREFIX}.${date}`
}

function readCompletedTaskKeys(date) {
  try {
    const value = JSON.parse(localStorage.getItem(completedStorageKey(date)) || '[]')
    return new Set(Array.isArray(value) ? value : [])
  } catch {
    return new Set()
  }
}

function rememberCompletedTaskKeys(date, nextKeys) {
  const saved = readCompletedTaskKeys(date)
  for (const key of nextKeys) {
    if (key) saved.add(key)
  }
  localStorage.setItem(completedStorageKey(date), JSON.stringify([...saved]))
}

function rememberCompletedTask(date, task) {
  if (isPassSpecificKey(task.key)) {
    rememberCompletedTaskKeys(date, [task.key])
    return
  }
  rememberCompletedTaskKeys(date, [
    task.key,
    task.topicId ? `topic:${task.topicId}` : '',
    task.lectureId ? `lecture:${task.lectureId}` : '',
    task.lectureId ? `task:${task.lectureId}` : '',
  ])
}

function readAllCompletedTaskKeys() {
  const all = new Set()
  try {
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index)
      if (!key?.startsWith(`${COMPLETED_TASK_PREFIX}.`)) continue
      const date = key.replace(`${COMPLETED_TASK_PREFIX}.`, '')
      for (const value of readCompletedTaskKeys(date)) {
        all.add(value)
      }
    }
  } catch {
    return all
  }
  return all
}

function calendarBlockCompletionKeys(block) {
  if (!block) return []
  const passPart = blockPassPart(block)
  if (passPart) return [calendarBlockTaskKey(block)]
  const keys = []
  if (block.topic_id && block.assessment_id) keys.push(`assessment:${block.assessment_id}:topic:${block.topic_id}`)
  if (block.topic_id) keys.push(`topic:${block.topic_id}`)
  if (block.lecture_id) {
    keys.push(`lecture:${block.lecture_id}`)
    keys.push(`task:${block.lecture_id}`)
  }
  return keys
}

function calendarBlockTaskKey(block) {
  if (!block) return ''
  const passPart = blockPassPart(block)
  if (block.topic_id && block.assessment_id) return `assessment:${block.assessment_id}:topic:${block.topic_id}${passPart}`
  if (block.topic_id) return `topic:${block.topic_id}${passPart}`
  if (block.lecture_id) return `lecture:${block.lecture_id}${passPart}`
  return ''
}

function blockPassPart(block) {
  const passNumber = Number(block?.pass_number)
  return Number.isFinite(passNumber) && passNumber > 0 ? `:pass:${passNumber}` : ''
}

function isPassSpecificKey(key = '') {
  return String(key).includes(':pass:')
}

function calendarBlockIsCompleted(block, dateKeys, allKeys) {
  if (!block) return false
  if (block.status === 'done') return true
  return calendarBlockCompletionKeys(block).some(key => dateKeys.has(key) || allKeys.has(key))
}

function studyBlockIsSchedulable(block) {
  return Boolean(
    block?.date &&
    block.lecture_id &&
    block.status !== 'assessment' &&
    block.status !== 'adjust' &&
    block.planned_minutes > 0
  )
}

function planSnapshotKey(date) {
  return `${PLAN_SNAPSHOT_PREFIX}.${date}`
}

function recoveryStorageKey(planKey, date) {
  return `${RECOVERY_PREFIX}.${date}.${planKey}`
}

function activePlanKey(plan) {
  if (!plan) return 'none'
  return [
    plan.target || '',
    plan.assessmentId || '',
    plan.assessmentDate || '',
    plan.courseId || '',
    plan.assessmentType || '',
    plan.lectureStart || '',
    plan.lectureEnd || '',
    plan.passes || 1,
  ].join(':')
}

function rememberPlanSnapshot(date, activePlan, tasks) {
  if (!date || !tasks.length) return
  const snapshot = {
    date,
    savedAt: new Date().toISOString(),
    planKey: activePlanKey(activePlan),
    mode: activePlan.includeAllAssessments ? MASTER_SNAPSHOT_MODE : SINGLE_SNAPSHOT_MODE,
    tasks: tasks.map(task => ({
      key: task.key,
      topicId: task.topicId,
      lectureId: task.lectureId,
      title: task.title,
      minutes: task.minutes,
      course: task.course,
      kind: task.kind,
      href: task.href,
    })),
  }
  localStorage.setItem(planSnapshotKey(date), JSON.stringify(snapshot))
}

function readPlanSnapshot(date) {
  try {
    const snapshot = JSON.parse(localStorage.getItem(planSnapshotKey(date)) || 'null')
    return snapshot && Array.isArray(snapshot.tasks) ? snapshot : null
  } catch {
    return null
  }
}

function storedSnapshotDates() {
  const dates = []
  for (let index = 0; index < localStorage.length; index += 1) {
    const key = localStorage.key(index)
    if (key?.startsWith(`${PLAN_SNAPSHOT_PREFIX}.`)) {
      dates.push(key.replace(`${PLAN_SNAPSHOT_PREFIX}.`, ''))
    }
  }
  return dates.sort()
}

function findMissedPlanRecovery(activePlan, today) {
  const planKey = activePlanKey(activePlan)
  const dates = storedSnapshotDates().filter(date => date < today).reverse()
  for (const date of dates) {
    if (localStorage.getItem(recoveryStorageKey(planKey, date))) continue
    const snapshot = readPlanSnapshot(date)
    if (!snapshot || snapshot.planKey !== planKey) continue
    const completed = readCompletedTaskKeys(date)
    const missed = snapshot.tasks.filter(task => !completed.has(task.key))
    if (missed.length) {
      return {
        date,
        planKey,
        missed,
        minutes: missed.reduce((sum, task) => sum + (Number(task.minutes) || 0), 0),
      }
    }
  }
  return null
}

function markPlanRecovered(recovery) {
  if (!recovery) return
  localStorage.setItem(recoveryStorageKey(recovery.planKey, recovery.date), new Date().toISOString())
}

function normalizeTasks(plan, overview, courses = []) {
  const liveTodayTasks = liveTodayTaskLookup(overview)

  if (plan?.items?.length) {
    return plan.items
      .map(item => {
        const liveTask = liveTodayTaskForItem(item, liveTodayTasks)
        const lectureId = liveTask?.lecture_id || item.lecture_id
        return {
          key: item.saved_key || (
            item.assessment_id && item.topic_id
              ? `assessment:${item.assessment_id}:topic:${item.topic_id}`
              : item.topic_id
                ? `topic:${item.topic_id}`
                : `lecture:${lectureId}`
          ),
          topicId: item.topic_id || liveTask?.topic_id,
          lectureId,
          title: anonymizeCourseTitle(cleanStudyTitle(item.topic_name || liveTask?.title), courses, item.course_name || liveTask?.course_name),
          course: courseDisplayName(courses, item.course_name || liveTask?.course_name),
          minutes: item.planned_minutes,
          kind: item.assessment_type ? titleCase(item.assessment_type) : item.assessment_boost ? 'Exam soon' : item.avg_quiz_score !== null && item.avg_quiz_score < 0.65 ? 'Review' : 'Study',
          href: lessonHref(lectureId, plan?.date),
        }
      })
      .filter(task => task.lectureId)
      .slice(0, MAX_TODAY_TASKS)
  }

  return (overview?.today_tasks || []).filter(task => task.lecture_id).slice(0, 7).map((task, index) => ({
    key: `task:${task.lecture_id || index}`,
    lectureId: task.lecture_id,
    title: anonymizeCourseTitle(cleanStudyTitle(task.title), courses, task.course_name),
    course: courseDisplayName(courses, task.course_name),
    minutes: task.planned_minutes || 15,
    kind: task.type || 'Study',
    href: `/lesson/${task.lecture_id}`,
  }))
}

function liveTodayTaskLookup(overview) {
  const byTopic = new Map()
  const byTitleCourse = new Map()
  const byTitle = new Map()

  for (const task of overview?.today_tasks || []) {
    if (!task?.lecture_id) continue
    if (task.topic_id) byTopic.set(Number(task.topic_id), task)

    const titleKey = taskLookupKey(task.title)
    const courseKey = taskLookupKey(task.course_name)
    if (titleKey && courseKey) byTitleCourse.set(`${titleKey}::${courseKey}`, task)
    if (titleKey && !byTitle.has(titleKey)) byTitle.set(titleKey, task)
  }

  return { byTopic, byTitleCourse, byTitle }
}

function liveTodayTaskForItem(item, lookup) {
  if (!item || !lookup) return null
  if (item.topic_id && lookup.byTopic.has(Number(item.topic_id))) {
    return lookup.byTopic.get(Number(item.topic_id))
  }

  const titleKey = taskLookupKey(item.topic_name || item.title)
  const courseKey = taskLookupKey(item.course_name)
  if (titleKey && courseKey && lookup.byTitleCourse.has(`${titleKey}::${courseKey}`)) {
    return lookup.byTitleCourse.get(`${titleKey}::${courseKey}`)
  }
  return titleKey ? lookup.byTitle.get(titleKey) || null : null
}

function taskLookupKey(value = '') {
  return cleanStudyTitle(value)
    .toLowerCase()
    .replace(/[–—]/g, '-')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
}

function cleanStudyTitle(value = '') {
  const cleaned = String(value || '')
    .replace(/\s+/g, ' ')
    .replace(/\s*[,;:]\s*$/g, '')
    .replace(/\s+(and|or)\s*$/i, '')
    .replace(/\s*[,;:]\s*$/g, '')
    .trim()
  return cleaned || String(value || '').trim() || 'Lecture'
}

function formatToday() {
  return new Intl.DateTimeFormat(undefined, { weekday: 'long', month: 'short', day: 'numeric' }).format(new Date())
}

function formatTodayLong() {
  return formatToday().toUpperCase()
}

function timeGreeting() {
  const hour = new Date().getHours()
  if (hour < 12) return 'Good morning'
  if (hour < 18) return 'Good afternoon'
  return 'Good evening'
}

function formatMinutesLong(minutes = 0) {
  const total = Math.max(0, Number(minutes) || 0)
  const hours = Math.floor(total / 60)
  const mins = total % 60
  if (hours && mins) return `${hours}h ${mins}m`
  if (hours) return `${hours}h`
  return `${mins}m`
}

function formatPlanDate(value) {
  if (!value) return 'Next day'
  return new Intl.DateTimeFormat(undefined, { weekday: 'long', month: 'short', day: 'numeric' }).format(new Date(`${value}T00:00:00`))
}

function emptyOverview() {
  return {
    xp: 0,
    level: 1,
    streak: 0,
    mastery_score: 0,
    courses_count: 0,
    lectures_count: 0,
    syllabi_count: 0,
    grade_items_count: 0,
    questions_count: 0,
    review_count: 0,
    today_tasks: [],
    weak_topics: [],
    upcoming_deadlines: [],
    badges: [],
  }
}

const STUDY_PLAN_KEY = 'studypace.studyPlan.active'

function readActiveStudyPlan() {
  try {
    const saved = JSON.parse(localStorage.getItem(STUDY_PLAN_KEY) || 'null')
    return saved?.active ? saved : null
  } catch {
    return null
  }
}

async function loadTodayFromActivePlan(activePlan) {
  const today = localISODate()
  const savedToday = snapshotPlanForToday(activePlan, today)
  if (savedToday) return savedToday

  const [assessmentItems, courseItems] = await Promise.all([
    api.getAssessments().catch(() => []),
    api.getCourses().catch(() => []),
  ])
  const upcomingAssessments = filterTargetsForActivePlan(
    buildTodayAssessmentTargets(assessmentItems, courseItems, today),
    activePlan
  )
  const shouldBlendAssessments = Boolean(activePlan.includeAllAssessments)

  if (shouldBlendAssessments && upcomingAssessments.length > 1) {
    if (savedToday?.snapshot_mode === MASTER_SNAPSHOT_MODE) return savedToday
    return loadBalancedAssessmentDay(activePlan, upcomingAssessments, today)
  }

  if (savedToday) return savedToday

  const blocks = await api.getCalendar({
    days: Math.max(1, Number(activePlan.days) || 1),
    assessmentId: activePlan.assessmentId || undefined,
    courseId: activePlan.courseId || undefined,
    assessmentType: activePlan.assessmentType || undefined,
    assessmentDate: activePlan.assessmentDate || undefined,
    lectureStart: activePlan.lectureStart || undefined,
    lectureEnd: activePlan.lectureEnd || undefined,
    passes: activePlan.passes || 1,
  })
  const items = blocks
    .filter(block => block.date === today && studyBlockIsSchedulable(block))
    .map(block => ({
      topic_id: block.topic_id,
      lecture_id: block.lecture_id,
      topic_name: block.title,
      course_name: block.course_name || activePlan.courseName || activePlan.label || 'Course',
      planned_minutes: block.planned_minutes,
      avg_quiz_score: null,
      assessment_boost: Boolean(block.assessment_id),
      assessment_type: block.pass_number ? `pass ${block.pass_number}` : block.status === 'review' ? 'review' : block.assessment_type,
      saved_key: calendarBlockTaskKey(block),
    }))

  return {
    date: today,
    total_minutes: items.reduce((sum, item) => sum + item.planned_minutes, 0),
    items,
    is_day_off: false,
  }
}

async function loadNextStudyDay(activePlan, currentDate) {
  const today = localISODate()
  const blocks = activePlan.includeAllAssessments
    ? await loadMasterPlanBlocks(today, activePlan)
    : await api.getCalendar({
      days: Math.max(1, Number(activePlan.days) || 1),
      assessmentId: activePlan.assessmentId || undefined,
      courseId: activePlan.courseId || undefined,
      assessmentType: activePlan.assessmentType || undefined,
      assessmentDate: activePlan.assessmentDate || undefined,
      lectureStart: activePlan.lectureStart || undefined,
      lectureEnd: activePlan.lectureEnd || undefined,
      passes: activePlan.passes || 1,
    })

  const futureBlocks = mergePlanBlocks(blocks)
    .filter(block => block.date > currentDate && studyBlockIsSchedulable(block))
  const completedEverywhere = readAllCompletedTaskKeys()
  const dates = [...new Set(futureBlocks.map(block => block.date))]
  let nextDate = ''
  let dayBlocks = []

  for (const date of dates) {
    const completedForDate = readCompletedTaskKeys(date)
    const remainingBlocks = futureBlocks
      .filter(block => block.date === date)
      .filter(block => !calendarBlockIsCompleted(block, completedForDate, completedEverywhere))
    if (remainingBlocks.length) {
      nextDate = date
      dayBlocks = remainingBlocks.slice(0, MAX_TODAY_TASKS)
      break
    }
  }

  if (!nextDate) return null
  return {
    date: nextDate,
    items: dayBlocks.map((block, index) => ({
      key: nextStudyDayItemKey(block, index),
      title: block.title || 'Lecture',
      courseId: block.course_id,
      courseName: block.course_name || activePlan.courseName || activePlan.label || 'Course',
      minutes: block.planned_minutes || 0,
      kind: block.pass_number ? `Pass ${block.pass_number}` : block.status === 'review' ? 'Review' : block.assessment_type ? `${titleCase(block.assessment_type)} prep` : 'Study',
      href: block.lecture_id ? lessonHref(block.lecture_id, block.date) : '/calendar',
    })),
  }
}

function lessonHref(lectureId, planDate = '') {
  if (!lectureId) return '/calendar'
  const params = planDate ? `?plan_date=${encodeURIComponent(planDate)}` : ''
  return `/lesson/${lectureId}${params}`
}

function nextStudyDayItemKey(block, index) {
  return [
    block.date || '',
    block.assessment_id || '',
    block.lecture_id || '',
    block.topic_id || '',
    block.pass_number || '',
    block.title || '',
    index,
  ].join(':')
}

async function loadMasterPlanBlocks(today, activePlan = {}) {
  const passes = activePlanPasses(activePlan)
  const [assessmentItems, courseItems] = await Promise.all([
    api.getAssessments().catch(() => []),
    api.getCourses().catch(() => []),
  ])
  const targets = filterTargetsForActivePlan(
    buildTodayAssessmentTargets(assessmentItems, courseItems, today),
    activePlan
  ).slice(0, 24)
  const targetRanges = readTargetRanges()
  const results = await Promise.all(
    targets.map(assessment => {
      const range = targetLectureRange(assessment, targetRanges)
      return api.getCalendar({
        days: Math.max(1, daysBetween(today, assessment.date) + 1),
        assessmentId: assessment.synthetic ? undefined : assessment.id,
        courseId: assessment.course_id,
        assessmentType: assessmentTypeValue(assessment),
        assessmentDate: assessment.date,
        lectureStart: range?.start || undefined,
        lectureEnd: range?.end || undefined,
        passes,
      }).catch(() => [])
    })
  )
  return mergePlanBlocks(results.flat())
}

function mergePlanBlocks(blocks) {
  const seen = new Set()
  const merged = []
  for (const block of blocks || []) {
    const key = [
      block.date,
      block.status,
      block.assessment_id || '',
      block.lecture_id || '',
      block.topic_id || '',
      block.title,
      block.course_name || '',
      block.assessment_type || '',
      block.pass_number || '',
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

function statusSort(status = '') {
  if (status === 'assessment') return 3
  if (status === 'review') return 2
  if (status === 'adjust') return 0
  return 1
}

function snapshotPlanForToday(activePlan, today) {
  const snapshot = readPlanSnapshot(today)
  if (!snapshot?.tasks?.length || snapshot.planKey !== activePlanKey(activePlan)) return null

  const items = snapshot.tasks
    .map(task => {
      const topicId = task.topicId || topicIdFromTaskKey(task.key)
      const lectureId = task.lectureId || lectureIdFromTask(task)
      return {
        topic_id: topicId,
        lecture_id: lectureId,
        topic_name: task.title,
        course_name: task.course || activePlan.courseName || 'Course',
        planned_minutes: task.minutes || 0,
        avg_quiz_score: null,
        assessment_boost: false,
        assessment_type: task.kind || '',
        saved_key: task.key,
      }
    })
    .filter(item => item.lecture_id)

  if (!items.length) return null

  return {
    date: today,
    total_minutes: items.reduce((sum, item) => sum + item.planned_minutes, 0),
    items,
    is_day_off: false,
    from_snapshot: true,
    snapshot_mode: snapshot.mode || '',
  }
}

function topicIdFromTaskKey(key = '') {
  const match = String(key).match(/(?:^|:)topic:(\d+)/)
  return match ? Number(match[1]) : null
}

function lectureIdFromTask(task = {}) {
  if (task.lectureId) return task.lectureId
  const keyMatch = String(task.key || '').match(/(?:^|:)(?:lecture|task):(\d+)/)
  if (keyMatch) return Number(keyMatch[1])
  const hrefMatch = String(task.href || '').match(/\/lesson\/(\d+)/)
  return hrefMatch ? Number(hrefMatch[1]) : null
}

async function loadBalancedAssessmentDay(activePlan, assessments, today) {
  const passes = activePlanPasses(activePlan)
  const settings = await api.getSettings().catch(() => ({ daily_minutes: 135 }))
  const dailyBudget = studyBudget(settings.daily_minutes)
  const targetRanges = readTargetRanges()
  const responses = []
  for (const assessment of assessments.slice(0, 8)) {
    const range = targetLectureRange(assessment, targetRanges)
    const blocks = await api.getCalendar({
      days: 1,
      assessmentId: assessment.synthetic ? undefined : assessment.id,
      courseId: assessment.synthetic ? assessment.course_id : undefined,
      assessmentType: assessmentTypeValue(assessment),
      assessmentDate: assessment.date,
      lectureStart: range?.start || undefined,
      lectureEnd: range?.end || undefined,
      passes,
    }).catch(() => [])
    responses.push({ assessment, blocks })
  }

  const candidatesByAssessment = new Map()
  for (const { assessment, blocks } of responses) {
    const assessmentWeight = assessmentStudyWeight(assessment, blocks, today, dailyBudget)
    const studyBlocks = blocks
      .filter(item => item.date === today && studyBlockIsSchedulable(item))
      .map(block => ({
        assessment,
        block,
        minutes: todayBlockMinutes(block),
        weight: assessmentWeight + blockPriorityScore(block) * 0.05,
      }))
      .sort((a, b) => b.weight - a.weight || String(a.block.title).localeCompare(String(b.block.title)))

    if (studyBlocks.length) {
      candidatesByAssessment.set(assessmentKey(assessment), studyBlocks)
    }
  }

  const selected = []
  const usedTopicKeys = new Set()
  let remainingBudget = dailyBudget

  const addCandidate = candidate => {
    if (!candidate || remainingBudget < 15) return false
    const topicKey = candidate.block.topic_id
      ? `topic:${candidate.block.topic_id}`
      : `${candidate.block.title}:${candidate.block.course_name || ''}`
    if (usedTopicKeys.has(topicKey)) return false
    const minutes = Math.min(candidate.minutes, remainingBudget)
    if (minutes < 15) return false
    usedTopicKeys.add(topicKey)
    selected.push({ ...candidate, minutes })
    remainingBudget -= minutes
    return true
  }

  for (const assessment of assessments) {
    const options = candidatesByAssessment.get(assessmentKey(assessment)) || []
    addCandidate(options.find(candidate => {
      const topicKey = candidate.block.topic_id
        ? `topic:${candidate.block.topic_id}`
        : `${candidate.block.title}:${candidate.block.course_name || ''}`
      return !usedTopicKeys.has(topicKey)
    }))
  }

  const remainingCandidates = [...candidatesByAssessment.values()]
    .flat()
    .sort((a, b) => daysBetween(today, a.assessment.date) - daysBetween(today, b.assessment.date) || b.weight - a.weight)

  for (const candidate of remainingCandidates) {
    if (remainingBudget < 15 || selected.length >= MAX_TODAY_TASKS) break
    addCandidate(candidate)
  }

  const items = selected.map(candidate => {
    const assessment = candidate.assessment
    const block = candidate.block
    return {
      topic_id: block.topic_id,
      lecture_id: block.lecture_id,
      topic_name: block.title,
      course_name: block.course_name || activePlan.courseName || 'Course',
      planned_minutes: candidate.minutes,
      avg_quiz_score: null,
      assessment_boost: true,
      assessment_id: assessment.synthetic ? null : assessment.id,
      assessment_title: assessment.title,
      assessment_type: block.pass_number ? `pass ${block.pass_number}` : block.status === 'review' ? 'review' : assessmentTypeValue(assessment),
      saved_key: calendarBlockTaskKey(block),
    }
  })

  if (!items.length) {
    const fallback = await api.getTodayPlan().catch(() => null)
    if (fallback?.items?.length) return fallback
  }

  return {
    date: today,
    total_minutes: items.reduce((sum, item) => sum + item.planned_minutes, 0),
    items,
    is_day_off: false,
  }
}

function activePlanPasses(activePlan = {}) {
  // The active plan is the source of truth; the loose localStorage value is
  // only a fallback for plans saved before passes were stored on the plan.
  if (activePlan.passes !== undefined && activePlan.passes !== null) return clampPasses(activePlan.passes)
  return clampPasses(localStorage.getItem('studypace.calendar.passes') || 1)
}

function readTargetRanges() {
  try {
    const saved = JSON.parse(localStorage.getItem('studypace.calendar.targetRanges') || '{}')
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

function todayBlockMinutes(block) {
  if (block.status === 'review') return Math.max(30, Math.min(block.planned_minutes || 60, 120))
  return Math.max(15, Math.min(block.planned_minutes || 60, 90))
}

function pickStudyBlock(blocks, usedTopics) {
  const ranked = [...blocks].sort((a, b) => blockPriorityScore(b) - blockPriorityScore(a))
  return ranked.find(block => block.topic_id && !usedTopics.has(block.topic_id)) || ranked[0] || null
}

function allocateStudyMinutes(candidates, budget) {
  const allocations = new Map()
  if (!candidates.length || budget < 15) return allocations

  const sorted = [...candidates].sort((a, b) => b.weight - a.weight)
  const baseMinutes = budget >= sorted.length * 30 ? 30 : 15
  const activeCount = Math.max(1, Math.min(sorted.length, Math.floor(budget / baseMinutes)))
  const active = sorted.slice(0, activeCount)
  const baseTotal = active.length * baseMinutes
  const remaining = Math.max(0, budget - baseTotal)
  const weightTotal = active.reduce((sum, candidate) => sum + candidate.weight, 0) || active.length

  const extras = active.map(candidate => {
    const ideal = remaining * candidate.weight / weightTotal
    const whole = Math.floor(ideal / 15) * 15
    return {
      candidate,
      extra: whole,
      remainder: ideal - whole,
    }
  })

  let leftover = remaining - extras.reduce((sum, item) => sum + item.extra, 0)
  for (const item of extras.sort((a, b) => b.remainder - a.remainder || b.candidate.weight - a.candidate.weight)) {
    if (leftover < 15) break
    item.extra += 15
    leftover -= 15
  }

  for (const item of extras) {
    allocations.set(assessmentKey(item.candidate.assessment), baseMinutes + item.extra)
  }
  return allocations
}

function assessmentStudyWeight(assessment, blocks, today, dailyBudget) {
  const daysLeft = daysBetween(today, assessment.date)
  const urgency = Math.min(1, 14 / (daysLeft + 1))
  const studyBlocks = blocks.filter(block => block.status !== 'assessment')
  const remainingMinutes = studyBlocks.reduce((sum, block) => sum + (block.planned_minutes || 0), 0)
  const workloadPressure = Math.min(1, remainingMinutes / Math.max(dailyBudget, (daysLeft + 1) * dailyBudget))
  const weakTopicScore = studyBlocks.some(block => block.priority === 'weak') ? 1 : 0.35
  const typeWeight = assessmentTypeWeight(assessmentTypeValue(assessment))
  const overduePenalty = daysLeft === 0 ? 1 : 0

  return (
    0.35 * urgency +
    0.25 * workloadPressure +
    0.2 * weakTopicScore +
    0.1 * typeWeight +
    0.1 * overduePenalty
  )
}

function interleaveByCourse(candidates) {
  return [...candidates].sort((a, b) => {
    const days = daysBetween(localISODate(), a.assessment.date) - daysBetween(localISODate(), b.assessment.date)
    if (days !== 0) return days
    return b.weight - a.weight
  })
}

function blockPriorityScore(block) {
  if (block.priority === 'weak') return 4
  if (block.priority === 'final') return 3
  if (block.priority === 'midterm') return 2.5
  if (block.priority === 'exam') return 2
  if (block.priority === 'quiz') return 1.5
  return 1
}

function assessmentTypeWeight(type) {
  if (type === 'final') return 1
  if (type === 'midterm' || type === 'exam') return 0.8
  if (type === 'quiz') return 0.55
  return 0.45
}

function assessmentKey(assessment) {
  return `${assessment.synthetic ? 'course' : 'assessment'}:${assessment.id}`
}

function studyBudget(value) {
  const parsed = Number(value)
  const safe = Number.isFinite(parsed) && parsed > 0 ? parsed : 135
  return Math.max(15, Math.round(safe / 15) * 15)
}

function buildTodayAssessmentTargets(assessments, courses, today) {
  const realTargets = assessments
    .filter(item => item.date >= today && item.title?.trim())
    .map(item => ({ ...item, synthetic: false }))

  const syntheticTargets = courses
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

  return [...realTargets, ...syntheticTargets].sort((a, b) => a.date.localeCompare(b.date))
}

function filterTargetsForActivePlan(targets, activePlan = {}) {
  if (activePlan?.includeAllAssessments) return targets
  if (!activePlan?.courseId) return targets
  return targets.filter(target => Number(target.course_id) === Number(activePlan.courseId))
}

function assessmentTypeValue(item = {}) {
  const text = `${item.type || ''} ${item.title || ''}`.toLowerCase()
  if (text.includes('final')) return 'final'
  if (text.includes('midterm') || text.includes('mid-term') || text.includes('semester')) return 'midterm'
  if (text.includes('quiz')) return 'quiz'
  return item.type || 'exam'
}

function daysBetween(start, end) {
  const a = new Date(`${start}T00:00:00`)
  const b = new Date(`${end}T00:00:00`)
  return Math.max(0, Math.round((b - a) / 86400000))
}

function titleCase(value = '') {
  return value ? value[0].toUpperCase() + value.slice(1) : 'Study'
}

function localISODate() {
  const now = new Date()
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000)
  return local.toISOString().slice(0, 10)
}
