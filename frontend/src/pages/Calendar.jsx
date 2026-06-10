import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, clearApiCache } from '../api'
import { courseDisplayName, setCourseDisplayNameOverride } from '../courseLabels'

const PLAN_TYPES = [
  { value: 'quiz', label: 'Quiz' },
  { value: 'midterm', label: 'Midterm' },
  { value: 'final', label: 'Final' },
  { value: 'assignment', label: 'Assignment' },
]

const PASS_OPTIONS = [
  { value: '1', label: 'Once' },
  { value: '2', label: 'Twice' },
  { value: '3', label: 'Three times' },
]

const FULL_LECTURE_RANGE = 999
const MIN_STUDY_MINUTES = 30
const MAX_STUDY_MINUTES = 1080

export default function Calendar() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [assessments, setAssessments] = useState([])
  const [courses, setCourses] = useState([])
  const [settings, setSettings] = useState(null)
  const [target, setTarget] = useState('loading')
  const [customDays, setCustomDays] = useState(() => savedDays())
  const [planCourseId, setPlanCourseId] = useState(() => savedPlanCourseId())
  const [planAssessmentType, setPlanAssessmentType] = useState(() => savedPlanAssessmentType())
  const [courseDetail, setCourseDetail] = useState(null)
  const [courseDetailsById, setCourseDetailsById] = useState({})
  const [courseDetailsReady, setCourseDetailsReady] = useState(false)
  const [lectureRange, setLectureRange] = useState(() => savedLectureRange())
  const [targetRanges, setTargetRanges] = useState(() => savedTargetRanges())
  const [targetTypes, setTargetTypes] = useState(() => savedTargetTypes())
  const [planPasses, setPlanPasses] = useState(() => savedPlanPasses())
  const [targetDateDrafts, setTargetDateDrafts] = useState({})
  const [targetTitleDrafts, setTargetTitleDrafts] = useState({})
  const [deadlineDrafts, setDeadlineDrafts] = useState({})
  const [deadlineSaving, setDeadlineSaving] = useState(false)
  const [deletingTarget, setDeletingTarget] = useState('')
  const [paceSaving, setPaceSaving] = useState(false)
  const [error, setError] = useState('')
  const lastScopeCourseId = useRef(null)

  const today = localISODate()
  const requestedCourseId = optionalNumber(searchParams.get('course_id'))
  const courseIdsKey = courses.map(course => course.id).join(',')

  useEffect(() => {
    let active = true
    Promise.all([api.getAssessments(), api.getCourses(), api.getSettings().catch(() => null)])
      .then(([assessmentItems, courseItems, settingsData]) => {
        if (!active) return
        if (settingsData) setSettings(settingsData)
        const upcoming = assessmentItems
          .filter(item => item.date >= today && isUsableAssessment(item))
          .sort((a, b) => a.date.localeCompare(b.date))
        const targetItems = buildAssessmentTargets(upcoming, courseItems, today, targetTypes)
        setAssessments(upcoming)
        setCourses(courseItems)
        if (requestedCourseId && courseItems.some(course => course.id === requestedCourseId)) {
          setPlanCourseId(String(requestedCourseId))
          setLectureRange({ start: 1, end: FULL_LECTURE_RANGE })
        } else {
          setPlanCourseId(prev => (
            courseItems.some(course => String(course.id) === String(prev))
              ? prev
              : String(courseItems[0]?.id || '')
          ))
        }
        setDeadlineDrafts(prev => {
          const next = { ...prev }
          for (const course of courseItems) {
            if (!next[course.id]) next[course.id] = preferredDeadline(upcoming, course.id) || course.exam_date || ''
          }
          return next
        })
        setTargetDateDrafts(prev => {
          const next = { ...prev }
          for (const item of targetItems) {
            if (!next[item.targetKey]) next[item.targetKey] = item.date || ''
          }
          for (const course of courseItems) {
            const key = `course:${course.id}`
            if (!next[key]) next[key] = preferredDeadline(upcoming, course.id) || course.exam_date || ''
          }
          return next
        })
        setTargetTitleDrafts(prev => {
          const next = { ...prev }
          for (const item of targetItems) {
            if (!next[item.targetKey]) next[item.targetKey] = item.title || titleForPlanType(assessmentTypeValue(item))
          }
          for (const course of courseItems) {
            const key = `course:${course.id}`
            if (!next[key]) next[key] = titleForPlanType(targetTypes[key] || 'final')
          }
          return next
        })
        setTarget(prev => {
          const savedActivePlan = readActiveStudyPlan()
          if (!requestedCourseId && savedActivePlan?.includeAllAssessments && targetItems.length > 1) return 'all'
          const requestedTarget = requestedCourseId
            ? targetItems.find(item => item.course_id === requestedCourseId)
            : null
          if (requestedTarget) return requestedTarget.targetKey
          if (requestedCourseId && courseItems.some(course => course.id === requestedCourseId)) return 'days'
          if (targetItems.some(item => item.targetKey === prev)) return prev
          const firstCourseId = requestedCourseId || courseItems[0]?.id || null
          const firstTarget = firstCourseId
            ? targetItems.find(item => item.course_id === firstCourseId)
            : null
          return firstTarget?.targetKey || targetItems[0]?.targetKey || (firstCourseId ? `course:${firstCourseId}` : 'days')
        })
      })
      .catch(() => setTarget('days'))
    return () => {
      active = false
    }
  }, [today, requestedCourseId])

  const assessmentTargets = useMemo(() => buildAssessmentTargets(assessments, courses, today, targetTypes), [assessments, courses, today, targetTypes])
  const allAssessmentsSelected = target === 'all' && assessmentTargets.length > 1
  const selectedAssessment = useMemo(() => assessmentTargets.find(item => item.targetKey === target) || null, [assessmentTargets, target])
  const effectiveAssessmentType = selectedAssessment ? assessmentTypeValue(selectedAssessment) : planAssessmentType
  const allAssessmentRunwayDays = assessmentTargets.length
    ? Math.max(1, daysBetween(today, assessmentTargets[assessmentTargets.length - 1].date) + 1)
    : null

  const assessmentRunwayDays = selectedAssessment
    ? Math.max(1, daysBetween(today, selectedAssessment.date) + 1)
    : null
  const planDays = selectedAssessment ? assessmentRunwayDays : allAssessmentsSelected ? allAssessmentRunwayDays : clampDays(customDays, 180)
  const scopeCourseId = selectedAssessment?.course_id || (allAssessmentsSelected ? null : optionalNumber(planCourseId))
  const selectedCourse = scopeCourseId ? courses.find(course => course.id === scopeCourseId) : null
  const sortedLectures = useMemo(() => sortLecturesForPlan(courseDetail?.lectures || []), [courseDetail])
  const maxLecture = sortedLectures.length || 1
  const lectureStart = clampLecture(lectureRange.start, maxLecture)
  const lectureEnd = clampLecture(Math.max(lectureRange.end, lectureStart), maxLecture)
  const allLecturesSelected = Boolean(scopeCourseId && lectureStart === 1 && lectureEnd === maxLecture)

  useEffect(() => {
    localStorage.setItem('studypace.calendar.customDays', String(customDays))
  }, [customDays])

  useEffect(() => {
    localStorage.setItem('studypace.calendar.planCourseId', String(planCourseId || ''))
  }, [planCourseId])

  useEffect(() => {
    localStorage.setItem('studypace.calendar.assessmentType', planAssessmentType)
  }, [planAssessmentType])

  useEffect(() => {
    const previousCourseId = lastScopeCourseId.current
    lastScopeCourseId.current = scopeCourseId || null
    if (!scopeCourseId || previousCourseId === scopeCourseId) return
    if (savedLectureRangeCourseId() !== scopeCourseId) {
      setLectureRange({ start: 1, end: FULL_LECTURE_RANGE })
    }
  }, [scopeCourseId])

  useEffect(() => {
    localStorage.setItem('studypace.calendar.lectureRange', JSON.stringify({ ...lectureRange, courseId: scopeCourseId || null, version: 2 }))
  }, [lectureRange, scopeCourseId])

  useEffect(() => {
    localStorage.setItem('studypace.calendar.targetRanges', JSON.stringify(targetRanges))
  }, [targetRanges])

  useEffect(() => {
    localStorage.setItem('studypace.calendar.targetTypes', JSON.stringify(targetTypes))
  }, [targetTypes])

  useEffect(() => {
    localStorage.setItem('studypace.calendar.passes', String(planPasses))
  }, [planPasses])

  useEffect(() => {
    setCourseDetailsReady(false)
    setCourseDetailsById({})
    if (!courses.length) {
      setCourseDetailsReady(true)
      return
    }
    let active = true
    setCourseDetailsReady(true)
    for (const course of courses) {
      api.getCourseDetail(course.id)
        .then(detail => {
          if (!active || !detail) return
          setCourseDetailsById(prev => ({ ...prev, [course.id]: detail }))
        })
        .catch(() => {})
    }
    return () => {
      active = false
    }
  }, [courseIdsKey])

  useEffect(() => {
    if (target === 'loading') return
    if (courseDetailsReady && !courses.length) {
      navigate('/courses?next=plan', { replace: true })
    }
  }, [target, courses.length, courseDetailsReady, navigate])

  useEffect(() => {
    const id = scopeCourseId
    if (!id) {
      setCourseDetail(null)
      return
    }
    let active = true
    api.getCourseDetail(id)
      .then(detail => {
        if (!active) return
        const ordered = sortLecturesForPlan(detail.lectures || [])
        setCourseDetail(detail)
        setCourseDetailsById(prev => ({ ...prev, [id]: detail }))
        setLectureRange(prev => {
          const max = ordered.length || 1
          const start = clampLecture(prev.start, max)
          const end = clampLecture(prev.end || max, max)
          return { start, end: Math.max(start, end) }
        })
      })
      .catch(() => {
        if (active) setCourseDetail(null)
      })
    return () => {
      active = false
    }
  }, [scopeCourseId])

  async function saveStudyTarget(item) {
    if (!item) return
    const course = courses.find(course => course.id === item.course_id)
    const date = targetDateDrafts[item.targetKey] || deadlineDrafts[item.course_id] || item.date
    if (!course || !date) return

    setDeadlineSaving(true)
    setError('')
    try {
      const detail = courseDetailsById[item.course_id]
      const max = sortLecturesForPlan(detail?.lectures || []).length || 1
      const range = targetRangeFor(targetRanges, item.targetKey, item.course_id, max)
      const planType = targetTypes[item.targetKey] || targetTypes[`course:${item.course_id}`] || assessmentTypeValue(item)
      const title = cleanTargetTitle(targetTitleDrafts[item.targetKey] || item.title, planType)
      let updatedCourse = null

      if (item.synthetic || shouldSyncCourseDate({ ...item, planType })) {
        updatedCourse = await api.updateCourse(course.id, {
          name: course.name,
          description: course.description || '',
          total_hours: course.total_hours || 40,
          exam_date: date,
          color: course.color || '#007aff',
        })
      }

      const payload = {
        course_id: course.id,
        title,
        date,
        type: storageTypeForPlanType(planType, item.type),
        notes: item.notes || 'Deadline set from the planner.',
      }
      const saved = item.synthetic
        ? await api.createAssessment(payload)
        : await api.updateAssessment(item.id, payload)
      const savedTargetKey = `assessment:${saved.id}`

      if (updatedCourse) {
        setCourses(prev => prev.map(existing => existing.id === updatedCourse.id ? updatedCourse : existing))
      }
      setAssessments(prev => {
        const withoutSaved = prev.filter(existing => existing.id !== saved.id)
        return [...withoutSaved, saved]
          .filter(existing => existing.date >= today && isUsableAssessment(existing))
          .sort((a, b) => a.date.localeCompare(b.date))
      })
      setTargetTypes(prev => ({
        ...prev,
        [savedTargetKey]: planType,
        ...(item.synthetic ? { [`course:${course.id}`]: planType } : {}),
      }))
      setTargetRanges(prev => ({
        ...prev,
        [savedTargetKey]: range,
        ...(item.synthetic ? { [`course:${course.id}`]: range } : {}),
      }))
      setTargetTitleDrafts(prev => ({
        ...prev,
        [savedTargetKey]: saved.title,
        ...(item.synthetic ? { [`course:${course.id}`]: saved.title } : {}),
      }))
      setTarget(savedTargetKey)
      setPlanCourseId(String(course.id))
      setPlanAssessmentType(planType)
      setLectureRange(range)
      const allUpcomingTargets = [
        ...assessmentTargets.filter(target => target.targetKey !== item.targetKey),
        { ...saved, targetKey: savedTargetKey, course_id: course.id, planType },
      ].filter(target => target.date >= today)
      const latestTargetDate = allUpcomingTargets
        .map(target => target.date)
        .sort()
        .at(-1) || saved.date
      activateSpecificPlan({
        target: 'all',
        days: Math.max(1, daysBetween(today, latestTargetDate) + 1),
        includeAllAssessments: true,
        assessmentCount: allUpcomingTargets.length || 1,
        assessmentId: null,
        assessmentTitle: '',
        assessmentType: '',
        assessmentDate: '',
        courseId: null,
        courseName: '',
        lectureStart: null,
        lectureEnd: null,
        lectureAll: false,
        passes: planPasses,
        label: 'All upcoming assessments',
        totalMinutes,
      })
    } catch (err) {
      setError(err.message || 'Could not save this plan.')
    } finally {
      setDeadlineSaving(false)
    }
  }

  async function removeStudyTarget(item) {
    if (!item || deletingTarget) return
    const course = courses.find(course => course.id === item.course_id)
    const fallback = fallbackTargetAfterRemoval(item, assessmentTargets, courses, targetTypes)
    setDeletingTarget(item.targetKey)
    setError('')

    try {
      if (!item.synthetic) {
        await api.deleteAssessment(item.id)
        setAssessments(prev => prev.filter(existing => existing.id !== item.id))
      }

      if (course && (item.synthetic || (shouldSyncCourseDate(item) && course.exam_date === item.date))) {
        const updatedCourse = await api.updateCourse(course.id, {
          name: course.name,
          description: course.description || '',
          total_hours: course.total_hours || 40,
          exam_date: null,
          color: course.color || '#007aff',
        })
        setCourses(prev => prev.map(existing => existing.id === updatedCourse.id ? updatedCourse : existing))
        setCourseDetail(prev => prev?.course?.id === updatedCourse.id ? { ...prev, course: updatedCourse } : prev)
        setCourseDetailsById(prev => {
          const existing = prev[updatedCourse.id]
          return existing ? { ...prev, [updatedCourse.id]: { ...existing, course: updatedCourse } } : prev
        })
      }

      setTargetTypes(prev => removeTargetKeys(prev, item))
      setTargetRanges(prev => removeTargetKeys(prev, item))
      setTargetTitleDrafts(prev => removeTargetKeys(prev, item))
      setTargetDateDrafts(prev => removeTargetKeys(prev, item, { clearCourseDraft: true }))
      setDeadlineDrafts(prev => ({ ...prev, [item.course_id]: '' }))
      clearActivePlanForRemovedTarget(item)

      setTarget(fallback.targetKey)
      setPlanCourseId(String(fallback.course_id))
      setPlanAssessmentType(assessmentTypeValue(fallback))

      const detail = courseDetailsById[fallback.course_id]
      const max = sortLecturesForPlan(detail?.lectures || []).length || 1
      const range = targetRangeFor(targetRanges, fallback.targetKey, fallback.course_id, max)
      setLectureRange(range)
    } catch (err) {
      setError(err.message || 'Could not remove this item.')
    } finally {
      setDeletingTarget('')
    }
  }

  const totalMinutes = useMemo(() => estimatePlanMinutes({
    allAssessmentsSelected,
    assessmentTargets,
    courseDetailsById,
    scopeCourseId,
    lectureStart,
    lectureEnd,
    planPasses,
    targetRanges,
  }), [allAssessmentsSelected, assessmentTargets, courseDetailsById, scopeCourseId, lectureStart, lectureEnd, planPasses, targetRanges])

  function activateCurrentPlan({ goToday = false } = {}) {
    const payload = {
      target,
      days: planDays,
      includeAllAssessments: allAssessmentsSelected,
      assessmentCount: allAssessmentsSelected ? assessmentTargets.length : null,
      assessmentId: allAssessmentsSelected ? null : selectedAssessment?.id || null,
      assessmentTitle: allAssessmentsSelected ? '' : selectedAssessment?.title || '',
      assessmentType: allAssessmentsSelected ? '' : effectiveAssessmentType,
      assessmentDate: allAssessmentsSelected ? '' : selectedAssessment?.date || '',
      courseId: allAssessmentsSelected ? null : scopeCourseId || null,
      courseName: allAssessmentsSelected ? '' : selectedCourse ? courseDisplayName(courses, selectedCourse) : '',
      lectureStart: !allAssessmentsSelected && scopeCourseId ? lectureStart : null,
      lectureEnd: !allAssessmentsSelected && scopeCourseId ? lectureEnd : null,
      lectureAll: !allAssessmentsSelected && allLecturesSelected,
      passes: planPasses,
      label: allAssessmentsSelected ? 'All upcoming assessments' : selectedAssessment ? selectedAssessment.title : 'Study plan',
      totalMinutes,
    }
    activateSpecificPlan(payload, { goToday })
  }

  function activateAllUpcomingPlan({ goToday = false } = {}) {
    const payload = {
      target: 'all',
      days: allAssessmentRunwayDays || planDays,
      includeAllAssessments: true,
      assessmentCount: assessmentTargets.length,
      assessmentId: null,
      assessmentTitle: '',
      assessmentType: '',
      assessmentDate: '',
      courseId: null,
      courseName: '',
      lectureStart: null,
      lectureEnd: null,
      lectureAll: false,
      passes: planPasses,
      label: 'All upcoming assessments',
      totalMinutes,
    }
    setTarget('all')
    activateSpecificPlan(payload, { goToday })
  }

  function activateSpecificPlan(payload, { goToday = false } = {}) {
    const saved = {
      active: true,
      createdAt: new Date().toISOString(),
      ...payload,
    }
    localStorage.setItem(STUDY_PLAN_KEY, JSON.stringify(saved))
    if (goToday) navigate('/')
  }

  function resetLectureRangeToCourse(value) {
    setPlanCourseId(value)
    if (value) setLectureRange({ start: 1, end: FULL_LECTURE_RANGE })
  }

  function updateTargetRange(targetKey, courseId, maxLecture, updates) {
    const current = targetRangeFor(targetRanges, targetKey, courseId, maxLecture)
    const next = normalizeLectureRange({ ...current, ...updates }, maxLecture)
    const saveAsCourseDefault = String(targetKey).startsWith('course:')
    setTargetRanges(prev => ({
      ...prev,
      [targetKey]: next,
      ...(saveAsCourseDefault ? { [`course:${courseId}`]: next } : {}),
    }))
    if (target === targetKey) setLectureRange(next)
  }

  function updateTargetType(item, value) {
    setTargetTypes(prev => ({
      ...prev,
      [item.targetKey]: value,
      ...(item.synthetic ? { [`course:${item.course_id}`]: value } : {}),
    }))
    setTargetTitleDrafts(prev => {
      const current = String(prev[item.targetKey] ?? item.title ?? '').trim()
      if (current && !isGenericTargetTitle(current)) return prev
      return {
        ...prev,
        [item.targetKey]: titleForPlanType(value),
        ...(item.synthetic ? { [`course:${item.course_id}`]: titleForPlanType(value) } : {}),
      }
    })
    if (target === item.targetKey) setPlanAssessmentType(value)
  }

  function useStudyTarget(item) {
    const detail = courseDetailsById[item.course_id]
    const max = sortLecturesForPlan(detail?.lectures || []).length || 1
    const range = targetRangeFor(targetRanges, item.targetKey, item.course_id, max)
    setTarget(item.targetKey)
    setPlanCourseId(String(item.course_id))
    setPlanAssessmentType(assessmentTypeValue(item))
    setLectureRange(range)
    localStorage.setItem('studypace.calendar.planCourseId', String(item.course_id))
    localStorage.setItem('studypace.calendar.lectureRange', JSON.stringify({
      ...range,
      courseId: item.course_id,
      version: 2,
    }))
  }

  async function regeneratePlan() {
    setError('')
    try {
      await api.regeneratePlan()
      clearApiCache('/learning/calendar')
      clearApiCache('/learning/overview')
      window.location.reload()
    } catch (e) {
      setError(e.message)
    }
  }

  async function updateDailyMinutes(minutes) {
    setPaceSaving(true)
    setError('')
    try {
      const updated = await api.updateSettings({ daily_minutes: minutes })
      setSettings(updated)
    } catch (err) {
      setError(err.message || 'Could not update study time.')
    } finally {
      setPaceSaving(false)
    }
  }

  async function renameCourse(courseId, name) {
    const course = courses.find(item => item.id === courseId)
    const nextName = name.trim()
    if (!course || nextName.length < 2) return null

    const optimistic = { ...course, name: nextName }
    setCourseDisplayNameOverride(course.id, nextName)
    setCourses(prev => prev.map(item => item.id === course.id ? optimistic : item))
    setCourseDetail(prev => prev?.course?.id === course.id ? { ...prev, course: optimistic } : prev)
    setCourseDetailsById(prev => {
      const existing = prev[course.id]
      return existing ? { ...prev, [course.id]: { ...existing, course: optimistic } } : prev
    })

    const updated = await api.updateCourse(course.id, {
      name: nextName,
      description: course.description || '',
      total_hours: course.total_hours || 40,
      exam_date: course.exam_date || null,
      color: course.color || '#007aff',
    })

    setCourseDisplayNameOverride(updated.id, nextName)
    setCourses(prev => prev.map(item => item.id === updated.id ? updated : item))
    setCourseDetail(prev => prev?.course?.id === updated.id ? { ...prev, course: updated } : prev)
    setCourseDetailsById(prev => {
      const existing = prev[updated.id]
      return existing ? { ...prev, [updated.id]: { ...existing, course: updated } } : prev
    })
    return updated
  }

  return (
    <div className="page space-y-4">
      <section className="surface p-3 sm:p-4">
        <div className="px-1 pb-3">
          <p className="eyebrow text-[var(--accent)]">Journey setup</p>
          <h1 className="mt-1 section-title text-lg">Choose the destination</h1>
        </div>

        <StudyTargetCards
          targets={assessmentTargets}
          courses={courses}
          drafts={{ ...deadlineDrafts, ...targetDateDrafts }}
          titleDrafts={targetTitleDrafts}
          detailsByCourseId={courseDetailsById}
          ranges={targetRanges}
          targetTypes={targetTypes}
          passes={planPasses}
          activeTarget={target}
          today={today}
          savingDates={deadlineSaving}
          settings={settings}
          paceSaving={paceSaving}
          onDateChange={(item, value) => {
            setTargetDateDrafts(prev => ({ ...prev, [item.targetKey]: value }))
            setDeadlineDrafts(prev => ({ ...prev, [item.course_id]: value }))
          }}
          onTitleChange={(item, value) => {
            setTargetTitleDrafts(prev => ({ ...prev, [item.targetKey]: value }))
          }}
          onSaveTarget={saveStudyTarget}
          onRangeChange={updateTargetRange}
          onTypeChange={updateTargetType}
          onPassesChange={value => setPlanPasses(clampPasses(value))}
          onUseTarget={useStudyTarget}
          onStudyTime={updateDailyMinutes}
          onRenameCourse={renameCourse}
          onRemoveTarget={removeStudyTarget}
          deletingTarget={deletingTarget}
          onRegenerate={regeneratePlan}
        />

      </section>
    </div>
  )
}

