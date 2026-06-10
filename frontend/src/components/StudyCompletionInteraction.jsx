import { useEffect, useMemo, useState } from 'react'
import { motion, useAnimationControls } from 'framer-motion'
import { awardStudyCompletion, readXp } from '../gamification'

export default function StudyCompletionInteraction({
  title,
  meta,
  completeLabel = 'Complete',
  completedLabel = 'Completed',
  loadingLabel = 'Saving...',
  progressBefore = 0,
  progressAfter = 100,
  xpAward = 25,
  isCompleted = false,
  disabled = false,
  onComplete,
  onWrong,
  errorMessage = '',
  secondary,
}) {
  const buttonControls = useAnimationControls()
  const [completed, setCompleted] = useState(isCompleted)
  const [saving, setSaving] = useState(false)
  const [burstKey, setBurstKey] = useState(0)
  const [wrongKey, setWrongKey] = useState(0)
  const [localError, setLocalError] = useState('')
  const [progress, setProgress] = useState(isCompleted ? progressAfter : progressBefore)
  const [xpTarget, setXpTarget] = useState(() => readXp())
  const [reward, setReward] = useState(null)
  const displayedXp = useRollingNumber(xpTarget, 600)
  const particles = useMemo(() => makeParticles(18), [])

  useEffect(() => {
    setCompleted(isCompleted)
    setProgress(isCompleted ? progressAfter : progressBefore)
    setReward(null)
    setXpTarget(readXp())
  }, [isCompleted, progressAfter, progressBefore, title])

  async function handleComplete() {
    if (saving || disabled || completed) return

    setSaving(true)
    setLocalError('')
    await buttonControls.start({ scale: 0.9, transition: springSnap(540, 18) })
    await buttonControls.start({ scale: 1.1, transition: springSnap(460, 12) })
    await buttonControls.start({ scale: 1, transition: springSnap(420, 16) })

    try {
      const result = await onComplete?.()
      const nextReward = awardStudyCompletion({ baseXp: xpAward, lessonId: result?.id || title, title })
      setReward(nextReward)
      setXpTarget(nextReward.xpAfter)
      setProgress(progressAfter)
      setCompleted(true)
      setBurstKey(Date.now())
      return result
    } catch (error) {
      setWrongKey(Date.now())
      setLocalError(error?.message || 'Try again.')
      onWrong?.(error)
      return null
    } finally {
      setSaving(false)
    }
  }

  return (
    <motion.div
      key={`completion-card-${wrongKey || 'calm'}`}
      className={`completion-card ${wrongKey ? 'completion-card-wrong' : ''}`}
    >
      <div className="completion-topbar">
        <div>
          <p className="completion-label">Study XP</p>
          <div className="completion-xp">{displayedXp}</div>
        </div>
        <div className="completion-progress-wrap" aria-label={`${Math.round(progress)}% complete`}>
          <div className="completion-progress-track">
            <div className="completion-progress-fill" style={{ width: `${clampPercent(progress)}%` }} />
          </div>
          <span>{Math.round(clampPercent(progress))}%</span>
        </div>
      </div>

      <div className="completion-body">
        <div className="min-w-0">
          <p className="completion-label">Finish this deck</p>
          <h2 className="completion-title">{title}</h2>
          {meta && <p className="completion-meta">{meta}</p>}
        </div>

        <div className="completion-action">
          <motion.button
            type="button"
            className={`completion-button ${completed ? 'completion-button-done' : ''}`}
            animate={buttonControls}
            whileTap={{ scale: 0.94 }}
            disabled={saving || disabled || completed}
            onClick={handleComplete}
          >
            {saving ? loadingLabel : completed ? completedLabel : completeLabel}
          </motion.button>

          {secondary}
        </div>
      </div>

      {burstKey > 0 && (
        <div key={burstKey} className="completion-burst" aria-hidden="true">
          <div className="completion-check">✓</div>
          {particles.map(particle => (
            <span
              key={particle.id}
              className="completion-confetti"
              style={{
                '--dx': `${particle.dx}px`,
                '--dy': `${particle.dy}px`,
                '--delay': `${particle.delay}ms`,
                '--rotate': `${particle.rotate}deg`,
              }}
            />
          ))}
        </div>
      )}

      {reward && (
        <div className="completion-reward">
          <span>+{reward.baseXp + reward.bonusXp} XP</span>
          {reward.bonusXp > 0 && <span>{reward.bonusLabel}: +{reward.bonusXp}</span>}
          {reward.freezeAwarded && <span>Streak freeze earned</span>}
          <span>{reward.identity.title}</span>
        </div>
      )}

      {(localError || errorMessage) && (
        <p className="completion-error">{localError || errorMessage}</p>
      )}
    </motion.div>
  )
}

function useRollingNumber(target, duration = 600) {
  const [value, setValue] = useState(target)

  useEffect(() => {
    const startValue = value
    const delta = target - startValue
    if (!delta) return undefined

    let frame = 0
    const startTime = performance.now()

    function tick(now) {
      const elapsed = Math.min(1, (now - startTime) / duration)
      const eased = 1 - Math.pow(1 - elapsed, 3)
      setValue(Math.round(startValue + delta * eased))
      if (elapsed < 1) frame = requestAnimationFrame(tick)
    }

    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, duration])

  return value
}

function makeParticles(count) {
  return Array.from({ length: count }, (_, index) => {
    const angle = (Math.PI * 2 * index) / count
    const radius = 42 + (index % 5) * 10
    return {
      id: index,
      dx: Math.round(Math.cos(angle) * radius),
      dy: Math.round(Math.sin(angle) * radius),
      delay: (index % 4) * 18,
      rotate: 45 + index * 23,
    }
  })
}

function clampPercent(value) {
  const next = Number(value)
  if (!Number.isFinite(next)) return 0
  return Math.max(0, Math.min(100, next))
}

function springSnap(stiffness, damping) {
  return { type: 'spring', stiffness, damping, mass: 0.65 }
}
