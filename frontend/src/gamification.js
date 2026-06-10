const XP_KEY = 'studypace.xp.total'
const COMPLETION_LOG_KEY = 'studypace.gamification.completions'
const FREEZE_KEY = 'studypace.gamification.streakFreezes'
const BONUS_LOG_KEY = 'studypace.gamification.lastReward'

const IDENTITY_LEVELS = [
  { min: 0, title: 'Study Rookie', next: 120 },
  { min: 120, title: 'Study Apprentice', next: 320 },
  { min: 320, title: 'Study Adept', next: 700 },
  { min: 700, title: 'Exam Climber', next: 1200 },
  { min: 1200, title: 'Finals Weapon', next: 2000 },
  { min: 2000, title: 'Study Master', next: null },
]

export function readXp() {
  try {
    return Math.max(0, Number(localStorage.getItem(XP_KEY)) || 0)
  } catch {
    return 0
  }
}

export function saveXp(value) {
  try {
    localStorage.setItem(XP_KEY, String(Math.max(0, Number(value) || 0)))
  } catch {
    // Cosmetic progression should not block studying.
  }
}

export function readStreakFreezes() {
  try {
    return Math.max(0, Number(localStorage.getItem(FREEZE_KEY)) || 0)
  } catch {
    return 0
  }
}

export function awardStudyCompletion({ baseXp = 25, lessonId = '', title = '' } = {}) {
  const beforeXp = readXp()
  const base = Math.max(0, Number(baseXp) || 0)
  const bonus = bonusRewardFor(`${lessonId}:${title}:${Date.now()}`)
  const totalAward = base + bonus.amount
  const afterXp = beforeXp + totalAward
  const log = readCompletionLog()
  const nextLog = [
    ...log,
    {
      id: `${Date.now()}:${lessonId || title || 'deck'}`,
      date: localISODate(),
      lessonId,
      title,
      baseXp: base,
      bonusXp: bonus.amount,
      createdAt: new Date().toISOString(),
    },
  ].slice(-120)
  let freezeAwarded = false

  saveXp(afterXp)
  saveCompletionLog(nextLog)

  if (nextLog.length > 0 && nextLog.length % 7 === 0) {
    const freezes = readStreakFreezes() + 1
    localStorage.setItem(FREEZE_KEY, String(freezes))
    freezeAwarded = true
  }

  const reward = {
    xpBefore: beforeXp,
    xpAfter: afterXp,
    baseXp: base,
    bonusXp: bonus.amount,
    bonusLabel: bonus.label,
    freezeAwarded,
    identity: identityForXp(afterXp),
  }

  try {
    localStorage.setItem(BONUS_LOG_KEY, JSON.stringify(reward))
  } catch {
    // Optional.
  }

  return reward
}

export function identityForXp(xpValue = readXp()) {
  const xp = Math.max(0, Number(xpValue) || 0)
  const current = [...IDENTITY_LEVELS].reverse().find(level => xp >= level.min) || IDENTITY_LEVELS[0]
  const nextXp = current.next
  const progress = nextXp ? Math.round(((xp - current.min) / (nextXp - current.min)) * 100) : 100

  return {
    title: current.title,
    xp,
    nextXp,
    progress: Math.max(0, Math.min(100, progress)),
  }
}

export function weeklyStudyRecap() {
  const today = localISODate()
  const weekDates = new Set(Array.from({ length: 7 }, (_, index) => addDaysISO(today, -index)))
  const completions = readCompletionLog().filter(item => weekDates.has(item.date))
  const bonusXp = completions.reduce((sum, item) => sum + (Number(item.bonusXp) || 0), 0)
  const strongest = mostCommon(completions.map(item => item.title).filter(Boolean))

  return {
    decks: completions.length,
    bonusXp,
    strongest,
  }
}

export function flameLabel(streak = 0) {
  const value = Math.max(0, Number(streak) || 0)
  if (value >= 30) return 'Wild flame'
  if (value >= 14) return 'Big flame'
  if (value >= 7) return 'Hot streak'
  if (value >= 2) return 'Streak lit'
  return 'Start the flame'
}

function bonusRewardFor(seed) {
  const roll = stableIndex(seed, 100)
  if (roll >= 92) return { amount: 50, label: 'Rare bonus' }
  if (roll >= 76) return { amount: 20, label: 'Bonus XP' }
  return { amount: 0, label: '' }
}

function readCompletionLog() {
  try {
    const parsed = JSON.parse(localStorage.getItem(COMPLETION_LOG_KEY) || '[]')
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function saveCompletionLog(value) {
  try {
    localStorage.setItem(COMPLETION_LOG_KEY, JSON.stringify(value))
  } catch {
    // Optional.
  }
}

function mostCommon(values = []) {
  const counts = new Map()
  for (const value of values) {
    counts.set(value, (counts.get(value) || 0) + 1)
  }
  let best = ''
  let bestCount = 0
  for (const [value, count] of counts.entries()) {
    if (count > bestCount) {
      best = value
      bestCount = count
    }
  }
  return best
}

function stableIndex(seed = '', modulo = 1) {
  let hash = 0
  const text = String(seed)
  for (let index = 0; index < text.length; index += 1) {
    hash = ((hash << 5) - hash + text.charCodeAt(index)) | 0
  }
  return Math.abs(hash) % Math.max(1, modulo)
}

function localISODate() {
  const now = new Date()
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000)
  return local.toISOString().slice(0, 10)
}

function addDaysISO(value, days) {
  const date = new Date(`${value}T00:00:00`)
  date.setDate(date.getDate() + days)
  return date.toISOString().slice(0, 10)
}