function StudyTargetCards({
  targets,
  courses,
  drafts,
  titleDrafts,
  detailsByCourseId,
  ranges,
  targetTypes,
  passes,
  activeTarget,
  today,
  savingDates,
  settings,
  paceSaving,
  onDateChange,
  onTitleChange,
  onSaveTarget,
  onRangeChange,
  onTypeChange,
  onPassesChange,
  onUseTarget,
  onStudyTime,
  onRenameCourse,
  onRemoveTarget,
  deletingTarget,
  onRegenerate,
}) {
  const allCards = buildStudyCards(targets, courses, drafts, targetTypes)
  const courseCards = courses
    .map(course => allCards.find(item => item.course_id === course.id))
    .filter(Boolean)
  const selectedItem = allCards.find(item => item.targetKey === activeTarget) || courseCards[0] || null
  const selectedCourse = selectedItem ? courses.find(course => course.id === selectedItem.course_id) : null
  const selectedCourseTargets = selectedItem
    ? allCards.filter(item => item.course_id === selectedItem.course_id)
    : []
  const detail = selectedItem ? detailsByCourseId[selectedItem.course_id] : null
  const lectures = sortLecturesForPlan(detail?.lectures || [])
  const detailLoaded = Boolean(detail)
  const maxLecture = lectures.length || 1
  const range = selectedItem ? targetRangeFor(ranges, selectedItem.targetKey, selectedItem.course_id, maxLecture) : { start: 1, end: 1 }
  const selectedType = selectedItem ? assessmentTypeValue(selectedItem) : 'quiz'
  const date = selectedItem ? drafts[selectedItem.targetKey] || drafts[selectedItem.course_id] || selectedItem.date || '' : ''
  const title = selectedItem ? titleDrafts[selectedItem.targetKey] ?? selectedItem.title ?? titleForPlanType(selectedType) : ''
  const dirtyDate = Boolean(selectedItem && date && selectedItem.date && date !== selectedItem.date)
  const dirtyTitle = Boolean(selectedItem && title.trim() && title.trim() !== (selectedItem.title || '').trim())
  const daysLeft = date ? Math.max(0, daysBetween(today, date)) : null
  const slideLabel = lectures.length
    ? range.start === 1 && range.end === lectures.length
      ? `All ${lectures.length} decks`
      : `Decks ${range.start}-${range.end} of ${lectures.length}`
    : detailLoaded ? 'No slides' : 'Loading'
  const canSave = Boolean(selectedItem && date && lectures.length)

  return (
    <div>
      {courseCards.length === 0 ? (
        <div className="mt-4 rounded-lg border border-dashed border-[var(--border-strong)] p-5 text-sm font-semibold text-[var(--text-muted)]">
          Set up your courses and slide decks from Courses first.
        </div>
      ) : (
        <>
          <div className="mt-4 space-y-1">
            <span className="eyebrow">Course</span>
            <CoursePickerControl
              courses={courses}
              value={selectedItem?.course_id || ''}
              onChange={value => {
                const nextItem = courseCards.find(item => String(item.course_id) === String(value))
                if (nextItem) onUseTarget(nextItem)
              }}
            />
            {selectedCourse && (
              <CourseRenameInline
                course={selectedCourse}
                displayName={courseDisplayName(courses, selectedCourse)}
                onRename={onRenameCourse}
              />
            )}
          </div>

          <label className="mt-4 block space-y-1">
            <span className="eyebrow">Assessment name</span>
            <input
              className="input"
              value={title}
              placeholder={titleForPlanType(selectedType)}
              onChange={event => selectedItem && onTitleChange(selectedItem, event.target.value)}
            />
          </label>

          <div className="mt-3 grid gap-3 lg:grid-cols-3">
            <label className="block space-y-1">
              <span className="eyebrow">Test date</span>
              <input
                className="input"
                type="date"
                value={date}
                onChange={event => selectedItem && onDateChange(selectedItem, event.target.value)}
              />
              {daysLeft !== null && <span className="block text-xs font-semibold text-[var(--text-faint)]">{daysLeftLabel(daysLeft)}</span>}
            </label>
            <div className="block space-y-1">
              <span className="eyebrow">Target type</span>
              <TargetTypeControl
                value={selectedType}
                onChange={value => selectedItem && onTypeChange(selectedItem, value)}
              />
            </div>
            <div className="block space-y-1">
              <span className="eyebrow">Review passes</span>
              <PassesControl
                value={passes}
                onChange={onPassesChange}
              />
              <span className="block text-xs font-medium text-[var(--text-faint)]">
                Each pass is one full review of the selected slides.
              </span>
            </div>
          </div>

          <details className="surface-soft mt-3 p-3">
            <summary className="cursor-pointer select-none marker:text-[var(--text-faint)]">
              <span className="ml-1 inline-flex w-[calc(100%-1rem)] items-center justify-between gap-2 align-middle">
                <span className="eyebrow">Slides</span>
                <span className="text-xs font-semibold text-[var(--accent)]">{slideLabel}</span>
              </span>
            </summary>

            {lectures.length ? (
              <>
                <p className="mt-3 text-xs font-medium text-[var(--text-faint)]">
                  Choose which slide decks this assessment covers. Leave it on all slides when you want the whole course included.
                </p>
                <div className="mt-3 grid grid-cols-2 gap-2">
                  <label className="space-y-1">
                    <span className="text-[11px] font-semibold text-[var(--text-faint)]">From</span>
                    <input
                      className="input"
                      type="number"
                      min="1"
                      max={maxLecture}
                      value={range.start}
                      onChange={event => selectedItem && onRangeChange(selectedItem.targetKey, selectedItem.course_id, maxLecture, { start: Number(event.target.value) || 1 })}
                    />
                  </label>
                  <label className="space-y-1">
                    <span className="text-[11px] font-semibold text-[var(--text-faint)]">To</span>
                    <input
                      className="input"
                      type="number"
                      min={range.start}
                      max={maxLecture}
                      value={range.end}
                      onChange={event => selectedItem && onRangeChange(selectedItem.targetKey, selectedItem.course_id, maxLecture, { end: Number(event.target.value) || maxLecture })}
                    />
                  </label>
                </div>
                <p className="mt-2 text-xs font-medium text-[var(--text-faint)]">
                  {lectureRangeLabel(lectures, range.start, range.end)}
                </p>
              </>
            ) : detailLoaded ? (
              <p className="mt-3 text-sm font-medium text-[var(--text-muted)]">
                No slide decks yet. Manage them from Courses.
              </p>
            ) : (
              <p className="mt-3 text-sm font-medium text-[var(--text-muted)]">
                Loading slides...
              </p>
            )}
          </details>

          {selectedCourseTargets.length > 0 && (
            <div className="mt-3 space-y-1">
              <span className="eyebrow">Saved assessments</span>
              <MenuSelect
                value={selectedItem?.targetKey || ''}
                options={selectedCourseTargets.map(item => ({
                  value: item.targetKey,
                  label: `${targetTitle(titleDrafts, item)} · ${formatCompactDate(item.date)}`,
                  removable: isRemovableTarget(item),
                  removing: deletingTarget === item.targetKey,
                  item,
                }))}
                onChange={value => {
                  const nextItem = selectedCourseTargets.find(item => item.targetKey === value)
                  if (nextItem) onUseTarget(nextItem)
                }}
                onRemove={option => onRemoveTarget(option.item)}
              />
            </div>
          )}

          <PlanJourneyPreview
            title={title}
            date={date}
            daysLeft={daysLeft}
            slideLabel={slideLabel}
            passes={passes}
          />

          {dirtyDate && (
            <p className="mt-3 text-xs font-semibold text-amber-300">Save to rebuild the plan with this date.</p>
          )}

          {dirtyTitle && (
            <p className="mt-3 text-xs font-semibold text-[var(--accent)]">Save to rename this assessment.</p>
          )}

          <button
            className="btn-primary mt-4 w-full"
            disabled={savingDates || !canSave}
            onClick={() => onSaveTarget(selectedItem)}
          >
            {savingDates ? 'Saving...' : 'Save and make plan'}
          </button>

          <button
            className="btn-secondary mt-2 w-full"
            onClick={onRegenerate}
          >
            Regenerate plan
          </button>
        </>
      )}
    </div>
  )
}

