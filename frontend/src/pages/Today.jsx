/**
 * Today.jsx — The daily study plan page.
 *
 * Shows the scheduler's output for today: a list of study blocks with course,
 * topic, and time allocation.
 */

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'

function priorityLabel(score) {
  if (score >= 0.7) return { label: 'High priority', cls: 'bg-red-100 text-red-700' }
  if (score >= 0.4) return { label: 'Medium', cls: 'bg-yellow-100 text-yellow-700' }
  return { label: 'Low', cls: 'bg-amber-100 text-amber-800' }
}

export default function Today() {
  const [plan, setPlan] = useState(null)
  const [settings, setSettings] = useState(null)
  const [completed, setCompleted] = useState(new Set())
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  useEffect(() => {
    Promise.all([api.getTodayPlan(), api.getSettings()])
      .then(([p, s]) => { setPlan(p); setSettings(s) })
      .finally(() => setLoading(false))
  }, [])

  async function markDone(item) {
    // Create a session record then mark it complete
    try {
      const session = await api.createSession({
        topic_id: item.topic_id,
        date: plan.date,
        planned_minutes: item.planned_minutes,
      })
      await api.completeSession(session.id, { actual_minutes: item.planned_minutes })
      setCompleted(prev => new Set([...prev, item.topic_id]))
    } catch (e) {
      alert(e.message)
    }
  }

  if (loading) return <div className="text-center py-20 text-gray-400">Loading your plan…</div>
  if (!plan) return null

  const totalDone = plan.items.filter(i => completed.has(i.topic_id)).length
  const pct = plan.items.length ? Math.round(totalDone / plan.items.length * 100) : 0

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">Today's Study Plan</h1>
          <p className="text-gray-500 text-sm mt-1">
            {plan.date} · {plan.total_minutes} min across {plan.items.length} topics
            {settings && ` · Budget: ${settings.daily_minutes} min`}
          </p>
        </div>
        {settings?.streak > 0 && (
          <div className="text-center bg-orange-50 rounded-2xl px-4 py-2 border border-orange-100">
            <div className="text-2xl">🔥</div>
            <div className="text-xs font-semibold text-orange-600">{settings.streak}-day streak</div>
          </div>
        )}
      </div>

      {/* Progress bar */}
      <div className="card">
        <div className="flex justify-between text-sm mb-2">
          <span className="font-semibold">{pct}% complete</span>
          <span className="text-gray-500">{totalDone}/{plan.items.length} blocks done</span>
        </div>
        <div className="h-3 bg-gray-100 rounded-full overflow-hidden">
          <div
            className="h-full bg-blue-500 rounded-full transition-all duration-500"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      {plan.is_day_off && (
        <div className="card bg-gray-50 text-center py-10">
          <div className="text-4xl mb-3">😴</div>
          <p className="font-semibold text-gray-700">Day off — enjoy your rest!</p>
        </div>
      )}

      {/* Study blocks */}
      {!plan.is_day_off && plan.items.length === 0 && (
        <div className="card text-center py-10">
          <div className="text-4xl mb-3">✅</div>
          <p className="font-semibold">No topics need study today.</p>
          <p className="text-sm text-gray-500 mt-1">Add courses or adjust deadlines to generate a plan.</p>
        </div>
      )}

      <div className="space-y-3">
        {plan.items.map((item, idx) => {
          const done = completed.has(item.topic_id)
          const p = priorityLabel(item.priority_score)
          return (
            <div
              key={idx}
              className={`card transition-opacity ${done ? 'opacity-50' : ''}`}
              style={{ borderLeft: `4px solid ${item.course_color}` }}
            >
              <div className="flex items-start gap-4">
                {/* Done toggle */}
                <button
                  onClick={() => !done && markDone(item)}
                  className={`mt-1 w-6 h-6 rounded-full border-2 flex items-center justify-center flex-shrink-0 transition-all ${
                    done ? 'bg-amber-600 border-amber-600 text-white' : 'border-gray-300 hover:border-blue-400'
                  }`}
                >
                  {done && '✓'}
                </button>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-semibold text-sm">{item.topic_name}</span>
                    {item.assessment_boost && (
                      <span className="badge bg-red-100 text-red-700">⚡ Exam soon</span>
                    )}
                    <span className={`badge ${p.cls}`}>{p.label}</span>
                  </div>
                  <div className="text-xs text-gray-500 mt-1">
                    {item.course_name} · {item.chapter}
                  </div>
                  <div className="flex items-center gap-4 mt-2 text-xs text-gray-500">
                    <span>⏱ {item.planned_minutes} min</span>
                    <span>
                      {item.days_since_studied === -1
                        ? '🆕 Never studied'
                        : `📅 Last studied ${item.days_since_studied}d ago`}
                    </span>
                  </div>
                </div>

                <div className="flex flex-col gap-2 flex-shrink-0">
                  <span className="text-lg font-bold text-gray-700">{item.planned_minutes}m</span>
                  {!done && (
                    <button
                      className="btn-primary text-xs py-1 px-2"
                      onClick={() => navigate(item.lecture_id ? `/lesson/${item.lecture_id}` : '/courses')}
                    >
                      Open slides →
                    </button>
                  )}
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {/* Settings nudge */}
      {settings && (
        <div className="card bg-blue-50 border-blue-100 text-sm flex justify-between items-center">
          <span className="text-gray-600">Daily budget: <strong>{settings.daily_minutes} min</strong> · Course cap: <strong>{Math.round(settings.max_course_pct * 100)}%</strong></span>
          <AdjustSettings settings={settings} onSave={s => setSettings(s)} />
        </div>
      )}
    </div>
  )
}

function AdjustSettings({ settings, onSave }) {
  const [open, setOpen] = useState(false)
  const [mins, setMins] = useState(settings.daily_minutes)
  const [cap, setCap] = useState(Math.round(settings.max_course_pct * 100))

  async function save() {
    const s = await api.updateSettings({ daily_minutes: mins, max_course_pct: cap / 100 })
    onSave(s); setOpen(false)
  }

  return (
    <>
      <button className="btn-ghost text-xs" onClick={() => setOpen(true)}>Adjust</button>
      {open && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={() => setOpen(false)}>
          <div className="bg-white rounded-2xl p-6 w-80 shadow-xl" onClick={e => e.stopPropagation()}>
            <h2 className="font-bold mb-4">Study Settings</h2>
            <label className="block mb-3">
              <span className="text-sm text-gray-600">Daily budget: <strong>{mins} min</strong></span>
              <input type="range" min={30} max={1080} step={15} value={mins}
                onChange={e => setMins(+e.target.value)}
                className="w-full mt-1 accent-blue-500" />
              <span className="text-xs text-gray-400">Supports 30 minutes to 18 hours per day.</span>
            </label>
            <label className="block mb-5">
              <span className="text-sm text-gray-600">Max per course: <strong>{cap}%</strong></span>
              <input type="range" min={30} max={80} step={5} value={cap}
                onChange={e => setCap(+e.target.value)}
                className="w-full mt-1 accent-blue-500" />
              <span className="text-xs text-gray-400">Prevents any one course from dominating the session</span>
            </label>
            <div className="flex gap-2">
              <button className="btn-primary flex-1" onClick={save}>Save</button>
              <button className="btn-ghost flex-1" onClick={() => setOpen(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
