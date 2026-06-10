import { useEffect, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { anonymizeCourseTitle, courseDisplayName, setCourseDisplayNameOverride } from '../courseLabels'

const COLORS = ['#c87941', '#9a6a3f', '#b8864b', '#8f6244', '#d69a5f', '#7c5f46']

// Mirrors the backend's ALLOWED_UPLOAD_EXTENSIONS and MAX_UPLOAD_MB so bad
// files are rejected before the slow base64 round trip instead of after it.
const UPLOAD_EXTENSIONS = ['.pdf', '.ppt', '.pptx', '.docx', '.txt', '.md']
const UPLOAD_ACCEPT = UPLOAD_EXTENSIONS.join(',')
const MAX_UPLOAD_MB = 50
const UPLOAD_CONCURRENCY = 3

export default function Courses() {
  const { courseId } = useParams()
  const [searchParams] = useSearchParams()
  const wantsPlan = searchParams.get('next') === 'plan'
  const [courses, setCourses] = useState([])
  const [selectedId, setSelectedId] = useState(courseId || '')
  const [detail, setDetail] = useState(null)
  const [coursesLoading, setCoursesLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadLabel, setUploadLabel] = useState('')
  const [error, setError] = useState('')
  const [newCourse, setNewCourse] = useState({ name: '' })
  const [renamingCourseId, setRenamingCourseId] = useState('')
  const [removingLectureId, setRemovingLectureId] = useState('')

  useEffect(() => {
    loadCourses()
  }, [])

  useEffect(() => {
    if (courseId) setSelectedId(courseId)
  }, [courseId])

  useEffect(() => {
    if (!selectedId) return
    let cancelled = false
    setDetailLoading(true)
    setError('')
    setDetail(null)
    api.getCourseDetail(selectedId)
      .then(data => {
        if (!cancelled) setDetail(data)
      })
      .catch(e => {
        if (!cancelled) setError(e.message)
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [selectedId])

  async function loadCourses() {
    setCoursesLoading(true)
    try {
      const loaded = await api.getCourses()
      setCourses(loaded)
      if (!selectedId && loaded[0]) setSelectedId(String(loaded[0].id))
    } catch (e) {
      setError(e.message)
    } finally {
      setCoursesLoading(false)
    }
  }

  async function addCourse() {
    setError('')
    try {
      const created = await api.createCourse({
        name: newCourse.name.trim(),
        description: '',
        exam_date: null,
        total_hours: 40,
        color: COLORS[courses.length % COLORS.length],
      })
      setCourses(prev => [...prev, created])
      setSelectedId(String(created.id))
      setNewCourse({ name: '' })
    } catch (e) {
      setError(e.message)
    }
  }

  async function upload(files) {
    const selectedFiles = Array.from(files || []).filter(Boolean)
    if (!selectedFiles.length || !selectedId || uploading) return

    const problems = []
    const accepted = []
    for (const file of selectedFiles) {
      const problem = uploadProblem(file)
      if (problem) problems.push(`${file.name} — ${problem}`)
      else accepted.push(file)
    }

    if (!accepted.length) {
      setError(problems.join(' · '))
      return
    }

    setUploading(true)
    setError('')
    const total = accepted.length
    let done = 0
    setUploadLabel(total > 1 ? `Uploading 0 of ${total}` : 'Extracting slides')
    const queue = [...accepted]
    const worker = async () => {
      while (queue.length) {
        const file = queue.shift()
        try {
          const content_base64 = await fileToBase64(file)
          await api.uploadLecture(selectedId, { filename: file.name, content_base64 })
        } catch (e) {
          problems.push(`${file.name} — ${e.message}`)
        }
        done += 1
        setUploadLabel(total > 1 ? `Uploading ${done} of ${total}` : 'Extracting slides')
      }
    }
    try {
      await Promise.all(Array.from({ length: Math.min(UPLOAD_CONCURRENCY, queue.length) }, worker))
      setDetail(await api.getCourseDetail(selectedId))
    } catch (e) {
      problems.push(e.message)
    } finally {
      setUploading(false)
      setUploadLabel('')
      if (problems.length) setError(problems.join(' · '))
    }
  }

  async function renameCourse(courseId, name) {
    const course = courses.find(item => String(item.id) === String(courseId))
    const nextName = name.trim()
    if (!course || nextName.length < 2) return null
    setError('')
    const optimistic = { ...course, name: nextName }
    setCourseDisplayNameOverride(course.id, nextName)
    setCourses(prev => prev.map(item => item.id === course.id ? optimistic : item))
    setDetail(prev => prev?.course?.id === course.id ? { ...prev, course: optimistic } : prev)

    const updated = await api.updateCourse(course.id, {
      name: nextName,
      description: course.description || '',
      total_hours: course.total_hours || 40,
      exam_date: course.exam_date || null,
      color: course.color || '#007aff',
    })
    setCourseDisplayNameOverride(updated.id, nextName)
    setCourses(prev => prev.map(item => item.id === updated.id ? updated : item))
    setDetail(prev => prev?.course?.id === updated.id ? { ...prev, course: updated } : prev)
    return updated
  }

  async function deleteCourse(course) {
    if (!course) return
    const name = courseDisplayName(courses, course)
    if (!window.confirm(`Delete "${name}" and all its slides? This cannot be undone.`)) return
    setError('')
    try {
      await api.deleteCourse(course.id)
      setCourses(prev => prev.filter(item => item.id !== course.id))
      const remaining = courses.filter(item => item.id !== course.id)
      setSelectedId(remaining[0] ? String(remaining[0].id) : '')
      setDetail(null)
    } catch (e) {
      setError(e.message)
    }
  }

  async function removeLecture(lecture) {
    if (!lecture || removingLectureId) return
    setError('')
    setRemovingLectureId(String(lecture.id))
    try {
      await api.deleteLecture(lecture.id)
      setDetail(prev => {
        if (!prev?.lectures) return prev
        return {
          ...prev,
          lectures: prev.lectures.filter(item => String(item.id) !== String(lecture.id)),
        }
      })
    } catch (e) {
      setError(e.message)
    } finally {
      setRemovingLectureId('')
    }
  }

  const selectedCourse = useMemo(
    () => courses.find(c => String(c.id) === String(selectedId)),
    [courses, selectedId]
  )

  const detailReady = Boolean(detail && String(detail.course?.id) === String(selectedId))
  const lectureCount = detailReady ? detail?.lectures?.length || 0 : 0
  const orderedLectures = useMemo(() => sortLecturesForPlan(detailReady ? detail.lectures || [] : []), [detailReady, detail])
  const dropdownLectures = orderedLectures
  const hasLocalLibrary = detailReady && lectureCount > 0
  const planHref = selectedCourse ? `/calendar?course_id=${selectedCourse.id}` : '/calendar'
  const libraryStatus = detailLoading && !detailReady
    ? 'Checking local material...'
    : hasLocalLibrary
      ? 'Open the deck list to inspect or remove slides.'
      : 'Add slides to start studying.'
  const slideStatus = detailLoading && !detailReady
    ? 'Checking slide decks...'
    : lectureCount > 0
    ? `${lectureCount} slide deck${lectureCount === 1 ? '' : 's'} ready`
    : 'No slides added yet'

  return (
    <div className="page grid gap-4 lg:grid-cols-[260px_1fr]">
      <aside className="space-y-4">
        <section className="surface p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h1 className="screen-title text-2xl">Courses</h1>
              <p className="mt-1 text-sm font-medium text-[var(--text-muted)]">
                {coursesLoading && courses.length === 0 ? 'Checking courses...' : `${courses.length} active courses`}
              </p>
            </div>
          </div>
          <div className="mt-4 space-y-2">
            {courses.map(course => {
              const active = String(selectedId) === String(course.id)
              return (
                <div
                  key={course.id}
                  className={`rounded-lg border p-3 transition ${
                    active ? 'border-[var(--accent)]' : 'border-[var(--border)] bg-[var(--surface-raised)] hover:bg-[var(--surface-hover)]'
                  }`}
                  style={active ? { background: 'color-mix(in srgb, var(--accent) 7%, var(--surface-raised))' } : undefined}
                >
                  <button
                    type="button"
                    className="flex w-full min-w-0 items-center gap-3 text-left"
                    onClick={() => setSelectedId(String(course.id))}
                  >
                    <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: course.color }} />
                    <span className="min-w-0 flex-1 truncate text-sm font-semibold text-[var(--text)]">{courseDisplayName(courses, course)}</span>
                  </button>
                  {active && (
                    <div className="mt-2 flex items-center gap-1 pl-5">
                      <button
                        type="button"
                        className="shrink-0 rounded-md px-2 py-1 text-xs font-semibold text-[var(--accent)] transition hover:bg-[var(--surface-hover)]"
                        onClick={() => setRenamingCourseId(String(course.id))}
                      >
                        Rename
                      </button>
                      <button
                        type="button"
                        className="shrink-0 rounded-md px-2 py-1 text-xs font-semibold text-[var(--danger)] transition hover:bg-[var(--surface-hover)]"
                        onClick={() => deleteCourse(course)}
                      >
                        Delete
                      </button>
                    </div>
                  )}
                  {active && (
                    <CourseNameEditor
                      course={course}
                      displayName={courseDisplayName(courses, course)}
                      onRename={name => renameCourse(course.id, name)}
                      compact
                      initiallyOpen={renamingCourseId === String(course.id)}
                      hideClosedButton
                      onDone={() => setRenamingCourseId('')}
                    />
                  )}
                </div>
              )
            })}
          </div>
        </section>

        <details className="surface p-4">
          <summary className="cursor-pointer text-sm font-semibold text-[var(--text-muted)] marker:text-[var(--text-faint)]">Add course</summary>
          <div className="mt-3 space-y-2">
            <input className="input" placeholder="Course name" value={newCourse.name} onChange={e => setNewCourse(v => ({ ...v, name: e.target.value }))} />
            <button className="btn-primary w-full" disabled={!newCourse.name.trim()} onClick={addCourse}>Create course</button>
          </div>
        </details>
      </aside>

      <main className="space-y-4">
        {error && <div className="rounded-lg border border-rose-400/30 bg-rose-500/10 p-3 text-sm font-semibold text-rose-200">{error}</div>}

        {wantsPlan && (
          <section className="surface p-4">
            <p className="eyebrow text-[var(--accent)]">Plan setup</p>
            <div className="mt-1 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-sm font-medium text-[var(--text-muted)]">
                Add slides for a course first. Then StudyPace can build the plan.
              </p>
              {hasLocalLibrary && (
                <Link className="btn-primary self-start px-4 py-2 text-sm sm:self-auto" to={planHref}>
                  Continue to Plan
                </Link>
              )}
            </div>
          </section>
        )}

        {selectedCourse && (
          <section className="surface p-5">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0">
                <p className="eyebrow text-[var(--accent)]">Course</p>
                <h2 className="mt-1 truncate text-2xl font-semibold">{courseDisplayName(courses, selectedCourse)}</h2>
                <p className="mt-1 text-sm font-medium text-[var(--text-muted)]">
                  {slideStatus}
                </p>
              </div>
              {!detailLoading && !lectureCount && (
                <span className="badge self-start text-[var(--accent)]">
                  Slides first
                </span>
              )}
            </div>

            <div className="mt-5">
              <div className="surface-soft p-4">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div>
                    <p className="eyebrow text-[var(--accent)]">Slides</p>
                    <h3 className="mt-1 section-title">Slide library</h3>
                  </div>
                </div>
                <UploadButton
                  label={uploading ? uploadLabel || 'Extracting slides' : lectureCount ? 'Upload more slides' : 'Add slides'}
                  busy={uploading}
                  icon="upload"
                  accept={UPLOAD_ACCEPT}
                  onFiles={upload}
                  multiple
                  variant={lectureCount ? 'default' : 'primary'}
                  className={lectureCount ? 'min-h-10 w-full px-4 py-2 text-sm sm:w-auto' : 'min-h-16 w-full px-5 py-3 text-sm'}
                  iconClassName={lectureCount ? 'h-4 w-4' : 'h-6 w-6'}
                />
                <p className="mt-3 text-xs font-medium text-[var(--text-faint)]">
                  {uploading ? libraryStatus : `${libraryStatus} Drag files here or click to browse — PDF, PPT, PPTX, DOCX, TXT, MD up to ${MAX_UPLOAD_MB} MB.`}
                </p>
                {detailLoading && !detailReady && <CourseMaterialLoading />}
                {orderedLectures.length > 0 && (
                  <SlideDeckDropdown
                    title="All slide decks"
                    count={`${orderedLectures.length} loaded`}
                    lectures={dropdownLectures}
                    courses={courses}
                    selectedCourse={selectedCourse}
                    emptyMessage="No slide decks loaded yet."
                    onRemove={removeLecture}
                    removingLectureId={removingLectureId}
                  />
                )}
              </div>
            </div>
          </section>
        )}
      </main>
    </div>
  )
}

function CourseMaterialLoading() {
  return (
    <div className="mt-3 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-3" aria-live="polite" aria-busy="true">
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--text-faint)]">Checking slide library</span>
        <span className="text-xs font-semibold text-[var(--text-faint)]">One moment</span>
      </div>
      <div className="mt-3 space-y-2">
        <div className="h-3 w-2/3 rounded bg-[var(--surface-raised)]" />
        <div className="h-3 w-1/2 rounded bg-[var(--surface-raised)]" />
      </div>
    </div>
  )
}

