import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'

export default function Practice() {
  const { lectureId } = useParams()
  const [lesson, setLesson] = useState(null)
  const [covered, setCovered] = useState({})
  const [writtenAnswers, setWrittenAnswers] = useState({})
  const [writtenFeedback, setWrittenFeedback] = useState({})
  const [checkingWritten, setCheckingWritten] = useState({})
  const [mcqAnswers, setMcqAnswers] = useState({})
  const [mcqFeedback, setMcqFeedback] = useState({})
  const [checkingMcq, setCheckingMcq] = useState({})
  const [error, setError] = useState('')

  useEffect(() => {
    api.getLesson(lectureId).then(setLesson)
  }, [lectureId])

  useEffect(() => {
    if (!lesson) return
    const saved = localStorage.getItem(`studypace.practice.correct.v2.${lesson.id}`)
    const savedWritten = localStorage.getItem(`studypace.practice.written.${lesson.id}`)
    setCovered(saved ? safeParse(saved) : {})
    setWrittenAnswers(savedWritten ? safeParse(savedWritten) : {})
    setWrittenFeedback({})
    setCheckingWritten({})
    setMcqAnswers({})
    setMcqFeedback({})
    setCheckingMcq({})
    setError('')
  }, [lesson?.id])

  const openQuestions = useMemo(() => {
    if (!lesson) return []
    return lesson.questions.filter(question => question.question_type !== 'generated_mcq')
  }, [lesson])

  const mcqQuestions = useMemo(() => {
    if (!lesson) return []
    return lesson.questions.filter(question => question.question_type === 'generated_mcq')
  }, [lesson])

  const slidesByNumber = useMemo(() => {
    if (!lesson) return new Map()
    return new Map(lesson.slides.map(slide => [slide.slide_number, slide]))
  }, [lesson])

  function updateWrittenAnswer(questionId, value) {
    setWrittenAnswers(prev => {
      const next = { ...prev, [questionId]: value }
      localStorage.setItem(`studypace.practice.written.${lesson.id}`, JSON.stringify(next))
      return next
    })
  }

  function selectMcq(questionId, selected) {
    if (mcqFeedback[questionId]) return
    setMcqAnswers(prev => ({ ...prev, [questionId]: selected }))
  }

  async function checkMcq(question) {
    const selected = mcqAnswers[question.id]
    if (!selected) return
    setCheckingMcq(prev => ({ ...prev, [question.id]: true }))
    setError('')
    try {
      const feedback = await api.checkLessonQuestion(lesson.id, question.id, {
        question_id: question.id,
        selected,
      })
      setMcqFeedback(prev => ({ ...prev, [question.id]: feedback }))
    } catch (e) {
      setError(e.message)
    } finally {
      setCheckingMcq(prev => ({ ...prev, [question.id]: false }))
    }
  }

  async function checkWritten(question) {
    const response = (writtenAnswers[question.id] || '').trim()
    if (!response) return
    setCheckingWritten(prev => ({ ...prev, [question.id]: true }))
    setError('')
    try {
      const feedback = await api.checkLessonQuestion(lesson.id, question.id, {
        question_id: question.id,
        response,
      })
      setWrittenFeedback(prev => ({ ...prev, [question.id]: feedback }))
      if (feedback.is_correct) {
        setCovered(prev => {
          const next = { ...prev, [question.id]: true }
          localStorage.setItem(`studypace.practice.correct.v2.${lesson.id}`, JSON.stringify(next))
          return next
        })
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setCheckingWritten(prev => ({ ...prev, [question.id]: false }))
    }
  }

  function retryWritten(questionId) {
    setWrittenFeedback(prev => {
      const next = { ...prev }
      delete next[questionId]
      return next
    })
  }

  function retryMcq(questionId) {
    setMcqAnswers(prev => {
      const next = { ...prev }
      delete next[questionId]
      return next
    })
    setMcqFeedback(prev => {
      const next = { ...prev }
      delete next[questionId]
      return next
    })
  }

  if (!lesson) return <div className="text-center py-20 text-slate-400">Loading practice...</div>

  const coveredCount = openQuestions.filter(question => covered[question.id]).length
  const checkedMcqs = mcqQuestions.filter(question => mcqFeedback[question.id]).length
  const totalPracticeItems = openQuestions.length + mcqQuestions.length
  const completedPracticeItems = coveredCount + checkedMcqs
  const coveragePct = totalPracticeItems ? Math.round((completedPracticeItems / totalPracticeItems) * 100) : 0
  const allPracticeQuestions = [...openQuestions, ...mcqQuestions]

  return (
    <div className="max-w-4xl mx-auto space-y-5">
      <section className="card">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Practice</p>
        <h1 className="text-2xl font-bold mt-1">{lesson.title}</h1>
        <div className="mt-4 rounded-2xl bg-emerald-50 border-2 border-emerald-100 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-sm font-black text-emerald-800">{coveragePct === 100 ? 'Practice complete' : 'Practice in progress'}</div>
              <div className="text-xs text-emerald-700 mt-1">{coveredCount} of {openQuestions.length} written answers correct · {checkedMcqs} of {mcqQuestions.length} quick checks checked</div>
            </div>
            <div className="text-3xl font-black text-emerald-700">{coveragePct}%</div>
          </div>
          <div className="mt-3 h-3 rounded-full bg-white overflow-hidden">
            <div className="h-full bg-emerald-500" style={{ width: `${coveragePct}%` }} />
          </div>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <Link className="btn-ghost" to={`/lesson/${lesson.id}`}>Back to lesson</Link>
        </div>
      </section>

      {error && <div className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">{error}</div>}

      {allPracticeQuestions.length > 0 && <CoverageReport questions={allPracticeQuestions} />}

      {openQuestions.length > 0 && (
        <section className="space-y-3">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Written response</p>
              <h2 className="text-lg font-black mt-1">Problem-solving practice</h2>
            </div>
            <span className="badge bg-violet-50 text-violet-700">{openQuestions.length} written</span>
          </div>
          {openQuestions.map((question, index) => (
            <WrittenQuestionCard
              key={question.id}
              question={question}
              index={index}
              slide={slidesByNumber.get(question.slide_number)}
              covered={Boolean(covered[question.id])}
              answer={writtenAnswers[question.id] || ''}
              feedback={writtenFeedback[question.id]}
              checking={Boolean(checkingWritten[question.id])}
              onAnswer={value => updateWrittenAnswer(question.id, value)}
              onSubmit={() => checkWritten(question)}
              onRetry={() => retryWritten(question.id)}
            />
          ))}
        </section>
      )}

      {mcqQuestions.length > 0 && (
        <section className="card">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Practice</p>
              <h2 className="text-lg font-black mt-1">Exam-style questions</h2>
            </div>
            <span className="badge bg-sky-50 text-sky-700">{checkedMcqs} checked</span>
          </div>

          <div className="mt-4 space-y-4">
            {mcqQuestions.map((question, index) => {
              const selected = mcqAnswers[question.id]
              const feedback = mcqFeedback[question.id]
              const checking = Boolean(checkingMcq[question.id])
              const meta = questionMeta(question)
              return (
                <article key={question.id} className="rounded-2xl border-2 border-slate-100 p-4 dark:border-white/10">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap gap-2">
                      <span className="badge bg-slate-100 text-slate-600">Slide {question.slide_number}</span>
                      <span className="badge bg-blue-50 text-blue-700">Question {index + 1}</span>
                      <span className={`badge ${difficultyClass(meta.difficulty)}`}>{meta.difficulty}</span>
                      <span className="badge bg-violet-50 text-violet-700">{meta.bloom}</span>
                    </div>
                    {feedback && (
                      <span className={`badge ${feedback.is_correct ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'}`}>
                        {feedback.is_correct ? 'Correct' : 'Review'}
                      </span>
                    )}
                  </div>
                  <div className="mt-3 rounded-xl bg-slate-50 p-3 text-xs font-semibold leading-relaxed text-slate-600 dark:bg-white/5 dark:text-slate-300">
                    <div><span className="font-black text-slate-900 dark:text-white">Concept:</span> {meta.concept}</div>
                    <div className="mt-1"><span className="font-black text-slate-900 dark:text-white">Objective:</span> {meta.objective}</div>
                  </div>
                  <h3 className="mt-3 font-bold leading-relaxed">{question.prompt}</h3>
                  <div className="mt-3 space-y-2">
                    {mcqOptions(question).map(option => (
                      <button
                        key={option.k}
                        disabled={Boolean(feedback)}
                        className={`w-full rounded-xl border-2 p-3 text-left text-sm font-semibold transition ${optionClass(option.k, selected, feedback)}`}
                        onClick={() => selectMcq(question.id, option.k)}
                      >
                        <span className="mr-2 font-black uppercase">{option.k}.</span>{option.v}
                      </button>
                    ))}
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {!feedback ? (
                      <button
                        className="btn-primary"
                        disabled={checking || !selected}
                        onClick={() => checkMcq(question)}
                      >
                        {checking ? 'Checking...' : 'Check answer'}
                      </button>
                    ) : (
                      <button className="btn-ghost" onClick={() => retryMcq(question.id)}>
                        Try again
                      </button>
                    )}
                  </div>
                  {feedback && (
                    <AnswerReview question={question} feedback={feedback} />
                  )}
                </article>
              )
            })}
          </div>
        </section>
      )}
    </div>
  )
}

function WrittenQuestionCard({ question, index, slide, covered, answer, feedback, checking, onAnswer, onSubmit, onRetry }) {
  const meta = questionMeta(question)
  const attempted = Boolean(feedback)
  const canSubmit = answer.trim().length >= 10 && !checking && !feedback?.is_correct
  return (
    <article className="card">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-2">
          <span className="badge bg-slate-100 text-slate-600 dark:bg-white/10 dark:text-slate-200">Slide {question.slide_number}</span>
          <span className={`badge ${badgeClass(question.question_type)}`}>{questionLabel(question.question_type)}</span>
          <span className={`badge ${difficultyClass(meta.difficulty)}`}>{meta.difficulty}</span>
          <span className="badge bg-violet-50 text-violet-700">{meta.bloom}</span>
        </div>
        <span className={`badge ${feedback?.is_correct ? 'bg-emerald-50 text-emerald-700' : attempted ? 'bg-rose-50 text-rose-700' : covered ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>
          {feedback?.is_correct ? 'Correct' : attempted ? 'Try again' : covered ? 'Mastered' : 'Working'}
        </span>
      </div>
      <div className="mt-3 rounded-xl bg-slate-50 p-3 text-xs font-semibold leading-relaxed text-slate-600 dark:bg-white/5 dark:text-slate-300">
        <div><span className="font-black text-slate-900 dark:text-white">Concept:</span> {meta.concept}</div>
        <div className="mt-1"><span className="font-black text-slate-900 dark:text-white">Objective:</span> {meta.objective}</div>
      </div>
      <h3 className="mt-3 font-bold leading-relaxed">{questionTitle(question.question_type)} {index + 1}</h3>
      <div className="mt-3 rounded-2xl border-2 border-sky-100 bg-sky-50 p-4 dark:border-sky-400/20 dark:bg-sky-400/10">
        <div className="text-xs font-black uppercase tracking-wide text-sky-700 dark:text-sky-200">Your task</div>
        <p className="mt-2 text-sm font-semibold leading-relaxed text-slate-800 dark:text-slate-100">{question.prompt}</p>
      </div>
      <SlideEvidence slide={slide} slideNumber={question.slide_number} />
      <textarea
        className="input mt-3 min-h-28 resize-y"
        value={answer}
        onChange={event => onAnswer(event.target.value)}
        placeholder="Work your answer here..."
        disabled={feedback?.is_correct}
      />
      <div className="mt-3 flex flex-wrap gap-2">
        {!feedback?.is_correct && (
          <button className="btn-primary" disabled={!canSubmit} onClick={onSubmit}>
            {checking ? 'Checking...' : attempted ? 'Check again' : 'Submit answer'}
          </button>
        )}
        {attempted && !feedback?.is_correct && (
          <button className="btn-ghost" onClick={onRetry}>Edit answer</button>
        )}
      </div>
      {attempted && <WrittenAttemptFeedback question={question} feedback={feedback} />}
    </article>
  )
}

function WrittenAttemptFeedback({ question, feedback }) {
  const rubric = feedback.rubric || question.wrong_explanations?.rubric
  const traps = feedback.common_errors || question.wrong_explanations?.common_errors
  const modelAnswer = cleanModelAnswer(feedback.model_answer || question.explanation)
  const missing = feedback.missing_keywords || []
  const matched = feedback.matched_keywords || []
  return (
    <div className={`mt-3 overflow-hidden rounded-2xl border-2 text-sm leading-relaxed ${
      feedback.is_correct
        ? 'border-emerald-200 bg-emerald-50 text-emerald-950 dark:border-emerald-400/30 dark:bg-emerald-400/10 dark:text-emerald-100'
        : 'border-rose-200 bg-rose-50 text-rose-950 dark:border-rose-400/30 dark:bg-rose-400/10 dark:text-rose-100'
    }`}>
      <div className="px-4 py-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="font-black">{feedback.is_correct ? 'Correct' : 'Not quite yet'}</div>
          <span className={`badge ${feedback.is_correct ? 'bg-emerald-600 text-white' : 'bg-rose-600 text-white'}`}>
            {feedback.is_correct ? 'Mastered' : 'Revise'}
          </span>
        </div>
        <p className="mt-2 font-semibold">{feedback.feedback}</p>
        {(matched.length > 0 || missing.length > 0) && (
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {matched.length > 0 && (
              <div className="rounded-xl bg-white/70 p-3 dark:bg-black/20">
                <div className="text-xs font-black uppercase tracking-wide opacity-70">You included</div>
                <div className="mt-2 flex flex-wrap gap-1">
                  {matched.slice(0, 5).map(item => <span key={item} className="badge bg-emerald-600 text-white">{readableKeyword(item)}</span>)}
                </div>
              </div>
            )}
            {!feedback.is_correct && missing.length > 0 && (
              <div className="rounded-xl bg-white/70 p-3 dark:bg-black/20">
                <div className="text-xs font-black uppercase tracking-wide opacity-70">Add this</div>
                <div className="mt-2 flex flex-wrap gap-1">
                  {missing.slice(0, 5).map(item => <span key={item} className="badge bg-rose-600 text-white">{readableKeyword(item)}</span>)}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
      <div className="border-t-2 border-white/60 px-4 py-4 dark:border-white/10">
        <div className="font-black">Model answer</div>
        <div className="mt-2 space-y-2">
          {answerParagraphs(modelAnswer).map((paragraph, idx) => (
            <p key={`${paragraph}-${idx}`}>{paragraph}</p>
          ))}
        </div>
        {rubric && (
          <div className="mt-3">
            <div className="font-black">What a strong answer needs</div>
            <ul className="mt-2 list-disc space-y-1 pl-5">
              {splitLines(rubric).map(item => <li key={item}>{item}</li>)}
            </ul>
          </div>
        )}
        {traps && (
          <div className="mt-3 rounded-xl bg-amber-50 p-3 font-semibold text-amber-800 dark:bg-amber-400/10 dark:text-amber-200">
            Common mistake: {traps}
          </div>
        )}
      </div>
    </div>
  )
}

function cleanModelAnswer(value = '') {
  const cleaned = value
    .replace(/^#{1,4}\s*model answer\s*/i, '')
    .replace(/\bcoverage check for slide\s+\d+\s*:\s*/i, '')
    .replace(/[◼■▪●]/g, '')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
  return cleaned || 'A strong answer should use the source slide, name the key idea, and explain why it matters.'
}

function answerParagraphs(value = '') {
  const cleaned = cleanModelAnswer(value)
  if (!cleaned) return ['Use the cited slide first. If the slide is missing details, say what is missing instead of guessing.']
  return cleaned
    .split(/\n{2,}/)
    .map(paragraph => paragraph.replace(/^[-*]\s+/, '').trim())
    .filter(Boolean)
}

function CoverageReport({ questions }) {
  const concepts = [...new Set(questions.map(question => questionMeta(question).concept).filter(Boolean))]
  const difficulty = tally(questions.map(question => questionMeta(question).difficulty))
  const bloom = tally(questions.map(question => questionMeta(question).bloom))
  return (
    <div className="mt-4 rounded-2xl border-2 border-slate-100 bg-slate-50 p-4 text-sm dark:border-white/10 dark:bg-white/5">
      <h3 className="font-black text-slate-900 dark:text-slate-100">Coverage report</h3>
      <div className="mt-3 grid gap-3 lg:grid-cols-2">
        <div>
          <div className="text-xs font-black uppercase tracking-wide text-slate-500">Concepts tested</div>
          <div className="mt-2 flex flex-wrap gap-1">
            {concepts.map(concept => <span key={concept} className="badge bg-white text-slate-700 dark:bg-black/30 dark:text-slate-200">{concept}</span>)}
          </div>
        </div>
        <div>
          <div className="text-xs font-black uppercase tracking-wide text-slate-500">Concepts not tested</div>
          <p className="mt-2 font-semibold text-slate-600 dark:text-slate-300">Low-priority slide details, links, and optional extension material are left out unless they affect exam reasoning.</p>
        </div>
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        <Distribution title="Difficulty distribution" data={difficulty} />
        <Distribution title="Bloom distribution" data={bloom} />
      </div>
    </div>
  )
}

function Distribution({ title, data }) {
  return (
    <div className="rounded-xl bg-white p-3 dark:bg-black/30">
      <div className="text-xs font-black uppercase tracking-wide text-slate-500">{title}</div>
      <div className="mt-2 flex flex-wrap gap-1">
        {Object.entries(data).map(([key, value]) => (
          <span key={key} className="badge bg-sky-50 text-sky-700">{key}: {value}</span>
        ))}
      </div>
    </div>
  )
}

function AnswerReview({ question, feedback }) {
  const options = mcqOptions(question)
  return (
    <div className="mt-3 rounded-xl bg-slate-50 p-3 text-sm leading-relaxed text-slate-600 dark:bg-white/5 dark:text-slate-200">
      <div className="font-black text-slate-900 dark:text-white">Correct answer: {feedback.correct_answer?.toUpperCase()}</div>
      <p className="mt-2">{feedback.explanation}</p>
      <div className="mt-3 space-y-1">
        {options.map(option => (
          <p key={option.k}>
            <span className="font-black uppercase">Why {option.k} is {option.k === feedback.correct_answer ? 'correct' : 'wrong'}:</span>{' '}
            {option.k === feedback.correct_answer ? feedback.explanation : question.wrong_explanations?.[option.k] || 'This option does not best match the reasoning required by the question.'}
          </p>
        ))}
      </div>
    </div>
  )
}

function SlideEvidence({ slide, slideNumber }) {
  if (!slide) {
    return (
      <div className="mt-3 rounded-2xl border-2 border-amber-100 bg-amber-50 p-4 text-sm font-semibold text-amber-800">
        Slide {slideNumber} was cited, but the extracted slide text is not available.
      </div>
    )
  }

  const heading = slideHeading(slide)
  const tags = evidenceTags(slide.content_tags)

  return (
    <details className="mt-3 overflow-hidden rounded-2xl border-2 border-emerald-100 bg-white">
      <summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-2 bg-emerald-50 px-4 py-3">
        <div>
          <div className="text-xs font-black uppercase tracking-wide text-emerald-600">Source slide</div>
          <h3 className="font-black text-emerald-950">Slide {slide.slide_number}: {heading}</h3>
        </div>
        <div className="flex flex-wrap items-center gap-1">
          {tags.map(tag => (
            <span key={tag} className="badge bg-white text-emerald-700">{tag}</span>
          ))}
          <span className="badge bg-emerald-600 text-white">Open</span>
        </div>
      </summary>
      {slide.image_url ? (
        <div className="bg-black p-2">
          <img
            src={slide.image_url}
            alt={`Slide ${slide.slide_number}: ${heading}`}
            loading="lazy"
            className="mx-auto w-full rounded-xl bg-white object-contain"
          />
        </div>
      ) : (
        <p className="max-h-80 overflow-y-auto whitespace-pre-wrap p-4 text-sm leading-relaxed text-slate-700">{slide.text}</p>
      )}
    </details>
  )
}

function slideHeading(slide) {
  const title = cleanSlideLine(slide.title)
  if (title && !isGenericSlideLine(title)) return title

  const lines = slide.text
    .split('\n')
    .map(cleanSlideLine)
    .filter(line => line && !isGenericSlideLine(line))

  const heading = lines.find(line => line.split(' ').length <= 8 && !line.endsWith(':'))
  return heading || lines[0] || `Slide ${slide.slide_number}`
}

function evidenceTags(tags = []) {
  return [...new Set(tags.map(tag => (tag === 'numerical problem' ? 'problem' : tag)))]
}

function cleanSlideLine(value = '') {
  return value
    .replace(/[\u0000-\u001f\u007f]/g, ' ')
    .replace(/\s+/g, ' ')
    .replace(/^[\s•◦◼■▪▫❑□●○▶►▸-]+/u, '')
    .trim()
}

function isGenericSlideLine(value = '') {
  const lower = value.toLowerCase()
  return (
    !value ||
    value.includes('\u00a9') ||
    /\bslide\s*[‹<#]\s*#\s*[›>#]/i.test(value) ||
    lower.includes('charles e. leiserson') ||
    lower === 'preliminary' ||
    lower === 'automata, computability,' ||
    lower === 'and complexity' ||
    lower === 'automata, computability, and complexity' ||
    /^chapter\s+\d+$/i.test(value)
  )
}

function questionTitle(type = '') {
  if (type === 'extracted_numerical') return 'Slide problem'
  if (type === 'extracted_problem') return 'Slide problem'
  if (type === 'extracted_question') return 'Slide question'
  if (type === 'claude_written') return 'Claude practice'
  if (type === 'coverage_problem') return 'Written problem'
  if (type === 'coverage_question') return 'Written question'
  return 'MCQ practice'
}

function questionLabel(type = '') {
  if (type === 'extracted_numerical') return 'Extracted numerical'
  if (type === 'extracted_problem') return 'Extracted problem'
  if (type === 'extracted_question') return 'Extracted from slides'
  if (type === 'claude_written') return 'Claude written'
  if (type === 'coverage_problem') return 'Written problem'
  if (type === 'coverage_question') return 'Written response'
  return 'Generated MCQ'
}

function badgeClass(type = '') {
  if (type.includes('claude')) return 'bg-indigo-50 text-indigo-700'
  if (type.includes('numerical') || type.includes('problem')) return 'bg-violet-50 text-violet-700'
  if (type.includes('extracted')) return 'bg-sky-50 text-sky-700'
  if (type.includes('coverage')) return 'bg-emerald-50 text-emerald-700'
  return 'bg-blue-50 text-blue-700'
}

function mcqOptions(question) {
  return [
    { k: 'a', v: question.option_a },
    { k: 'b', v: question.option_b },
    { k: 'c', v: question.option_c },
    { k: 'd', v: question.option_d },
  ]
}

function questionMeta(question) {
  const parts = String(question.topic_tag || '')
    .split('|')
    .map(part => part.trim())
  const meta = {}
  for (const part of parts) {
    const [key, ...rest] = part.split(':')
    if (!key || rest.length === 0) continue
    meta[key.trim().toLowerCase()] = rest.join(':').trim()
  }
  return {
    difficulty: titleCase(meta.difficulty || question.difficulty || 'Medium'),
    bloom: titleCase(meta.bloom || 'Application'),
    concept: meta.concept || question.topic_tag || 'Course concept',
    objective: meta.objective || 'Apply the lecture concept to an exam-style scenario.',
  }
}

function tally(values) {
  return values.reduce((counts, value) => ({ ...counts, [value]: (counts[value] || 0) + 1 }), {})
}

function splitLines(value = '') {
  return value
    .split(/\n+|;+/)
    .map(item => item.replace(/^\d+[.)]\s*/, '').trim())
    .filter(Boolean)
}

function readableKeyword(value = '') {
  return value.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/_/g, ' ')
}

function difficultyClass(value = '') {
  const lower = value.toLowerCase()
  if (lower === 'hard') return 'bg-rose-50 text-rose-700'
  if (lower === 'medium') return 'bg-amber-50 text-amber-700'
  return 'bg-emerald-50 text-emerald-700'
}

function titleCase(value = '') {
  return value
    .split(/\s+/)
    .filter(Boolean)
    .map(word => word[0]?.toUpperCase() + word.slice(1).toLowerCase())
    .join(' ')
}

function optionClass(option, selected, feedback) {
  if (feedback) {
    if (option === feedback.correct_answer) return 'border-emerald-500 bg-emerald-50 text-emerald-900'
    if (option === selected && !feedback.is_correct) return 'border-rose-500 bg-rose-50 text-rose-900'
    return 'border-slate-200 text-slate-500'
  }
  return selected === option
    ? 'border-sky-500 bg-sky-50 text-sky-900'
    : 'border-slate-200 hover:border-sky-300 dark:border-white/10 dark:hover:border-sky-400/70'
}

function safeParse(value) {
  try {
    return JSON.parse(value)
  } catch {
    return {}
  }
}
