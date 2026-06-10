import { useEffect, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api, getToken } from '../api'
import StudyCompletionInteraction from '../components/StudyCompletionInteraction'

export default function Lesson() {
  const { lectureId } = useParams()
  const [searchParams] = useSearchParams()
  const planDateParam = validPlanDate(searchParams.get('plan_date'))
  const [lesson, setLesson] = useState(null)
  const [loading, setLoading] = useState(true)
  const [aiUnavailable, setAiUnavailable] = useState(false)
  const [nextStep, setNextStep] = useState({
    loading: true,
    planActive: false,
    currentBlock: null,
    nextTodayBlock: null,
    futureBlock: null,
  })

  useEffect(() => {
    setLoading(true)
    api.getLesson(lectureId)
      .then(setLesson)
      .finally(() => setLoading(false))
  }, [lectureId])

  useEffect(() => {
    let active = true
    const activePlan = readActiveStudyPlan()
    if (!activePlan) {
      setNextStep({
        loading: false,
        planActive: false,
        currentBlock: null,
        nextTodayBlock: null,
        futureBlock: null,
      })
      return () => {
        active = false
      }
    }

    setNextStep({
      loading: true,
      planActive: true,
      currentBlock: null,
      nextTodayBlock: null,
      futureBlock: null,
    })
    api.getCalendar({
      days: activePlan.days || 30,
      assessmentId: activePlan.assessmentId || undefined,
      courseId: activePlan.courseId || undefined,
      assessmentType: activePlan.assessmentType || undefined,
      assessmentDate: activePlan.assessmentDate || undefined,
      lectureStart: activePlan.lectureStart || undefined,
      lectureEnd: activePlan.lectureEnd || undefined,
      passes: activePlan.passes || 1,
    })
      .then(blocks => {
        if (!active) return
        const currentId = Number(lectureId)
        const studyBlocks = blocks.filter(block =>
          block.lecture_id &&
          block.status !== 'assessment' &&
          block.status !== 'adjust'
        )
        const datedCurrentBlock = planDateParam
          ? studyBlocks.find(block => block.date === planDateParam && Number(block.lecture_id) === currentId)
          : null
        const currentBlock = datedCurrentBlock || studyBlocks.find(block => Number(block.lecture_id) === currentId) || null
        const currentDate = planDateParam || currentBlock?.date || localISODate()
        const currentDayBlocks = studyBlocks.filter(block => block.date === currentDate)
        rememberPlanSnapshotFromBlocks(currentDate, activePlan, currentDayBlocks)
        const completedToday = readCompletedTaskKeys(currentDate)
        const currentIndex = currentDayBlocks.findIndex(block => Number(block.lecture_id) === currentId)
        const nextTodayBlock = currentIndex >= 0
          ? currentDayBlocks.slice(currentIndex + 1).find(block => !blockIsCompleted(block, completedToday)) || null
          : currentDayBlocks.find(block => Number(block.lecture_id) !== currentId && !blockIsCompleted(block, completedToday)) || null
        const futureBlock = studyBlocks.find(block => (
          block.date > currentDate &&
          block.status !== 'done' &&
          !blockIsCompleted(block, readCompletedTaskKeys(block.date))
        )) || null
        setNextStep({ loading: false, planActive: true, currentBlock, nextTodayBlock, futureBlock })
      })
      .catch(() => {
        if (active) {
          setNextStep({
            loading: false,
            planActive: true,
            currentBlock: null,
            nextTodayBlock: null,
            futureBlock: null,
          })
        }
      })

    return () => {
      active = false
    }
  }, [lectureId, planDateParam])

  if (loading) return <div className="py-20 text-center text-sm font-semibold text-[var(--text-faint)]">Loading lesson...</div>
  if (!lesson) return null

  return (
    <article className="reading-page pb-20 text-[var(--text)]">
      <header className="pb-6">
        <div className="flex items-center justify-between gap-3 text-sm font-semibold">
          <Link className="quiet-link" to={`/courses/${lesson.course_id}`}>
            Courses
          </Link>
        </div>
        <p className="mt-6 eyebrow">Lecture</p>
        <h1 className="mt-2 text-3xl font-semibold leading-tight tracking-normal text-[var(--text)] sm:text-4xl">
          {lesson.title}
        </h1>
        <p className="mt-3 text-sm font-medium leading-6 text-[var(--text-muted)]">
          {lesson.source_filename} · {lesson.slides.length} slides · {lesson.estimated_minutes} min
        </p>
      </header>

      {lesson.extraction_error && (
        <section className="mb-6 border-y border-rose-300/30 py-4 text-sm font-semibold text-rose-200">
          {lesson.extraction_error}
        </section>
      )}

      <DeckSummary lesson={lesson} onUnavailable={() => setAiUnavailable(true)} />

      <LectureSlides slides={lesson.slides} />

      <PlanCompletion nextStep={nextStep} lesson={lesson} planDate={planDateParam} onLessonComplete={setLesson} />

      {!aiUnavailable && <TestYourself lesson={lesson} />}
      {lesson.mastery_score >= 0.8 && <LectureVideos lectureId={lesson.id} />}
    </article>
  )
}