function CourseNameEditor({ course, displayName, onRename, compact = false, initiallyOpen = false, hideClosedButton = false, onDone }) {
  const [open, setOpen] = useState(initiallyOpen)
  const [draft, setDraft] = useState(displayName)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    setDraft(displayName)
    setSaved(false)
    setError('')
  }, [course.id, displayName])

  useEffect(() => {
    if (initiallyOpen) setOpen(true)
  }, [course.id, initiallyOpen])

  async function save() {
    const nextName = draft.trim()
    if (nextName.length < 2 || saving) return
    setSaving(true)
    setError('')
    try {
      const renamePromise = onRename(nextName)
      setSaved(true)
      setOpen(false)
      await renamePromise
      onDone?.()
    } catch (err) {
      setOpen(true)
      setError(err.message || 'Could not rename this course.')
    } finally {
      setSaving(false)
    }
  }

  if (!open) {
    if (hideClosedButton) return null
    return (
      <div className={`${compact ? 'mt-2 pl-5' : 'mt-3'} flex flex-wrap items-center gap-2`}>
        <button type="button" className="btn-ghost min-h-8 px-3 py-1.5 text-xs" onClick={() => setOpen(true)}>
          Rename course
        </button>
        {saved && <span className="text-xs font-semibold text-[var(--accent)]">Saved</span>}
      </div>
    )
  }

  return (
    <div className={`surface-soft ${compact ? 'mt-2 p-2' : 'mt-3 p-3'}`}>
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
              save()
            }
            if (event.key === 'Escape') setOpen(false)
          }}
        />
      </label>
      {error && <p className="mt-2 text-xs font-semibold text-rose-300">{error}</p>}
      <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" className={`btn-primary ${compact ? 'min-h-8 px-3 py-1.5 text-xs' : 'px-4 py-2 text-sm'}`} disabled={saving || draft.trim().length < 2} onClick={save}>
          {saving ? 'Saving...' : 'Save name'}
        </button>
        <button type="button" className={`btn-ghost ${compact ? 'min-h-8 px-3 py-1.5 text-xs' : 'px-4 py-2 text-sm'}`} onClick={() => {
          setOpen(false)
          onDone?.()
        }}>
          Cancel
        </button>
      </div>
    </div>
  )
}

