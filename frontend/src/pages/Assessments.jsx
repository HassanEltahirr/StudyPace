import { useEffect, useState } from 'react'
import { api } from '../api'
import { courseDisplayName } from '../courseLabels'

const TYPE_COLORS = { quiz: 'bg-blue-100 text-blue-700', midterm: 'bg-orange-100 text-orange-700', final: 'bg-red-100 text-red-700', exam: 'bg-red-100 text-red-700', assignment: 'bg-purple-100 text-purple-700' }

export default function Assessments() {
  const [assessments, setAssessments] = useState([])
  const [courses, setCourses] = useState([])
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ course_id: '', title: '', date: '', type: 'quiz', notes: '' })

  useEffect(() => {
    Promise.all([api.getAssessments(), api.getCourses()])
      .then(([a, c]) => { setAssessments(a); setCourses(c) })
  }, [])

  async function addAssessment() {
    const a = await api.createAssessment({ ...form, course_id: parseInt(form.course_id) })
    setAssessments(prev => [...prev, a].sort((a, b) => a.date.localeCompare(b.date)))
    setShowForm(false)
    setForm({ course_id: '', title: '', date: '', type: 'quiz', notes: '' })
  }

  async function remove(id) {
    await api.deleteAssessment(id)
    setAssessments(prev => prev.filter(a => a.id !== id))
  }

  const today = new Date().toISOString().split('T')[0]
  const upcoming = assessments.filter(a => a.date >= today)
  const past = assessments.filter(a => a.date < today)

  function daysUntil(d) {
    const diff = Math.round((new Date(d) - new Date(today)) / 86400000)
    if (diff === 0) return 'Today!'
    if (diff === 1) return 'Tomorrow'
    return `${diff} days`
  }

  const courseName = (id) => courseDisplayName(courses, id)

  return (
    <div className="space-y-5 max-w-2xl">
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-bold">🗓 Assessments</h1>
        <button className="btn-primary" onClick={() => setShowForm(true)}>+ Add</button>
      </div>

      <p className="text-sm text-gray-500">
        Adding an assessment here automatically boosts the priority of its course's topics in your daily plan during the 7 days leading up to it.
      </p>

      {/* Add form */}
      {showForm && (
        <div className="card space-y-3 border-2 border-blue-200">
          <h2 className="font-semibold">New Assessment</h2>
          <select className="w-full border border-gray-200 rounded-lg p-2 text-sm"
            value={form.course_id} onChange={e => setForm(f => ({ ...f, course_id: e.target.value }))}>
            <option value="">Select course…</option>
            {courses.map(c => <option key={c.id} value={c.id}>{courseDisplayName(courses, c)}</option>)}
          </select>
          <input className="w-full border border-gray-200 rounded-lg p-2 text-sm"
            placeholder="Title (e.g. Midterm Quiz 2)"
            value={form.title} onChange={e => setForm(f => ({ ...f, title: e.target.value }))} />
          <div className="flex gap-2">
            <input type="date" className="flex-1 border border-gray-200 rounded-lg p-2 text-sm"
              value={form.date} onChange={e => setForm(f => ({ ...f, date: e.target.value }))} />
            <select className="border border-gray-200 rounded-lg p-2 text-sm"
              value={form.type} onChange={e => setForm(f => ({ ...f, type: e.target.value }))}>
              <option value="quiz">Quiz</option>
              <option value="midterm">Midterm</option>
              <option value="final">Final</option>
              <option value="exam">Exam</option>
              <option value="assignment">Assignment</option>
            </select>
          </div>
          <textarea className="w-full border border-gray-200 rounded-lg p-2 text-sm" rows={2}
            placeholder="Notes (optional)"
            value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} />
          <div className="flex gap-2">
            <button className="btn-primary flex-1"
              disabled={!form.course_id || !form.title || !form.date}
              onClick={addAssessment}>Save</button>
            <button className="btn-ghost flex-1" onClick={() => setShowForm(false)}>Cancel</button>
          </div>
        </div>
      )}

      {/* Upcoming */}
      <div>
        <h2 className="text-base font-semibold mb-3 text-gray-700">Upcoming ({upcoming.length})</h2>
        {upcoming.length === 0 && <p className="text-sm text-gray-400">No upcoming assessments.</p>}
        <div className="space-y-2">
          {upcoming.map(a => {
            const days = parseInt(daysUntil(a.date))
            const urgent = !isNaN(days) && days <= 7
            return (
              <div key={a.id} className={`card flex items-center gap-3 ${urgent ? 'border-red-200 bg-red-50' : ''}`}>
                {urgent && <span className="text-lg">⚡</span>}
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-sm">{a.title}</span>
                    <span className={`badge ${TYPE_COLORS[a.type] || 'bg-gray-100 text-gray-700'}`}>{a.type}</span>
                    {urgent && <span className="badge bg-red-100 text-red-600">Scheduler boosted ⬆</span>}
                  </div>
                  <div className="text-xs text-gray-500 mt-0.5">
                    {courseName(a.course_id)} · {a.date} · <strong className={urgent ? 'text-red-600' : ''}>{daysUntil(a.date)}</strong>
                  </div>
                  {a.notes && <div className="text-xs text-gray-400 mt-0.5">{a.notes}</div>}
                </div>
                <button onClick={() => remove(a.id)} className="text-gray-300 hover:text-red-400 text-lg">×</button>
              </div>
            )
          })}
        </div>
      </div>

      {/* Past */}
      {past.length > 0 && (
        <div>
          <h2 className="text-base font-semibold mb-3 text-gray-400">Past ({past.length})</h2>
          <div className="space-y-1">
            {past.slice(-5).reverse().map(a => (
              <div key={a.id} className="flex items-center gap-3 text-sm text-gray-400 py-1">
                <span className={`badge ${TYPE_COLORS[a.type]}`}>{a.type}</span>
                <span>{a.title}</span>
                <span className="text-xs">{courseName(a.course_id)} · {a.date}</span>
                <button onClick={() => remove(a.id)} className="ml-auto hover:text-red-400">×</button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