function DeckSummary({ lesson, onUnavailable }) {
  const [summary, setSummary] = useState(lesson.ai_summary || '')
  const [status, setStatus] = useState(lesson.ai_summary ? 'ready' : 'pending')

  useEffect(() => {
    if (lesson.ai_summary) {
      setSummary(lesson.ai_summary)
      setStatus('ready')
      return undefined
    }
    let active = true
    let timer = null
    let tries = 0

    function poll() {
      api.getLessonAiSummary(lesson.id)
        .then(result => {
          if (!active) return
          if (result.status === 'ready' && result.summary) {
            setSummary(result.summary)
            setStatus('ready')
            return
          }
          if (result.status === 'unavailable') {
            setStatus('unavailable')
            onUnavailable?.()
            return
          }
          if (tries >= 8) {
            setStatus('unavailable')
            return
          }
          tries += 1
          setStatus('pending')
          timer = setTimeout(poll, 5000)
        })
        .catch(() => {
          if (active) setStatus('unavailable')
        })
    }
    poll()

    return () => {
      active = false
      if (timer) clearTimeout(timer)
    }
  }, [lesson.id, lesson.ai_summary])

  if (status === 'unavailable') return null

  const { bullets, formulas } = parseDeckSummary(summary)

  return (
    <section className="border-t border-[var(--border)] py-7">
      <p className="eyebrow text-[var(--accent)]">Before you start</p>
      <h2 className="mt-2 section-title text-2xl">Deck summary</h2>
      {status === 'pending' ? (
        <p className="mt-4 text-sm font-semibold text-[var(--text-faint)]">Preparing the summary...</p>
      ) : (
        <>
          <ul className="reading-content mt-4 space-y-2">
            {bullets.map((bullet, index) => <li key={index}>{bullet}</li>)}
          </ul>
          {formulas.length > 0 && (
            <div className="surface-soft mt-4 p-4">
              <p className="eyebrow">Key formula</p>
              <p className="mt-2 whitespace-pre-wrap font-mono text-sm leading-6 text-[var(--text)]">
                {formulas.join('\n')}
              </p>
            </div>
          )}
        </>
      )}
    </section>
  )
}

function parseDeckSummary(text = '') {
  const bullets = []
  const formulas = []
  let inFormulaBlock = false

  for (const rawLine of String(text).replace(/\r\n/g, '\n').split('\n')) {
    const line = rawLine.trim()
    if (!line) continue
    const unbulleted = line.replace(/^[-•*]\s*/, '')
    if (/^\*{0,2}key formulas?\b/i.test(unbulleted)) {
      inFormulaBlock = true
      const rest = cleanInlineMarkdown(unbulleted.replace(/^\*{0,2}key formulas?:?\*{0,2}\s*/i, ''))
      if (rest) formulas.push(rest)
      continue
    }
    if (inFormulaBlock) {
      formulas.push(cleanInlineMarkdown(unbulleted))
      continue
    }
    const bullet = line.match(/^[-•*]\s+(.+)$/)
    if (bullet) bullets.push(cleanInlineMarkdown(bullet[1]))
    else bullets.push(cleanInlineMarkdown(line))
  }

  return { bullets, formulas }
}

const PRACTICE_DIFFICULTIES = [
  { value: 'easy', label: 'Easy' },
  { value: 'medium', label: 'Medium' },
  { value: 'hard', label: 'Hard' },
]

const PRACTICE_COUNTS = [3, 5, 10]