function SlideDeckDropdown({ title, count, lectures, courses, selectedCourse, emptyMessage, onRemove, removingLectureId }) {
  return (
    <details className="mt-3 rounded-lg border border-[var(--border)] bg-[var(--surface)]">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2 text-xs font-semibold text-[var(--text-muted)] marker:hidden">
        <span>{title}</span>
        <span className="text-[var(--text-faint)]">{count}</span>
      </summary>
      <div className="max-h-72 overflow-auto border-t border-[var(--border)] p-1">
        {lectures.length ? (
          lectures.map(lecture => {
            const titleText = anonymizeCourseTitle(cleanStudyTitle(lecture.title || lecture.source_filename), courses, selectedCourse)
            const removing = String(removingLectureId || '') === String(lecture.id)
            return (
              <div
                key={lecture.id}
                className="group flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium text-[var(--text-muted)] transition hover:bg-[var(--surface-hover)]"
              >
                <Link
                  to={`/lesson/${lecture.id}`}
                  className="min-w-0 flex-1 truncate text-[var(--text)] hover:text-[var(--accent)]"
                  aria-label={`Open ${titleText} slides`}
                >
                  {titleText}
                </Link>
                <span className="shrink-0 text-xs text-[var(--text-faint)]">{lecture.slide_count} slides</span>
                <Link
                  to={`/lesson/${lecture.id}`}
                  className="shrink-0 rounded-md border border-[var(--border)] bg-[var(--surface-raised)] px-2.5 py-1 text-xs font-semibold text-[var(--accent)] transition hover:border-[var(--accent)] hover:bg-[var(--surface-hover)]"
                >
                  Open
                </Link>
                {onRemove && (
                  <button
                    type="button"
                    className="shrink-0 rounded-md px-2.5 py-1 text-xs font-semibold text-[var(--text-faint)] transition hover:bg-rose-500/10 hover:text-rose-300 disabled:cursor-not-allowed disabled:opacity-50"
                    disabled={removing}
                    onClick={() => onRemove(lecture)}
                    aria-label={`Remove ${titleText}`}
                  >
                    {removing ? 'Removing...' : 'Remove'}
                  </button>
                )}
              </div>
            )
          })
        ) : (
          <p className="px-3 py-3 text-sm font-medium text-[var(--text-faint)]">{emptyMessage}</p>
        )}
      </div>
    </details>
  )
}

