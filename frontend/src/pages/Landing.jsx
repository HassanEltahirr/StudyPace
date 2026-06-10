import { Link } from 'react-router-dom'
import Brand from '../components/Brand'
import { getToken } from '../api'

const outcomes = [
  {
    pain: 'Guessing what to study',
    title: 'The next deck is already queued',
    text: 'StudyPace turns every course date into one route, so the day starts with the slide deck you should open next.',
  },
  {
    pain: 'Rebuilding plans by hand',
    title: 'Dates and slides stay connected',
    text: 'Change an assessment, remove a deck, or fall behind, and the timetable can absorb the shift without a spreadsheet.',
  },
  {
    pain: 'Cramming every course alone',
    title: 'One calm route across the semester',
    text: 'Quizzes, midterms, finals, and assignments sit together instead of becoming separate plans fighting for attention.',
  },
]

export default function Landing() {
  const hasToken = Boolean(getToken())

  return (
    <div className="landing-page">
      <header className="landing-header">
        <Brand />
        <nav className="landing-actions" aria-label="Landing navigation">
          {hasToken ? (
            <Link className="btn-primary" to="/today">Open app</Link>
          ) : (
            <>
              <Link className="btn-ghost" to="/login">Sign in</Link>
              <Link className="btn-primary" to="/login?mode=register">Create account</Link>
            </>
          )}
        </nav>
      </header>

      <section className="landing-hero">
        <div className="landing-copy">
          <p className="eyebrow">StudyPace</p>
          <h1>The study planner that keeps working even when you fall behind.</h1>
          <p className="landing-lede">
            Upload your course slides, add your assessment dates, and StudyPace turns the semester into a clear daily route.
          </p>
          <div className="landing-social-proof">
            Open beta for student testers. Built around real course slides and assessment schedules.
          </div>
          <div className="landing-cta-row">
            <Link className="btn-primary" to={hasToken ? '/today' : '/login?mode=register'}>
              {hasToken ? 'Go to today' : 'Start your plan'}
            </Link>
            <Link className="landing-secondary-link" to={hasToken ? '/courses' : '/login'}>
              {hasToken ? 'Manage courses' : 'I already have an account'}
            </Link>
          </div>
        </div>

        <StudyPlanPreview />
      </section>

      <section className="landing-section" aria-label="What StudyPace offers">
        <div className="landing-section-head">
          <p className="eyebrow">Why it exists</p>
          <h2>It replaces the part of studying students avoid.</h2>
          <p className="landing-section-copy">
            StudyPace is for the moment when every course has slides, every date is moving closer, and you just need to know what to open next.
          </p>
        </div>
        <div className="landing-outcomes">
          {outcomes.map(outcome => (
            <article key={outcome.title} className="landing-outcome">
              <span>{outcome.pain}</span>
              <div>
                <h3>{outcome.title}</h3>
                <p>{outcome.text}</p>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="landing-trust-strip" aria-label="StudyPace privacy and focus">
        {[
          'Built around your slides, not generic advice.',
          'Designed for re-entry when real student life interrupts the plan.',
          'Private accounts keep each student workspace separate.',
        ].map(item => (
          <p key={item}>{item}</p>
        ))}
      </section>
    </div>
  )
}

function StudyPlanPreview() {
  return (
    <div className="landing-preview-frame" aria-label="StudyPace timetable preview">
      <div className="landing-frame-bar">
        <div className="landing-frame-dots" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <strong>Today in StudyPace</strong>
      </div>

      <div className="landing-preview">
        <div className="landing-preview-top">
          <div>
            <p className="eyebrow">Today</p>
            <h2>Monday 8 Jun</h2>
          </div>
          <span>3 decks</span>
        </div>

        <div className="landing-preview-list">
          <PreviewItem title="Analysis of Algorithms III" meta="60 min · Quiz prep · Course 1" state="next" />
          <PreviewItem title="Complexity" meta="45 min · Midterm prep · Course 2" />
          <PreviewItem title="Lists and Iterators" meta="45 min · Final prep · Course 3" />
        </div>

        <div className="landing-preview-note">
          Miss a day and the route adjusts. No guilt, no starting over.
        </div>
      </div>
    </div>
  )
}

function PreviewItem({ title, meta, state }) {
  return (
    <div className={state === 'next' ? 'landing-preview-item is-next' : 'landing-preview-item'}>
      <div>
        <strong>{title}</strong>
        <span>{meta}</span>
      </div>
      <small>{state === 'next' ? 'Start' : 'Queued'}</small>
    </div>
  )
}