function TestYourself({ lesson }) {
  const [difficulty, setDifficulty] = useState('medium')
  const [count, setCount] = useState(5)
  const [questions, setQuestions] = useState([])
  const [answers, setAnswers] = useState({})
  const [checked, setChecked] = useState({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function generate() {
    if (loading) return
    setLoading(true)
    setError('')
    setQuestions([])
    setAnswers({})
    setChecked({})
    try {
      const result = await api.generatePracticeExam(lesson.id, { difficulty, count })
      setQuestions(result.questions || [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const checkedCount = Object.keys(checked).length
  const correctCount = questions.filter((question, index) => checked[index] && answers[index] === question.correct).length

  return (
    <section className="border-t border-[var(--border)] py-7">
      <p className="eyebrow text-[var(--accent)]">Test yourself</p>
      <h2 className="mt-2 section-title text-2xl">Exam-style practice</h2>
      <p className="mt-2 text-sm font-medium leading-6 text-[var(--text-muted)]">
        Calculation and application questions written at final-exam level for this deck.
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        {PRACTICE_DIFFICULTIES.map(option => (
          <button
            key={option.value}
            type="button"
            className={`rounded-lg border px-4 py-2 text-sm font-semibold transition ${
              difficulty === option.value
                ? 'border-[var(--accent)] text-[var(--accent)]'
                : 'border-[var(--border)] text-[var(--text-muted)] hover:border-[var(--border-strong)] hover:text-[var(--text)]'
            }`}
            style={difficulty === option.value ? { background: 'color-mix(in srgb, var(--accent) 8%, var(--surface))' } : undefined}
            onClick={() => setDifficulty(option.value)}
          >
            {option.label}
          </button>
        ))}
        <span className="mx-1 h-6 w-px bg-[var(--border)]" aria-hidden="true" />
        {PRACTICE_COUNTS.map(option => (
          <button
            key={option}
            type="button"
            className={`rounded-lg border px-4 py-2 text-sm font-semibold transition ${
              count === option
                ? 'border-[var(--accent)] text-[var(--accent)]'
                : 'border-[var(--border)] text-[var(--text-muted)] hover:border-[var(--border-strong)] hover:text-[var(--text)]'
            }`}
            style={count === option ? { background: 'color-mix(in srgb, var(--accent) 8%, var(--surface))' } : undefined}
            onClick={() => setCount(option)}
          >
            {option} questions
          </button>
        ))}
        <button type="button" className="btn-primary" disabled={loading} onClick={generate}>
          {loading ? 'Writing questions...' : questions.length ? 'New questions' : 'Test yourself'}
        </button>
      </div>

      {error && (
        <p className="mt-3 text-sm font-semibold text-rose-300">{error}</p>
      )}

      {questions.length > 0 && (
        <div className="mt-5 space-y-4">
          {questions.map((question, index) => (
            <PracticeExamQuestion
              key={index}
              question={question}
              index={index}
              selected={answers[index]}
              isChecked={Boolean(checked[index])}
              onSelect={choice => setAnswers(prev => ({ ...prev, [index]: choice }))}
              onCheck={() => setChecked(prev => ({ ...prev, [index]: true }))}
            />
          ))}
          {checkedCount === questions.length && (
            <p className="text-sm font-semibold text-[var(--text-muted)]">
              {correctCount} of {questions.length} correct on {difficulty}.
            </p>
          )}
        </div>
      )}
    </section>
  )
}

function PracticeExamQuestion({ question, index, selected, isChecked, onSelect, onCheck }) {
  return (
    <article className="surface p-4">
      <p className="eyebrow">Question {index + 1}</p>
      <h3 className="mt-2 text-base font-semibold leading-7 text-[var(--text)]">{question.question}</h3>
      <div className="mt-3 space-y-2">
        {question.choices.map((choice, choiceIndex) => (
          <button
            key={choiceIndex}
            type="button"
            disabled={isChecked}
            className={`w-full rounded-lg border p-3 text-left text-sm font-semibold transition ${practiceChoiceClass(choiceIndex, selected, isChecked, question.correct)}`}
            style={practiceChoiceStyle(choiceIndex, selected, isChecked, question.correct)}
            onClick={() => onSelect(choiceIndex)}
          >
            {choice}
          </button>
        ))}
      </div>
      {!isChecked ? (
        <button
          type="button"
          className="btn-primary mt-3"
          disabled={selected === undefined}
          onClick={onCheck}
        >
          Check answer
        </button>
      ) : (
        <div className="surface-soft mt-3 p-3">
          <p className={`text-sm font-semibold ${selected === question.correct ? 'text-[var(--accent)]' : 'text-rose-300'}`}>
            {selected === question.correct ? 'Correct.' : 'Not quite.'}
          </p>
          <p className="mt-1 text-sm font-medium leading-6 text-[var(--text-muted)]">{question.explanation}</p>
        </div>
      )}
    </article>
  )
}

function practiceChoiceClass(choiceIndex, selected, isChecked, correct) {
  if (isChecked) {
    if (choiceIndex === correct) return 'border-[var(--accent)] text-[var(--text)]'
    if (choiceIndex === selected) return 'border-rose-400/60 text-rose-200'
    return 'border-[var(--border)] text-[var(--text-faint)]'
  }
  return selected === choiceIndex
    ? 'border-[var(--accent)] text-[var(--text)]'
    : 'border-[var(--border)] text-[var(--text-muted)] hover:border-[var(--border-strong)] hover:text-[var(--text)]'
}

function practiceChoiceStyle(choiceIndex, selected, isChecked, correct) {
  if (isChecked && choiceIndex === correct) {
    return { background: 'color-mix(in srgb, var(--accent) 8%, var(--surface))' }
  }
  if (!isChecked && selected === choiceIndex) {
    return { background: 'color-mix(in srgb, var(--accent) 6%, var(--surface))' }
  }
  return undefined
}

function LectureVideos({ lectureId }) {
  const [videos, setVideos] = useState(null)

  useEffect(() => {
    let active = true
    api.getLessonVideos(lectureId)
      .then(result => {
        if (active) setVideos(result.videos || [])
      })
      .catch(() => {
        if (active) setVideos([])
      })
    return () => {
      active = false
    }
  }, [lectureId])

  if (!videos?.length) return null

  return (
    <section className="border-t border-[var(--border)] py-7">
      <p className="eyebrow text-[var(--accent)]">Keep learning</p>
      <h2 className="mt-2 section-title text-2xl">Watch it explained</h2>
      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        {videos.map(video => (
          <a
            key={video.video_id}
            href={video.url}
            target="_blank"
            rel="noreferrer"
            className="surface-soft block overflow-hidden transition hover:border-[var(--border-strong)]"
          >
            <img
              src={video.thumbnail_url}
              alt={video.title}
              loading="lazy"
              className="aspect-video w-full object-cover"
            />
            <div className="p-3">
              <p className="truncate text-sm font-semibold text-[var(--text)]" title={video.title}>{video.title}</p>
              <p className="mt-1 truncate text-xs font-medium text-[var(--text-faint)]">
                {video.channel}{video.duration ? ` · ${video.duration}` : ''}
              </p>
            </div>
          </a>
        ))}
      </div>
    </section>
  )
}

function PlanCompletion({ nextStep, lesson, planDate, onLessonComplete }) {
  const [done, setDone] = useState(() => lesson.mastery_score >= 0.8)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const block = nextStep.currentBlock
    const savedKeys = readCompletedTaskKeys(planDate || block?.date || localISODate())
    setDone(lesson.mastery_score >= 0.8 || Boolean(block && blockIsCompleted(block, savedKeys)))
  }, [lesson.id, lesson.mastery_score, nextStep.currentBlock, planDate])

  async function finishSlides() {
    if (saving) return
    setSaving(true)
    setError('')
    try {
      const updated = await api.completeLesson(lesson.id)
      rememberLessonCompletion(nextStep.currentBlock, lesson, planDate)
      onLessonComplete(updated)
      setDone(true)
      return updated
    } catch (e) {
      setError(e.message)
      throw e
    } finally {
      setSaving(false)
    }
  }

  if (nextStep.loading) {
    return (
      <section className="border-t border-[var(--border)] py-6">
        <div className="surface-soft p-4 text-sm font-semibold text-[var(--text-muted)]">
          Finding next plan step...
        </div>
      </section>
    )
  }

  if (!done) {
    const block = nextStep.currentBlock
    return (
      <section className="border-t border-[var(--border)] py-6">
        <StudyCompletionInteraction
          title={lesson.title}
          meta={block ? `${formatPlanDate(block.date)} · ${block.planned_minutes} min · ${block.course_name || 'Course'}` : `${lesson.slides.length} slides · ${lesson.estimated_minutes} min`}
          completeLabel="Complete"
          completedLabel="Completed"
          loadingLabel="Updating..."
          progressBefore={Math.round((Number(lesson.mastery_score) || 0) * 100)}
          progressAfter={100}
          xpAward={completionXpForLesson(lesson)}
          disabled={saving}
          onComplete={finishSlides}
          errorMessage={error}
          secondary={<Link className="btn-ghost" to="/">Today</Link>}
        />
      </section>
    )
  }

  if (nextStep.nextTodayBlock) {
    const block = nextStep.nextTodayBlock
    return (
      <section className="border-t border-[var(--border)] py-6">
        <div className="surface p-4">
          <p className="eyebrow text-[var(--accent)]">Saved to Today</p>
          <h2 className="mt-2 section-title text-xl">Next in today's plan</h2>
          <p className="mt-2 text-sm font-medium text-[var(--text-muted)]">
            {block.title} · {block.planned_minutes} min · {block.course_name || 'Course'}
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Link className="btn-primary" to={lessonHref(block.lecture_id, block.date)}>Open next slides</Link>
            <Link className="btn-ghost" to="/">Today</Link>
          </div>
        </div>
      </section>
    )
  }

  if (nextStep.planActive) {
    const block = nextStep.futureBlock
    return (
      <section className="border-t border-[var(--border)] py-6">
        <div className="surface p-4">
          <p className="eyebrow text-[var(--accent)]">Today complete</p>
          <h2 className="mt-2 section-title text-xl">Congratulations, you finished today's plan.</h2>
          <p className="mt-2 text-sm font-medium text-[var(--text-muted)]">
            {block ? `Want to peek at ${formatPlanDate(block.date)}? ${block.title} is next.` : 'No more slide decks are scheduled in this plan.'}
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            {block && <Link className="btn-primary" to={lessonHref(block.lecture_id, block.date)}>Start next day</Link>}
            <Link className="btn-ghost" to="/">Today</Link>
            <Link className="btn-ghost" to="/calendar">Plan</Link>
          </div>
        </div>
      </section>
    )
  }

  return (
    <section className="border-t border-[var(--border)] py-6">
      <div className="surface p-4">
        <p className="eyebrow">Slides finished</p>
        <h2 className="mt-2 section-title text-xl">
          Nice work. This deck is done.
        </h2>
        <div className="mt-4 flex flex-wrap gap-2">
          <Link className="btn-primary" to="/">Today</Link>
          <Link className="btn-ghost" to="/courses?next=plan">Make plan</Link>
        </div>
      </div>
    </section>
  )
}

const STUDY_PLAN_KEY = 'studypace.studyPlan.active'
const COMPLETED_TASK_PREFIX = 'studypace.today.completed'
const PLAN_SNAPSHOT_PREFIX = 'studypace.today.snapshot'

function readActiveStudyPlan() {
  try {
    const saved = JSON.parse(localStorage.getItem(STUDY_PLAN_KEY) || 'null')
    return saved?.active ? saved : null
  } catch {
    return null
  }
}

function validPlanDate(value = '') {
  const text = String(value || '').trim()
  return /^\d{4}-\d{2}-\d{2}$/.test(text) ? text : ''
}

function lessonHref(lectureId, planDate = '') {
  if (!lectureId) return '/'
  const date = validPlanDate(planDate)
  return `/lesson/${lectureId}${date ? `?plan_date=${encodeURIComponent(date)}` : ''}`
}

function completionXpForLesson(lesson = {}) {
  const slideCount = Array.isArray(lesson.slides) ? lesson.slides.length : Number(lesson.slide_count) || 0
  return Math.max(15, Math.min(80, slideCount * 2))
}

function blockTaskKey(block) {
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

function blockIsCompleted(block, keys) {
  if (!block) return false
  if (block.status === 'done') return true
  const key = blockTaskKey(block)
  if (blockPassPart(block)) return Boolean(key && keys.has(key))
  return Boolean(
    (key && keys.has(key)) ||
    (block.topic_id && keys.has(`topic:${block.topic_id}`)) ||
    (block.lecture_id && keys.has(`lecture:${block.lecture_id}`)) ||
    (block.lecture_id && keys.has(`task:${block.lecture_id}`))
  )
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

function rememberCompletedTaskKeys(date, keys) {
  const saved = readCompletedTaskKeys(date)
  for (const key of keys) {
    if (key) saved.add(key)
  }
  localStorage.setItem(completedStorageKey(date), JSON.stringify([...saved]))
}

function rememberLessonCompletion(block, lesson, planDate = '') {
  const date = validPlanDate(planDate) || block?.date || localISODate()
  const primaryKey = blockTaskKey(block)
  if (blockPassPart(block)) {
    rememberCompletedTaskKeys(date, [primaryKey])
    window.dispatchEvent(new CustomEvent('studypace:completion', { detail: { date, lectureId: lesson.id } }))
    return
  }
  rememberCompletedTaskKeys(date, [
    primaryKey,
    lesson.topic_id ? `topic:${lesson.topic_id}` : '',
    block?.assessment_id && lesson.topic_id ? `assessment:${block.assessment_id}:topic:${lesson.topic_id}` : '',
    lesson.id ? `task:${lesson.id}` : '',
    block?.lecture_id ? `lecture:${block.lecture_id}` : '',
  ])
  window.dispatchEvent(new CustomEvent('studypace:completion', { detail: { date, lectureId: lesson.id } }))
}

function planSnapshotKey(date) {
  return `${PLAN_SNAPSHOT_PREFIX}.${date}`
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

function readPlanSnapshot(date) {
  try {
    const snapshot = JSON.parse(localStorage.getItem(planSnapshotKey(date)) || 'null')
    return snapshot && Array.isArray(snapshot.tasks) ? snapshot : null
  } catch {
    return null
  }
}

function rememberPlanSnapshotFromBlocks(date, activePlan, blocks) {
  if (!date || date !== localISODate() || !activePlan || !blocks?.length) return

  const planKey = activePlanKey(activePlan)
  const existing = readPlanSnapshot(date)
  if (existing?.planKey === planKey && existing.tasks?.length) return

  const tasks = blocks
    .filter(block => block.lecture_id && block.status !== 'assessment' && block.status !== 'adjust')
    .map(block => ({
      key: blockTaskKey(block),
      topicId: block.topic_id,
      lectureId: block.lecture_id,
      title: block.title,
      minutes: block.planned_minutes || 0,
      course: block.course_name || 'Course',
      kind: block.assessment_type ? titleCase(block.assessment_type) : 'Study',
      href: lessonHref(block.lecture_id, block.date),
    }))

  if (!tasks.length) return

  localStorage.setItem(planSnapshotKey(date), JSON.stringify({
    date,
    savedAt: new Date().toISOString(),
    planKey,
    tasks,
  }))
}

function localISODate() {
  const now = new Date()
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000)
  return local.toISOString().slice(0, 10)
}

function titleCase(value = '') {
  return value ? value[0].toUpperCase() + value.slice(1) : 'Study'
}

function formatPlanDate(value) {
  if (!value) return 'Planned'
  return new Intl.DateTimeFormat(undefined, { weekday: 'short', month: 'short', day: 'numeric' }).format(new Date(`${value}T00:00:00`))
}

function LessonSection({ title, children }) {
  return (
    <section className="border-t border-[var(--border)] py-7">
      <h2 className="section-title text-2xl">{title}</h2>
      <div className="reading-content mt-4 space-y-4">
        {children}
      </div>
    </section>
  )
}

function MarkdownSummary({ content }) {
  const blocks = useMemo(() => parseMarkdownSummary(content), [content])

  return (
    <div className="space-y-5">
      {blocks.map((block, index) => {
        if (block.type === 'heading') {
          return (
            <h3 key={index} className="pt-2 text-xl font-semibold leading-7 tracking-normal text-[var(--text)]">
              {block.text}
            </h3>
          )
        }
        if (block.type === 'list') {
          return (
            <ul key={index} className="space-y-2">
              {block.items.map((item, itemIndex) => <li key={itemIndex}>{item}</li>)}
            </ul>
          )
        }
        return <p key={index}>{block.text}</p>
      })}
    </div>
  )
}

function parseMarkdownSummary(content = '') {
  const blocks = []
  let paragraph = []
  let list = []

  const flushParagraph = () => {
    const text = paragraph.join(' ').trim()
    if (text) blocks.push({ type: 'paragraph', text: cleanInlineMarkdown(text) })
    paragraph = []
  }

  const flushList = () => {
    if (list.length) blocks.push({ type: 'list', items: list.map(cleanInlineMarkdown) })
    list = []
  }

  for (const rawLine of content.replace(/\r\n/g, '\n').split('\n')) {
    const line = rawLine.trim()
    if (!line) {
      flushParagraph()
      flushList()
      continue
    }
    if (/^-{3,}$/.test(line)) {
      flushParagraph()
      flushList()
      continue
    }
    const heading = line.match(/^#{1,4}\s+(.+)$/)
    if (heading) {
      flushParagraph()
      flushList()
      const text = cleanInlineMarkdown(heading[1])
      if (!(blocks.length === 0 && /study summary/i.test(text))) {
        blocks.push({ type: 'heading', text })
      }
      continue
    }
    if (/^(Summary:|Part\s+\d+\s+-\s+)/i.test(line)) {
      flushParagraph()
      flushList()
      blocks.push({ type: 'heading', text: cleanInlineMarkdown(line) })
      continue
    }
    const bullet = line.match(/^[-*]\s+(.+)$/)
    if (bullet) {
      flushParagraph()
      list.push(bullet[1])
      continue
    }
    paragraph.push(line)
  }

  flushParagraph()
  flushList()
  const compacted = compactSummaryBlocks(blocks)
  return compacted.length ? compacted : [{ type: 'paragraph', text: 'This lesson was created from the uploaded slides.' }]
}

function cleanInlineMarkdown(text) {
  return text
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()
}

function compactSummaryBlocks(blocks) {
  const compacted = []
  for (const block of blocks) {
    const previous = compacted[compacted.length - 1]
    if (block.type === 'list' && previous?.type === 'list') {
      previous.items.push(...block.items)
    } else {
      compacted.push(block)
    }
  }
  return compacted
}

function buildShortSummary(lesson, reader) {
  const paragraphBlocks = parseMarkdownSummary(lesson.summary || reader.overview)
    .filter(block => block.type === 'paragraph')
    .map(block => block.text)

  const sentences = paragraphBlocks
    .flatMap(splitSentences)
    .map(cleanInlineMarkdown)
    .filter(sentence => sentence && !isNoisyConcept(sentence) && sentence.length > 35)

  const studyGoal = 'The main goal is to understand the definitions, follow the reasoning or process shown in the slides, and practise applying the ideas without relying on memorized slide wording.'

  if (sentences.length >= 2) {
    return clipText([sentences[0], sentences[1], studyGoal].join(' '), 620)
  }

  const concepts = (lesson.key_concepts || [])
    .map(cleanInlineMarkdown)
    .filter(item => item && !isNoisyConcept(item) && item.split(' ').length <= 7)
    .slice(0, 5)

  const opening = sentences[0] || (concepts.length ? `${lesson.title} covers ${humanList(concepts)}.` : reader.overview)

  return clipText([opening, studyGoal].join(' '), 620)
}

function splitSentences(text = '') {
  return String(text)
    .replace(/\s+/g, ' ')
    .split(/(?<=[.!?])\s+/)
    .map(sentence => sentence.trim())
}

function humanList(items) {
  if (items.length <= 1) return items[0] || ''
  if (items.length === 2) return `${items[0]} and ${items[1]}`
  return `${items.slice(0, -1).join(', ')}, and ${items[items.length - 1]}`
}

function clipText(text, limit) {
  const cleaned = cleanInlineMarkdown(text)
  if (cleaned.length <= limit) return cleaned
  const clipped = cleaned.slice(0, limit - 1).trim()
  const sentenceEnd = Math.max(clipped.lastIndexOf('.'), clipped.lastIndexOf('!'), clipped.lastIndexOf('?'))
  if (sentenceEnd > limit * 0.55) return clipped.slice(0, sentenceEnd + 1)
  return `${clipped.replace(/[,\s]+$/, '')}.`
}

function LectureSlides({ slides = [] }) {
  if (!slides.length) return null
  return (
    <section id="lecture-slides" className="border-t border-[var(--border)] py-7">
      <h2 className="section-title text-2xl">Lecture slides</h2>
      <div className="mt-5 space-y-6">
        {slides.map(slide => (
          <figure key={slide.id} className="space-y-2">
            <figcaption className="text-sm font-medium text-[var(--text-muted)]">
              {slide.slide_number}. {slide.title || `Slide ${slide.slide_number}`}
            </figcaption>
            {slide.image_url ? (
              <SlideImage
                src={slide.image_url}
                alt={`Slide ${slide.slide_number}: ${slide.title}`}
                fallbackText={slide.text}
              />
            ) : (
              <p className="surface-soft whitespace-pre-wrap p-4 text-sm leading-6 text-[var(--text-muted)]">
                {slide.text}
              </p>
            )}
          </figure>
        ))}
      </div>
    </section>
  )
}

function SlideImage({ src, alt, fallbackText }) {
  const [objectUrl, setObjectUrl] = useState('')
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    let revokeUrl = ''
    setObjectUrl('')
    setFailed(false)

    const token = getToken()
    fetch(src, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then(response => {
        if (!response.ok) throw new Error(`Slide image failed: ${response.status}`)
        return response.blob()
      })
      .then(blob => {
        if (cancelled) return
        revokeUrl = URL.createObjectURL(blob)
        setObjectUrl(revokeUrl)
      })
      .catch(() => {
        if (!cancelled) setFailed(true)
      })

    return () => {
      cancelled = true
      if (revokeUrl) URL.revokeObjectURL(revokeUrl)
    }
  }, [src])

  if (failed) {
    return (
      <p className="surface-soft whitespace-pre-wrap p-4 text-sm leading-6 text-[var(--text-muted)]">
        {fallbackText || 'Slide image could not be loaded.'}
      </p>
    )
  }

  if (!objectUrl) {
    return (
      <div className="grid min-h-[220px] place-items-center rounded-lg border border-[var(--border)] bg-[var(--surface-raised)] text-sm font-medium text-[var(--text-faint)]">
        Loading slide...
      </div>
    )
  }

  return (
    <img
      src={objectUrl}
      alt={alt}
      loading="lazy"
      className="w-full rounded-lg border border-[var(--border)] bg-white"
    />
  )
}

// ─── Lesson builder ──────────────────────────────────────────────────────────

function buildTutorLesson(lesson) {
  const slides = normalizeSlides(lesson.slides || [])
  const concepts = buildConcepts(lesson, slides)
  const sections = []

  if (concepts.length) {
    sections.push({
      title: 'Key concepts',
      paragraphs: ['These are the ideas the lecture returns to most often. Make sure you can define each one and give a concrete example before the exam.'],
      bullets: concepts.slice(0, 10),
    })
  }

  sections.push(...buildContentSections(slides))

  return {
    overview: buildOverview(lesson, slides, concepts),
    sections,
    thinkPrompts: buildThinkPrompts(lesson, slides),
    reviewPoints: buildReviewPoints(lesson, concepts),
  }
}

// ─── Overview ────────────────────────────────────────────────────────────────

function buildOverview(lesson, slides, concepts) {
  const meaningfulTitles = slides
    .filter(s => !genericTitle(s.title) && s.title.split(' ').length >= 2 && !isNoisyConcept(s.title))
    .map(s => cleanHeading(s.title))
  const topTopics = dedupe(meaningfulTitles).slice(0, 4)

  const opening = `This lesson covers ${lesson.title} across ${slides.length} slides.`

  const flowSentence = topTopics.length >= 2
    ? `The material moves through ${joinReadable(topTopics.map(t => t.toLowerCase()))}.`
    : ''

  const conceptSentence = concepts.length
    ? `Central ideas: ${concepts.slice(0, 4).join(', ')}.`
    : ''

  return [opening, flowSentence, conceptSentence,
    'Read for the big picture first, then revisit the slides until each idea feels clear without looking back.',
  ].filter(Boolean).join(' ')
}

// ─── Key concepts ────────────────────────────────────────────────────────────

function buildConcepts(lesson, slides) {
  const fromLesson = cleanList(lesson.key_concepts).filter(c => !isNoisyConcept(c))
  const slideTopics = slides
    .filter(s => !genericTitle(s.title) && goodConcept(s.title))
    .map(s => cleanHeading(s.title))
  return dedupe([...fromLesson, ...slideTopics]).filter(goodConcept).slice(0, 12)
}

// ─── Content sections ────────────────────────────────────────────────────────

function buildContentSections(slides) {
  if (!slides.length) return []

  const groups = groupSlides(slides)

  return groups.map(group => {
    const titleSlide = group.find(s => !genericTitle(s.title) && s.title.split(' ').length >= 2) || group[0]
    const title = cleanHeading(titleSlide.title)
    const intro = sectionIntroFor(title, group)
    const bullets = extractBullets(group)
    return { title, paragraphs: intro ? [intro] : [], bullets }
  }).filter(g => g.paragraphs.length || g.bullets.length)
}

function groupSlides(slides) {
  if (!slides.length) return []
  const targetGroups = Math.min(6, Math.max(2, Math.ceil(slides.length / 8)))
  const chunkSize = Math.ceil(slides.length / targetGroups)
  const groups = []

  for (let i = 0; i < slides.length; i += chunkSize) {
    groups.push(slides.slice(i, i + chunkSize))
  }
  return groups
}

function sectionIntroFor(title, slides) {
  const lower = title.toLowerCase()
  const bodyText = slides.map(s => `${s.title} ${s.lines.join(' ')}`).join(' ').toLowerCase()

  if (hasAny(lower, ['motivation', 'why', 'overview', 'introduction', 'intro', 'background']))
    return 'Understand the problem this topic solves before focusing on the mechanics.'
  if (hasAny(lower, ['definition', 'terminology', 'terms', 'vocabulary', 'notation']))
    return 'These definitions are the foundation — later operations depend on them.'
  if (hasAny(lower, ['algorithm', 'operation', 'insert', 'delete', 'search', 'process', 'step', 'procedure']))
    return 'Focus on the decision at each step and what condition must hold before and after.'
  if (hasAny(lower, ['complexity', 'performance', 'big-o', 'runtime', 'time', 'space', 'cost', 'analysis']))
    return 'Understand the reasoning behind each bound, not just the final result.'
  if (hasAny(lower, ['example', 'trace', 'case', 'proof', 'exercise']))
    return 'Work through these yourself before checking the slide answer.'
  if (hasAny(lower, ['implementation', 'code', 'class', 'method', 'structure']))
    return 'Connect the abstract idea to how it is expressed in code.'
  if (hasAny(lower, ['comparison', 'vs', 'tradeoff', 'advantage', 'disadvantage']))
    return 'Focus on which constraint or workload makes one approach better than the other.'
  if (hasAny(lower, ['properties', 'property', 'invariant', 'rule', 'theorem', 'lemma']))
    return 'These rules must hold at all times — be ready to verify them in an example.'

  // Fall back to detecting from content
  if (hasAny(bodyText, ['o(n', 'o(log', 'θ(', 'complexity', 'runtime']))
    return 'Pay attention to the conditions that produce best, average, and worst-case costs.'
  if (hasAny(bodyText, ['algorithm', 'insert', 'delete', 'operation']))
    return 'Trace through each operation step by step and verify the invariant holds.'
  return ''
}

function extractBullets(slides) {
  const bullets = []
  const seen = new Set()

  for (const slide of slides) {
    const lines = slide.lines
      .filter(l => l.length >= 18 && l.length <= 160)
      .filter(l => !isNoisyLine(l))
      .slice(0, 2)

    for (const line of lines) {
      const key = line.toLowerCase().trim()
      if (!seen.has(key)) {
        seen.add(key)
        bullets.push(line)
      }
    }
  }
  return bullets.slice(0, 8)
}

// ─── Think prompts ───────────────────────────────────────────────────────────

function buildThinkPrompts(lesson, slides) {
  const prompts = []
  const allText = slides.map(s => `${s.title} ${s.lines.join(' ')}`).join(' ').toLowerCase()

  // Pull actual questions from slides
  const questionSlides = slides
    .filter(s => s.title.trim().endsWith('?') || s.tags.includes('question'))
    .slice(0, 3)
  for (const s of questionSlides) {
    const q = cleanHeading(s.title)
    if (q && q.length > 10) prompts.push(q)
  }

  // Add content-driven questions
  if (hasAny(allText, ['algorithm', 'operation', 'insert', 'delete', 'search', 'process']))
    prompts.push('Can you trace the main operation from start to finish, explaining the decision at each step?')
  if (hasAny(allText, ['complexity', 'o(', 'runtime', 'worst case', 'best case', 'average']))
    prompts.push('What are the best, average, and worst-case costs — and what input produces each one?')
  if (hasAny(allText, ['compare', 'versus', 'vs', 'advantage', 'tradeoff', 'better', 'faster']))
    prompts.push('What constraint makes one approach clearly better than the alternatives described in the lecture?')
  if (hasAny(allText, ['property', 'invariant', 'rule', 'must', 'theorem', 'proof']))
    prompts.push('What invariant must always hold, and how would you verify it on a small example?')
  if (hasAny(allText, ['definition', 'define', ' is a ', ' means ']))
    prompts.push('Can you give a precise definition of the core concept and construct a concrete example from scratch?')

  if (prompts.length < 3)
    prompts.push('What is the one idea from this lecture you would most likely get wrong under exam pressure, and why?')

  return dedupe(prompts).slice(0, 5)
}

// ─── Review checklist ────────────────────────────────────────────────────────

function buildReviewPoints(lesson, concepts) {
  const points = []

  const goodObjectives = cleanList(lesson.learning_objectives)
    .filter(o => !isNoisyConcept(o) && o.length > 20 && o.split(' ').length >= 5)
  points.push(...goodObjectives.slice(0, 3))

  for (const concept of concepts.slice(0, 5))
    points.push(`Be able to explain ${concept.toLowerCase()} without looking back at the slides.`)

  return dedupe(points).slice(0, 8)
}

// ─── Slide normalisation ─────────────────────────────────────────────────────

function normalizeSlides(slides) {
  return slides.map(slide => {
    const title = cleanLine(slide.title) || `Slide ${slide.slide_number}`
    const lines = String(slide.text || '')
      .split(/\n+/)
      .map(cleanLine)
      .filter(line => keepLine(line, title))

    return {
      slideNumber: slide.slide_number,
      title,
      tags: slide.content_tags || [],
      lines: dedupe(lines),
    }
  })
}

// ─── Filter helpers ───────────────────────────────────────────────────────────

function isNoisyConcept(value) {
  const lower = (value || '').toLowerCase().trim()
  if (!lower || lower.length < 3) return true
  if (lower.startsWith('credit:')) return true
  if (lower.startsWith('last lecture')) return true
  if (lower.startsWith('slide ‹')) return true
  if (lower.startsWith('©') || lower.includes('leiserson')) return true
  if (lower.startsWith('❑') || lower.startsWith('◼') || lower.startsWith('•')) return true
  if (/^\d+[.)]\s/.test(lower)) return true
  return false
}

function isNoisyLine(value) {
  const lower = (value || '').toLowerCase()
  if (lower.startsWith('credit:')) return true
  if (lower.startsWith('last lecture')) return true
  if (lower.startsWith('©') || lower.includes('leiserson')) return true
  if (lower.startsWith('slide ‹')) return true
  if (/^\d{1,3}$/.test(value.trim())) return true
  return false
}

function goodConcept(value = '') {
  const text = cleanLine(value)
  const lower = text.toLowerCase()
  if (!text || genericTitle(text)) return false
  if (text.length > 72) return false
  if (text.endsWith(':') || text.endsWith('.')) return false
  if (/^\d+[.)]\s/.test(text)) return false
  if (/^\(?o\(/i.test(text)) return false
  if (isNoisyConcept(text)) return false
  if (lower.startsWith('a special kind')) return false
  if (lower.startsWith('//') || lower.startsWith('public ')) return false
  if (lower.includes('slide ‹#›')) return false
  return true
}

// ─── Text helpers ─────────────────────────────────────────────────────────────

function cleanLine(value = '') {
  return String(value)
    .replace(/\0/g, ' ')
    .replace(/[�]/g, '')
    .replace(/[•●❑❖➢◼■]/g, ' ')
    .replace(/^\s*[qn]\s+/i, '')
    .replace(/^\s*§\s*/, '')
    .replace(/\s+/g, ' ')
    .replace(/\s+([,.;:!?])/g, '$1')
    .trim()
}

function keepLine(line, title) {
  if (!line) return false
  if (/^\d{1,3}$/.test(line)) return false
  if (sameText(line, title)) return false
  const lower = line.toLowerCase()
  if (lower === 'cosc 310' || lower === 'cosc310') return false
  if (lower.startsWith('credit:')) return false
  if (lower.startsWith('last lecture')) return false
  if (lower.includes('slide ‹#›')) return false
  if (lower.startsWith('©') || lower.includes('leiserson')) return false
  return true
}

function cleanHeading(value = '') {
  return cleanLine(value).replace(/\.+$/, '')
}

function cleanList(value) {
  return (Array.isArray(value) ? value : [])
    .map(cleanLine)
    .filter(Boolean)
}

function genericTitle(title = '') {
  return /^slide\s+\d+$/i.test(title) || /^\d+$/.test(title)
}

function dedupe(items) {
  const seen = new Set()
  const result = []
  for (const item of items) {
    const key = item.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    result.push(item)
  }
  return result
}

function sameText(a = '', b = '') {
  return cleanLine(a).toLowerCase() === cleanLine(b).toLowerCase()
}

function hasAny(value, needles) {
  return needles.some(needle => value.includes(needle))
}

function joinReadable(items) {
  if (items.length <= 1) return items[0] || ''
  if (items.length === 2) return `${items[0]} and ${items[1]}`
  return `${items.slice(0, -1).join(', ')}, and ${items[items.length - 1]}`
}