function UploadButton({ label, busy, icon, accept, onFile, onFiles, multiple = false, variant = 'default', className = '', iconClassName = 'h-6 w-6' }) {
  const [dragOver, setDragOver] = useState(false)
  const tone = variant === 'primary'
    ? 'border-[var(--cta)] bg-[var(--cta)] text-[var(--cta-text)] hover:bg-[var(--cta-strong)]'
    : 'border-[var(--border)] bg-[var(--surface-raised)] text-[var(--text-muted)] hover:border-[var(--border-strong)] hover:bg-[var(--surface-hover)]'

  function deliver(files) {
    if (!files.length) return
    if (multiple) onFiles?.(files)
    else onFile?.(files[0])
  }

  function handleChange(event) {
    deliver(Array.from(event.target.files || []))
    event.target.value = ''
  }

  function handleDrop(event) {
    event.preventDefault()
    setDragOver(false)
    if (busy) return
    deliver(Array.from(event.dataTransfer?.files || []))
  }

  return (
    <label
      className={`grid min-w-[86px] cursor-pointer place-items-center rounded-lg border px-3 py-2 text-xs font-semibold transition ${tone} ${busy ? 'opacity-60' : ''} ${dragOver ? 'border-dashed border-[var(--accent)]' : ''} ${className}`}
      style={dragOver ? { background: 'color-mix(in srgb, var(--accent) 8%, var(--surface-raised))' } : undefined}
      onDragOver={event => {
        event.preventDefault()
        if (!busy) setDragOver(true)
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
    >
      <Icon name={icon} className={iconClassName} />
      <span className="mt-1">{dragOver ? 'Drop slides to upload' : label}</span>
      <input
        type="file"
        className="hidden"
        accept={accept}
        multiple={multiple}
        disabled={busy}
        onChange={handleChange}
      />
    </label>
  )
}

function Icon({ name, className = 'h-6 w-6' }) {
  const common = {
    'aria-hidden': 'true',
    viewBox: '0 0 24 24',
    className,
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: '2.2',
    strokeLinecap: 'round',
    strokeLinejoin: 'round',
  }

  if (name === 'file') {
    return (
      <svg {...common}>
        <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Z" />
        <path d="M14 3v6h6" />
      </svg>
    )
  }
  if (name === 'calendar') {
    return (
      <svg {...common}>
        <path d="M8 2v4M16 2v4" />
        <rect x="3" y="5" width="18" height="16" rx="3" />
        <path d="M3 10h18" />
      </svg>
    )
  }
  if (name === 'check') {
    return (
      <svg {...common}>
        <path d="m5 13 4 4L19 7" />
      </svg>
    )
  }
  if (name === 'plan') {
    return (
      <svg {...common}>
        <path d="M5 20V10" />
        <path d="M12 20V4" />
        <path d="M19 20v-7" />
      </svg>
    )
  }
  if (name === 'grade') {
    return (
      <svg {...common}>
        <path d="M4 19V5a2 2 0 0 1 2-2h12v18H6a2 2 0 0 1-2-2Z" />
        <path d="M8 7h6M8 11h8M8 15h5" />
      </svg>
    )
  }
  return (
    <svg {...common}>
      <path d="M12 16V4" />
      <path d="m7 9 5-5 5 5" />
      <path d="M5 20h14" />
    </svg>
  )
}

function uploadProblem(file) {
  const extension = `.${String(file.name || '').split('.').pop().toLowerCase()}`
  if (!UPLOAD_EXTENSIONS.includes(extension)) {
    return `unsupported type, use ${UPLOAD_EXTENSIONS.join(', ')}`
  }
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
    return `larger than ${MAX_UPLOAD_MB} MB`
  }
  return ''
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(new Error('Could not read the selected file.'))
    reader.readAsDataURL(file)
  })
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
