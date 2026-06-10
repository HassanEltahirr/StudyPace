import { useEffect, useState } from 'react'
import { api } from '../api'

const API_ORIGIN = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '')
const EXPORT_URL = API_ORIGIN ? `${API_ORIGIN}/api/learning/instructor/export.csv` : '/api/learning/instructor/export.csv'

export default function Instructor() {
  const [data, setData] = useState(null)

  useEffect(() => {
    api.getInstructorDashboard().then(setData)
  }, [])

  if (!data) return <div className="text-center py-20 text-slate-400">Loading instructor dashboard...</div>

  return (
    <div className="space-y-5">
      <section className="card">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Instructor mode</p>
            <h1 className="text-2xl font-bold mt-1">Class progress</h1>
            <p className="text-sm text-slate-500 mt-2">MVP uses the local student as a demo cohort. Hosted mode can expand this to real class rosters.</p>
          </div>
          <a className="btn-ghost" href={EXPORT_URL}>Export CSV</a>
        </div>
      </section>

      <section className="grid md:grid-cols-4 gap-3">
        <Stat label="Courses" value={data.class_progress.courses} />
        <Stat label="Lectures" value={data.class_progress.lectures} />
        <Stat label="Completion" value={`${Math.round(data.class_progress.completion * 100)}%`} />
        <Stat label="At risk" value={data.students_falling_behind.length} />
      </section>

      <div className="grid lg:grid-cols-2 gap-4">
        <section className="card">
          <h2 className="text-lg font-bold">Students falling behind</h2>
          <div className="mt-3 space-y-2">
            {data.students_falling_behind.length === 0 && <p className="text-sm text-slate-500">No falling-behind signal yet.</p>}
            {data.students_falling_behind.map(student => (
              <div key={student.student} className="rounded-lg border border-rose-100 bg-rose-50 p-3">
                <div className="font-semibold text-sm text-rose-900">{student.student}</div>
                <div className="text-xs text-rose-700 mt-1">{student.reason}</div>
              </div>
            ))}
          </div>
        </section>

        <section className="card">
          <h2 className="text-lg font-bold">Mastery distribution</h2>
          <div className="mt-4 grid grid-cols-3 gap-2 text-center">
            <Stat label="High" value={data.mastery_distribution.high} />
            <Stat label="Medium" value={data.mastery_distribution.medium} />
            <Stat label="Low" value={data.mastery_distribution.low} />
          </div>
        </section>
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
        <section className="card">
          <h2 className="text-lg font-bold">Most missed questions</h2>
          <div className="mt-3 space-y-2">
            {data.most_missed_questions.length === 0 && <p className="text-sm text-slate-500">No missed questions yet.</p>}
            {data.most_missed_questions.map((question, idx) => (
              <div key={idx} className="rounded-lg border border-slate-200 p-3">
                <div className="flex items-center gap-2">
                  <span className="badge bg-slate-100 text-slate-600">Slide {question.slide_number}</span>
                  <span className="badge bg-rose-50 text-rose-700">{question.misses} misses</span>
                </div>
                <p className="text-sm font-semibold mt-2">{question.question}</p>
                <p className="text-xs text-slate-500 mt-1">{question.topic}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="card">
          <h2 className="text-lg font-bold">Weak topics by cohort</h2>
          <div className="mt-3 space-y-2">
            {data.weak_topics_by_cohort.length === 0 && <p className="text-sm text-slate-500">Weak-topic data appears after quiz attempts.</p>}
            {data.weak_topics_by_cohort.map(topic => (
              <div key={topic.topic_id} className="rounded-lg border border-slate-200 p-3 flex items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-semibold">{topic.topic}</div>
                  <div className="text-xs text-slate-500">{topic.attempts} attempts</div>
                </div>
                <span className="badge bg-rose-50 text-rose-700">{Math.round(topic.avg_score * 100)}%</span>
              </div>
            ))}
          </div>
        </section>
      </div>

      <section className="card">
        <h2 className="text-lg font-bold">Lecture completion</h2>
        <div className="mt-3 overflow-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs text-slate-500">
                <th className="py-2 pr-3 font-medium">Lecture</th>
                <th className="py-2 pr-3 font-medium">Mastery</th>
                <th className="py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {data.lecture_completion.map(lecture => (
                <tr key={lecture.lecture} className="border-b border-slate-100">
                  <td className="py-2 pr-3 font-semibold">{lecture.lecture}</td>
                  <td className="py-2 pr-3">{Math.round(lecture.mastery * 100)}%</td>
                  <td className="py-2"><span className="badge bg-slate-100 text-slate-600">{lecture.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}

function Stat({ label, value }) {
  return (
    <div className="rounded-lg bg-slate-50 p-3">
      <div className="text-2xl font-bold">{value}</div>
      <div className="text-xs text-slate-500">{label}</div>
    </div>
  )
}