function TargetTypeControl({ value, onChange }) {
  return (
    <MenuSelect
      value={value}
      options={PLAN_TYPES}
      onChange={onChange}
    />
  )
}

function PlanJourneyPreview({ title, date, daysLeft, slideLabel, passes }) {
  const safeTitle = cleanTargetTitle(title || 'Assessment')
  const passLabel = Number(passes) > 1 ? `${passes} passes` : 'One pass'
  const destination = date ? `${formatCompactDate(date)} · ${daysLeftLabel(daysLeft)}` : 'Pick a date'

  return (
    <section className="journey-panel mt-4">
      <div className="journey-panel-head">
        <div>
          <p className="eyebrow">Journey</p>
          <h2>{safeTitle}</h2>
        </div>
        <span className="badge text-[var(--accent)]">{destination}</span>
      </div>
      <div className="journey-steps mt-4">
        <div className="journey-step journey-step-done">
          <span className="journey-dot" />
          <strong>Start</strong>
          <p>Slides loaded</p>
        </div>
        <div className="journey-step journey-step-active">
          <span className="journey-dot" />
          <strong>Route</strong>
          <p>{slideLabel}</p>
        </div>
        <div className="journey-step">
          <span className="journey-dot" />
          <strong>Finish</strong>
          <p>{passLabel} then review</p>
        </div>
      </div>
    </section>
  )
}

