import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api'

export default function Quiz() {
  const [params] = useSearchParams()
  const initialTopic = params.get('topic') || ''
  const lectureId = params.get('lecture') || ''
  const title = params.get('name') || 'Quiz'

  const [questions, setQuestions] = useState([])
  const [answers, setAnswers] = useState({})
  const [current, setCurrent] = useState(0)
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [courses, setCourses] = useState([])
  const [topics, setTopics] = useState([])
  const [selectedTopic, setSelectedTopic] = useState(initialTopic)
  const [lesson, setLesson] = useState(null)

  const isLessonQuiz = Boolean(lectureId)

  useEffect(() => {
    Promise.all([api.getCourses(), api.getTopics()]).then(([c, t]) => { setCourses(c); setTopics(t) })
  }, [])

  useEffect(() => {
    if (isLessonQuiz) loadLessonQuiz(lectureId)
  }, [lectureId])

  useEffect(() => {
    if (!isLessonQuiz && selectedTopic) loadTopicQuiz(selectedTopic)
  }, [selectedTopic, isLessonQuiz])

  async function loadLessonQuiz(id) {
    setLoading(true); setError(''); setResult(null); setAnswers({}); setCurrent(0)
    try {
      const loaded = await api.getLesson(id)
      const mcqs = loaded.questions.filter(isGeneratedMcq)
      setLesson(loaded)
      setQuestions(mcqs)
      if (!mcqs.length) {
        setError('This lesson does not have a generated multiple-choice check yet.')
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function loadTopicQuiz(topicId) {
    setLoading(true); setError(''); setResult(null); setAnswers({}); setCurrent(0)
    try {
      const qs = await api.getQuestions(topicId)
      setQuestions(qs.map(q => ({ ...q, prompt: q.question })))
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function submitQuiz() {
    const payload = {
      answers: Object.entries(answers).map(([qid, selected]) => ({ question_id: Number(qid), selected })),
    }
    const res = isLessonQuiz
      ? await api.submitLessonQuiz(lectureId, payload)
      : await api.submitQuiz({ topic_id: Number(selectedTopic), answers: payload.answers })
    setResult(res)
  }

  const q = questions[current]
  const opts = useMemo(() => q ? [
    { k: 'a', v: q.option_a },
    { k: 'b', v: q.option_b },
    { k: 'c', v: q.option_c },
    { k: 'd', v: q.option_d },
  ] : [], [q])

  return (
    <div className="max-w-3xl mx-auto space-y-5">
      <section className="card">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">MCQ quiz</p>
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold mt-1">{isLessonQuiz ? title : 'Topic quiz'}</h1>
            <p className="text-sm text-slate-500 mt-1">
              {isLessonQuiz ? 'Generated from uploaded slide content with slide citations.' : 'Scheduler topic quiz.'}
            </p>
          </div>
          {lesson && <Link to={`/lesson/${lesson.id}`} className="btn-ghost">Lesson</Link>}
        </div>

        {!isLessonQuiz && (
          <select className="input mt-4" value={selectedTopic} onChange={e => setSelectedTopic(e.target.value)}>
            <option value="">Choose a topic</option>
            {courses.map(course => (
              <optgroup key={course.id} label={course.name}>
                {topics.filter(topic => topic.course_id === course.id).map(topic => (
                  <option key={topic.id} value={topic.id}>{topic.name}</option>
                ))}
              </optgroup>
            ))}
          </select>
        )}
      </section>

      {loading && <div className="text-center py-12 text-slate-400">Loading questions...</div>}
      {error && <div className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">{error}</div>}

      {!result && q && !loading && (
        <section className="card">
          <div className="flex items-center justify-between gap-3">
            <span className="text-sm text-slate-500">Question {current + 1} of {questions.length}</span>
            <div className="flex gap-1">
              {questions.map((item, idx) => (
                <span key={item.id} className={`h-2 w-7 rounded-full ${idx <= current ? 'bg-slate-900' : 'bg-slate-200'}`} />
              ))}
            </div>
          </div>

          <div className="mt-4 flex flex-wrap gap-2">
            {q.slide_number && <span className="badge bg-slate-100 text-slate-600">Slide {q.slide_number}</span>}
            {q.topic_tag && <span className="badge bg-blue-50 text-blue-700">{q.topic_tag}</span>}
            {q.difficulty && <span className="badge bg-amber-50 text-amber-700">{q.difficulty}</span>}
          </div>

          <h2 className="mt-4 text-lg font-bold leading-relaxed">{q.prompt}</h2>
          <div className="mt-4 space-y-2">
            {opts.map(option => (
              <button
                key={option.k}
                className={`w-full rounded-lg border-2 p-3 text-left text-sm transition ${answers[q.id] === option.k ? 'border-slate-900 bg-slate-50' : 'border-slate-200 hover:border-slate-400'}`}
                onClick={() => setAnswers(prev => ({ ...prev, [q.id]: option.k }))}
              >
                <span className="mr-2 font-bold uppercase">{option.k}.</span>{option.v}
              </button>
            ))}
          </div>

          <div className="mt-5 flex gap-2">
            {current > 0 && <button className="btn-ghost" onClick={() => setCurrent(c => c - 1)}>Back</button>}
            {current < questions.length - 1 ? (
              <button className="btn-primary ml-auto" disabled={!answers[q.id]} onClick={() => setCurrent(c => c + 1)}>Next</button>
            ) : (
              <button className="btn-primary ml-auto" disabled={Object.keys(answers).length < questions.length} onClick={submitQuiz}>Submit quiz</button>
            )}
          </div>
        </section>
      )}

      {!result && !q && !loading && !error && (
        <section className="card text-center">
          <h2 className="text-lg font-bold">No MCQ check yet</h2>
          <p className="text-sm text-slate-500 mt-2">This lesson does not have a generated multiple-choice quiz yet.</p>
          {lesson && <Link className="btn-primary mt-4" to={`/lesson/${lesson.id}`}>Back to lesson</Link>}
        </section>
      )}

      {result && (
        <section className="space-y-4">
          <div className={`card text-center ${result.score >= 0.8 ? 'bg-emerald-50 border-emerald-100' : result.score >= 0.6 ? 'bg-amber-50 border-amber-100' : 'bg-rose-50 border-rose-100'}`}>
            <div className="text-5xl font-bold">{Math.round(result.score * 100)}%</div>
            <div className="text-sm text-slate-600 mt-1">{result.correct} / {result.total} correct</div>
            {isLessonQuiz && (
              <div className="mt-2 text-xs text-slate-600">
                {result.xp_earned} XP earned · Mastery {Math.round(result.mastery_score * 100)}% · {result.unlocked_next ? 'Next lesson unlocked' : 'Reach 80% to unlock confidently'}
              </div>
            )}
          </div>

          <div className="space-y-3">
            {result.per_question.map((item, idx) => (
              <article key={`${item.question_id || item.id}-${idx}`} className={`card border-l-4 ${item.is_correct ? 'border-emerald-500' : 'border-rose-500'}`}>
                <div className="flex flex-wrap gap-2">
                  {item.slide_number && <span className="badge bg-slate-100 text-slate-600">Slide {item.slide_number}</span>}
                  <span className={`badge ${item.is_correct ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'}`}>{item.is_correct ? 'Correct' : 'Review this'}</span>
                </div>
                <p className="font-semibold text-sm mt-3">{item.prompt || item.question}</p>
                <p className="text-xs text-slate-500 mt-2">
                  Your answer: <strong>{(item.selected || '').toUpperCase()}</strong>
                  {!item.is_correct && <> · Correct: <strong>{(item.correct_answer || '').toUpperCase()}</strong></>}
                </p>
                <p className="mt-2 rounded-lg bg-slate-50 p-3 text-sm text-slate-600">{item.feedback || item.explanation}</p>
              </article>
            ))}
          </div>

          <div className="flex gap-2">
            <button className="btn-primary flex-1" onClick={() => isLessonQuiz ? loadLessonQuiz(lectureId) : loadTopicQuiz(selectedTopic)}>Retry</button>
          </div>
        </section>
      )}
    </div>
  )
}

function isGeneratedMcq(question) {
  if (question.question_type) return question.question_type === 'generated_mcq'
  return ['easy', 'medium', 'hard'].includes(question.difficulty)
}