function PassesControl({ value, onChange }) {
  return (
    <MenuSelect
      value={String(clampPasses(value))}
      options={PASS_OPTIONS}
      onChange={onChange}
    />
  )
}

function MenuSelect({ value, options, onChange, onRemove, placeholder = 'Choose' }) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef(null)
  const selected = options.find(option => String(option.value) === String(value))

  useEffect(() => {
    if (!open) return undefined

    function handlePointerDown(event) {
      if (!rootRef.current?.contains(event.target)) setOpen(false)
    }

    function handleKeyDown(event) {
      if (event.key === 'Escape') setOpen(false)
    }

    window.addEventListener('pointerdown', handlePointerDown)
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      window.removeEventListener('pointerdown', handlePointerDown)
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [open])

  function choose(nextValue) {
    onChange(nextValue)
    setOpen(false)
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        className="input flex min-h-11 items-center justify-between gap-3 text-left"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen(current => !current)}
      >
        <span className={`min-w-0 flex-1 truncate ${selected ? 'text-[var(--text)]' : 'text-[var(--text-faint)]'}`}>
          {selected?.label || placeholder}
        </span>
        <span className={`shrink-0 text-[var(--text-faint)] transition ${open ? 'rotate-180' : ''}`} aria-hidden="true">
          <svg className="h-4 w-4" viewBox="0 0 20 20" fill="none">
            <path d="m5 7.5 5 5 5-5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
      </button>

      {open && (
        <div className="absolute inset-x-0 top-full z-50 mt-2 max-h-64 overflow-auto rounded-lg border border-[var(--border-strong)] bg-[var(--surface-raised)] p-1">
          <div role="listbox" aria-label={placeholder}>
            {options.map(option => {
              const active = String(option.value) === String(value)
              const removable = Boolean(option.removable && onRemove)
              return (
                <div key={option.value} className="flex w-full items-stretch gap-1">
                  <button
                    type="button"
                    role="option"
                    aria-selected={active}
                    className={`flex min-w-0 flex-1 items-center justify-between gap-3 rounded-md px-3 py-2.5 text-left text-sm font-semibold transition ${
                      active
                        ? 'bg-[var(--accent)] text-[var(--accent-text)]'
                        : 'text-[var(--text-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--text)]'
                    }`}
                    onClick={() => choose(option.value)}
                  >
                    <span className="min-w-0 truncate">{option.label}</span>
                    {active && <span aria-hidden="true">✓</span>}
                  </button>
                  {removable && (
                    <button
                      type="button"
                      className="grid w-10 shrink-0 place-items-center rounded-md text-[var(--text-faint)] transition hover:bg-rose-500/10 hover:text-[var(--danger)] disabled:cursor-not-allowed disabled:opacity-40"
                      aria-label={`Remove ${option.label}`}
                      title="Remove"
                      disabled={option.removing}
                      onClick={event => {
                        event.preventDefault()
                        event.stopPropagation()
                        onRemove(option)
                      }}
                    >
                      <svg className="h-4 w-4" viewBox="0 0 20 20" fill="none" aria-hidden="true">
                        <path d="M5.5 5.5l9 9M14.5 5.5l-9 9" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                      </svg>
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

function CourseRenameInline({ course, displayName, onRename }) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState(displayName)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    setDraft(displayName)
    setSaved(false)
    setError('')
  }, [course.id, displayName])

  async function saveName() {
    const nextName = draft.trim()
    if (nextName.length < 2 || saving) return
    setSaving(true)
    setError('')
    try {
      const renamePromise = onRename(course.id, nextName)
      setSaved(true)
      setOpen(false)
      await renamePromise
    } catch (err) {
      setOpen(true)
      setError(err.message || 'Could not rename this course.')
    } finally {
      setSaving(false)
    }
  }

  if (!open) {
    return (
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button type="button" className="btn-ghost min-h-8 px-3 py-1.5 text-xs" onClick={() => setOpen(true)}>
          Rename course
        </button>
        {saved && <span className="text-xs font-semibold text-[var(--accent)]">Saved</span>}
      </div>
    )
  }

  return (
    <div className="surface-soft mt-2 p-3">
      <label className="block space-y-1">
        <span className="eyebrow">Course name</span>
        <input
          className="input"
          value={draft}
          placeholder="Course 1"
          onChange={event => setDraft(event.target.value)}
          onKeyDown={event => {
            if (event.key === 'Enter') {
              event.preventDefault()
              saveName()
            }
            if (event.key === 'Escape') setOpen(false)
          }}
        />
      </label>
      {error && <p className="mt-2 text-xs font-semibold text-rose-300">{error}</p>}
      <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" className="btn-primary px-4 py-2 text-sm" disabled={saving || draft.trim().length < 2} onClick={saveName}>
          {saving ? 'Saving...' : 'Save name'}
        </button>
        <button type="button" className="btn-ghost px-4 py-2 text-sm" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </div>
  )
}

function CoursePickerControl({ courses, value, onChange }) {
  if (!courses.length) {
    return (
      <div className="min-h-11 rounded-lg border border-dashed border-[var(--border-strong)] px-3 py-2 text-sm font-semibold text-[var(--text-muted)]">
        Add courses first
      </div>
    )
  }

  return (
    <MenuSelect
      value={String(value || '')}
      options={courses.map(course => ({
        value: String(course.id),
        label: courseDisplayName(courses, course),
      }))}
      onChange={onChange}
      placeholder="Choose course"
    />
  )
}

function StudyTimeControl({ settings, saving, onPick }) {
  const current = clampStudyMinutes(settings?.daily_minutes || 135)
  const [draft, setDraft] = useState(String(current))

  useEffect(() => {
    setDraft(String(current))
  }, [current])

  function applyDraft() {
    const next = clampStudyMinutes(draft)
    setDraft(String(next))
    onPick(next)
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <span className="eyebrow">Daily study time</span>
        <span className="text-xs font-semibold text-[var(--accent)]">{formatStudyTime(current)}</span>
      </div>
      <input
        className="w-full accent-[var(--accent)]"
        type="range"
        min={MIN_STUDY_MINUTES}
        max={MAX_STUDY_MINUTES}
        step={15}
        value={clampStudyMinutes(draft)}
        disabled={saving}
        onChange={event => setDraft(event.target.value)}
      />
      <div className="grid grid-cols-[1fr_auto] gap-2">
        <label className="space-y-1">
          <span className="text-[11px] font-semibold text-[var(--text-faint)]">Minutes per day</span>
          <input
            className="input"
            type="number"
            min={MIN_STUDY_MINUTES}
            max={MAX_STUDY_MINUTES}
            step={15}
            value={draft}
            disabled={saving}
            onChange={event => setDraft(event.target.value)}
          />
        </label>
        <button
          type="button"
          className="btn-primary self-end px-4"
          disabled={saving || clampStudyMinutes(draft) === current}
          onClick={applyDraft}
        >
          Apply
        </button>
      </div>
      <p className="text-xs font-medium text-[var(--text-faint)]">
        Range: 30 minutes to 18 hours per day.
      </p>
    </div>
  )
}

function daysLeftLabel(days) {
  const value = Math.max(0, Number(days) || 0)
  if (value === 0) return 'Today'
  if (value === 1) return 'Tomorrow'
  return `${value} days left`
}

function isRemovableTarget(item = {}) {
  return !item.synthetic || Boolean(item.date)
}

function fallbackTargetAfterRemoval(item, targets = [], courses = [], targetTypes = {}) {
  const sameCourse = targets.find(target => target.targetKey !== item.targetKey && target.course_id === item.course_id)
  if (sameCourse) return sameCourse

  const course = courses.find(course => course.id === item.course_id)
  const targetKey = `course:${item.course_id}`
  const planType = targetTypes[targetKey] || 'final'
  return {
    id: targetKey,
    targetKey,
    synthetic: true,
    course_id: item.course_id,
    title: titleForPlanType(planType),
    date: '',
    type: storageTypeForPlanType(planType),
    planType,
    notes: course ? `New target for ${courseDisplayName(courses, course)}.` : '',
  }
}

function removeTargetKeys(values = {}, item = {}, options = {}) {
  const next = { ...values }
  delete next[item.targetKey]
  if (item.synthetic || options.clearCourseDraft) {
    delete next[`course:${item.course_id}`]
    if (options.clearCourseDraft) next[item.course_id] = ''
  }
  return next
}

function clearActivePlanForRemovedTarget(item = {}) {
  try {
    const saved = JSON.parse(localStorage.getItem(STUDY_PLAN_KEY) || 'null')
    if (!saved?.active) return
    const removedSpecificPlan = saved.target === item.targetKey || (!item.synthetic && Number(saved.assessmentId) === Number(item.id))
    const removedCourseDatePlan = item.synthetic && saved.courseId === item.course_id && saved.assessmentDate === item.date
    if (removedSpecificPlan || removedCourseDatePlan) {
      localStorage.removeItem(STUDY_PLAN_KEY)
    }
  } catch {
    localStorage.removeItem(STUDY_PLAN_KEY)
  }
}

function clampStudyMinutes(value) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return 135
  return Math.max(MIN_STUDY_MINUTES, Math.min(MAX_STUDY_MINUTES, Math.round(parsed / 15) * 15))
}

function formatStudyTime(minutes) {
  const value = clampStudyMinutes(minutes)
  const hours = Math.floor(value / 60)
  const mins = value % 60
  if (!hours) return `${mins} min`
  if (!mins) return `${hours} ${hours === 1 ? 'hour' : 'hours'}`
  return `${hours}h ${mins}m`
}

function assessmentKind(item) {
  const drafted = planTypeOption(assessmentTypeValue(item))
  if (drafted) return drafted.label
  const title = item.title.toLowerCase()
  if (title.includes('final')) return 'Final'
  if (title.includes('midterm') || title.includes('mid-term')) return 'Midterm'
  if (title.includes('quiz') || item.type === 'quiz') return 'Quiz'
  if (item.type === 'exam') return 'Exam'
  return item.type ? item.type[0].toUpperCase() + item.type.slice(1) : 'Assessment'
}

function courseCode(name = '') {
  return name.split(' - ')[0].trim() || name
}

function preferredDeadline(assessments, courseId) {
  return deadlineAssessment(assessments, courseId)?.date || ''
}

function buildAssessmentTargets(assessments, courses, today, targetTypes = {}) {
  const realTargets = assessments
    .filter(item => item.date >= today && isUsableAssessment(item))
    .map(item => {
      const targetKey = `assessment:${item.id}`
      const obviousType = obviousAssessmentType(item)
      const savedType = planTypeOption(targetTypes[targetKey]) ? targetTypes[targetKey] : ''
      const planType = obviousType || savedType || assessmentTypeValue(item)
      return {
        ...item,
        targetKey,
        synthetic: false,
        planType,
        type: storageTypeForPlanType(planType, item.type),
      }
    })

  const courseTargets = courses
    .filter(course => course.exam_date && course.exam_date >= today)
    .filter(course => !realTargets.some(item => item.course_id === course.id))
    .map(course => {
      const targetKey = `course:${course.id}`
      const planType = targetTypes[targetKey] || 'final'
      return {
        id: `course-${course.id}`,
        targetKey,
        synthetic: true,
        course_id: course.id,
        title: titleForPlanType(planType),
        date: course.exam_date,
        type: storageTypeForPlanType(planType),
        planType,
        notes: 'Course target date from imported syllabus setup.',
      }
    })

  return [...realTargets, ...courseTargets].sort((a, b) => a.date.localeCompare(b.date))
}

function buildStudyCards(targets, courses, drafts, targetTypes = {}) {
  const seenCourses = new Set()
  const cards = targets.map(item => {
    seenCourses.add(item.course_id)
    return item
  })

  for (const course of courses) {
    if (seenCourses.has(course.id)) continue
    const targetKey = `course:${course.id}`
    const planType = targetTypes[targetKey] || 'final'
    cards.push({
      id: `course-${course.id}`,
      targetKey,
      synthetic: true,
      course_id: course.id,
      title: titleForPlanType(planType),
      date: drafts[course.id] || course.exam_date || '',
      type: storageTypeForPlanType(planType),
      planType,
      notes: '',
    })
  }

  return cards.sort((a, b) => {
    const dateA = a.date || '9999-12-31'
    const dateB = b.date || '9999-12-31'
    return dateA.localeCompare(dateB) || String(a.course_id).localeCompare(String(b.course_id))
  })
}

function estimatePlanMinutes({
  allAssessmentsSelected,
  assessmentTargets,
  courseDetailsById,
  scopeCourseId,
  lectureStart,
  lectureEnd,
  planPasses,
  targetRanges,
}) {
  const passes = clampPasses(planPasses)
  if (allAssessmentsSelected) {
    return assessmentTargets.reduce((sum, item) => {
      const lectures = sortLecturesForPlan(courseDetailsById[item.course_id]?.lectures || [])
      if (!lectures.length) return sum
      const range = targetRangeFor(targetRanges, item.targetKey, item.course_id, lectures.length)
      return sum + Math.max(0, range.end - range.start + 1) * 60 * passes
    }, 0)
  }
  if (!scopeCourseId) return 0
  return Math.max(0, lectureEnd - lectureStart + 1) * 60 * passes
}

function deadlineAssessment(assessments, courseId) {
  const courseAssessments = assessments
    .filter(item => item.course_id === courseId)
    .sort((a, b) => a.date.localeCompare(b.date))
  return courseAssessments.find(item => {
    const title = item.title.toLowerCase()
    return item.type === 'exam' || title.includes('final') || title.includes('midterm') || title.includes('exam')
  }) || courseAssessments[0]
}

function isUsableAssessment(item) {
  return item.title?.trim().length >= 3
}

function shouldSyncCourseDate(item = {}) {
  return ['Final', 'Midterm', 'Exam'].includes(assessmentKind(item))
}

function savedDays() {
  const saved = Number(localStorage.getItem('studypace.calendar.customDays'))
  return clampDays(Number.isFinite(saved) && saved > 0 ? saved : 21)
}

function savedPlanCourseId() {
  return localStorage.getItem('studypace.calendar.planCourseId') || ''
}

function savedPlanAssessmentType() {
  const saved = localStorage.getItem('studypace.calendar.assessmentType')
  return PLAN_TYPES.some(option => option.value === saved) ? saved : 'midterm'
}

function savedLectureRange() {
  try {
    const saved = JSON.parse(localStorage.getItem('studypace.calendar.lectureRange') || '{}')
    const savedCourseId = optionalNumber(saved.courseId)
    const planCourseId = optionalNumber(localStorage.getItem('studypace.calendar.planCourseId'))
    const keepSavedRange = saved.version === 2 && (!planCourseId || savedCourseId === planCourseId)
    return {
      start: keepSavedRange ? Number(saved.start) || 1 : 1,
      end: keepSavedRange ? Number(saved.end) || FULL_LECTURE_RANGE : FULL_LECTURE_RANGE,
    }
  } catch {
    return { start: 1, end: FULL_LECTURE_RANGE }
  }
}

function savedTargetRanges() {
  try {
    const saved = JSON.parse(localStorage.getItem('studypace.calendar.targetRanges') || '{}')
    return saved && typeof saved === 'object' ? saved : {}
  } catch {
    return {}
  }
}

function savedTargetTypes() {
  try {
    const saved = JSON.parse(localStorage.getItem('studypace.calendar.targetTypes') || '{}')
    return saved && typeof saved === 'object' ? saved : {}
  } catch {
    return {}
  }
}

function savedPlanPasses() {
  return clampPasses(localStorage.getItem('studypace.calendar.passes') || 1)
}

function savedLectureRangeCourseId() {
  try {
    const saved = JSON.parse(localStorage.getItem('studypace.calendar.lectureRange') || '{}')
    return saved.version === 2 ? optionalNumber(saved.courseId) : null
  } catch {
    return null
  }
}

function clampDays(value, max = 180) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return 1
  return Math.max(1, Math.min(max, Math.round(parsed)))
}

function clampPasses(value) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return 1
  return Math.max(1, Math.min(3, Math.round(parsed)))
}

function optionalNumber(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null
}

function clampLecture(value, max) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return 1
  return Math.max(1, Math.min(max, Math.round(parsed)))
}

function normalizeLectureRange(range, max) {
  const start = clampLecture(range.start, max)
  const end = clampLecture(range.end, max)
  return { start, end: Math.max(start, end) }
}

function targetRangeFor(ranges, targetKey, courseId, max) {
  const saved = ranges[targetKey] || ranges[`course:${courseId}`] || { start: 1, end: FULL_LECTURE_RANGE }
  return normalizeLectureRange(saved, max)
}

function sortLecturesForPlan(lectures) {
  return [...lectures].sort((a, b) => lectureSortNumber(a) - lectureSortNumber(b) || a.id - b.id)
}

function lectureSortNumber(lecture) {
  const text = `${lecture.source_filename || ''} ${lecture.title || ''}`.toLowerCase()
  const lectureMatch = text.match(/\blecture\s*(\d{1,2})\b/)
  if (lectureMatch) return Number(lectureMatch[1])
  const chapterPartMatch = text.match(/\bchapter\s*(\d{1,2})\s*,?\s*part\s*(\d{1,2})\b/)
  if (chapterPartMatch) return Number(chapterPartMatch[1]) * 10 + Number(chapterPartMatch[2])
  const chapterMatch = text.match(/\bch\s*(\d{1,2})(?:\.(\d{1,2}))?\b/)
  if (chapterMatch) return Number(chapterMatch[1]) * 10 + Number(chapterMatch[2] || 0)
  return 9999
}

function lectureRangeLabel(lectures, start, end) {
  if (!lectures.length) return 'No slide decks yet.'
  if (start === 1 && end === lectures.length) return `All ${lectures.length} slide decks included.`
  return `Slide decks ${start} to ${end} included.`
}

function targetTitle(titleDrafts = {}, item = {}) {
  const value = titleDrafts[item.targetKey] ?? item.title ?? titleForPlanType(assessmentTypeValue(item))
  return cleanTargetTitle(value, assessmentTypeValue(item))
}

function cleanTargetTitle(value = '', planType = 'final') {
  const cleaned = String(value || '').replace(/\s+/g, ' ').trim()
  return cleaned || titleForPlanType(planType)
}

function assessmentTypeValue(item = {}) {
  const obviousType = obviousAssessmentType(item)
  if (obviousType) return obviousType
  if (planTypeOption(item.planType)) return item.planType
  const text = `${item.type || ''} ${item.title || ''}`.toLowerCase()
  if (text.includes('final')) return 'final'
  if (text.includes('midterm') || text.includes('mid-term') || text.includes('semester')) return 'midterm'
  if (text.includes('quiz')) return 'quiz'
  if (text.includes('assignment') || text.includes('project')) return 'assignment'
  return PLAN_TYPES.some(option => option.value === item.type) ? item.type : 'final'
}

function obviousAssessmentType(item = {}) {
  const title = String(item.title || '').toLowerCase()
  if (title.includes('final')) return 'final'
  if (title.includes('midterm') || title.includes('mid-term') || title.includes('semester')) return 'midterm'
  if (title.includes('quiz')) return 'quiz'
  if (title.includes('assignment') || title.includes('project')) return 'assignment'
  return ''
}

function planTypeOption(value) {
  return PLAN_TYPES.find(option => option.value === value)
}

function titleForPlanType(value) {
  const option = planTypeOption(value)
  if (!option) return 'Assessment'
  if (value === 'final') return 'Final Exam'
  return option.label
}

function storageTypeForPlanType(value, fallback = 'exam') {
  if (value === 'quiz') return 'quiz'
  if (value === 'assignment') return 'assignment'
  if (value === 'final' || value === 'midterm' || value === 'exam') return 'exam'
  return fallback === 'quiz' || fallback === 'assignment' || fallback === 'exam' ? fallback : 'exam'
}

function isGenericTargetTitle(title = '') {
  return /^(final exam|final examination|midterm|midterm exam|mid-term exam|quiz|exam|assessment|assignment|project\/assignment deadline)$/i.test(title.trim())
}

function daysBetween(start, end) {
  const a = new Date(`${start}T00:00:00`)
  const b = new Date(`${end}T00:00:00`)
  return Math.round((b - a) / 86400000)
}

function localISODate() {
  const now = new Date()
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000)
  return local.toISOString().slice(0, 10)
}

function formatCompactDate(value) {
  if (!value) return 'No date'
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(new Date(`${value}T00:00:00`))
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
