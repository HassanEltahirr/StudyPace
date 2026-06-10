const MINDS = [
  {
    name: 'Al-Khwarizmi',
    role: 'Algebra and algorithms',
    variant: 'turban',
    quote: 'Turn the unknown into steps.',
  },
  {
    name: 'Hypatia',
    role: 'Mathematics and teaching',
    variant: 'classical',
    quote: 'Clarity is built one return at a time.',
  },
  {
    name: 'Ibn Sina',
    role: 'Medicine and philosophy',
    variant: 'turban-soft',
    quote: 'Study until the idea can stand on its own.',
  },
  {
    name: 'Ada Lovelace',
    role: 'Computing imagination',
    variant: 'victorian',
    quote: 'See the pattern before the answer.',
  },
]

export default function GreatMindsRail() {
  const leftMind = MINDS[0]
  const rightMind = MINDS[3]

  return (
    <>
      <GreatMindSide side="left" mind={leftMind} supporting={MINDS[1]} />
      <GreatMindSide side="right" mind={rightMind} supporting={MINDS[2]} />
    </>
  )
}

function GreatMindSide({ side, mind, supporting }) {
  return (
    <aside className={`great-minds-side great-minds-side-${side}`} aria-hidden="true">
      <p className="great-minds-label">Great minds</p>
      <div className="great-minds-feature">
        <ScholarPortrait variant={mind.variant} size="lg" />
        <blockquote className="great-minds-quote">{mind.quote}</blockquote>
        <div>
          <div className="great-mind-name">{mind.name}</div>
          <div className="great-mind-role">{mind.role}</div>
        </div>
      </div>

      <div className="great-mind-secondary">
        <ScholarPortrait variant={supporting.variant} />
        <div className="min-w-0">
          <div className="great-mind-name">{supporting.name}</div>
          <div className="great-mind-role">{supporting.role}</div>
        </div>
      </div>
    </aside>
  )
}

function ScholarPortrait({ variant, size = 'sm' }) {
  const isClassical = variant === 'classical'
  const isVictorian = variant === 'victorian'
  const isSoft = variant === 'turban-soft'

  return (
    <svg className={`great-mind-portrait great-mind-portrait-${size}`} viewBox="0 0 96 96" fill="none">
      <path
        d="M18 71c8-12 17-18 30-18s22 6 30 18c-10 8-20 12-30 12s-20-4-30-12Z"
        fill="currentColor"
        opacity="0.18"
      />
      <path
        d="M30 44c1 15 8 25 18 25s17-10 18-25"
        stroke="currentColor"
        strokeWidth="3.2"
        strokeLinecap="round"
      />
      <path
        d="M34 39c2-11 8-17 14-17s12 6 14 17"
        stroke="currentColor"
        strokeWidth="3.2"
        strokeLinecap="round"
      />
      {isVictorian ? (
        <>
          <path d="M25 31c9-15 38-15 47 0-2 8-7 14-10 18-7-7-17-9-29-3-3-4-6-9-8-15Z" fill="currentColor" opacity="0.16" />
          <path d="M26 32c10-14 36-14 46 0M29 39c10-8 28-8 38 0" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" />
          <path d="M35 68c4 8 22 8 26 0" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round" />
        </>
      ) : isClassical ? (
        <>
          <path d="M24 37c9-20 39-20 48 0-4 2-8 4-12 5-10-5-20-5-30 0-2-1-4-3-6-5Z" fill="currentColor" opacity="0.16" />
          <path d="M24 37c9-20 39-20 48 0M31 34c9-6 25-6 34 0M27 45c5-3 9-4 14-5" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" />
          <path d="M35 66c4 5 20 5 26 0" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round" />
        </>
      ) : (
        <>
          <path
            d={isSoft
              ? 'M20 35c7-18 38-24 56-5-3 11-12 16-21 18-8-8-18-8-27-2-3-3-5-6-8-11Z'
              : 'M19 36c6-18 36-27 57-7-2 12-12 18-22 20-8-9-19-8-28-1-3-3-5-7-7-12Z'}
            fill="currentColor"
            opacity="0.16"
          />
          <path d="M21 35c8-17 37-23 55-6M26 45c12-8 27-10 44-7M30 31c10 3 20 3 30 0" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" />
          <path d="M37 67c5 6 17 6 23 0M39 75c5 3 14 3 19 0" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round" />
        </>
      )}
      <path d="M39 47h.01M57 47h.01" stroke="currentColor" strokeWidth="4.2" strokeLinecap="round" />
      <path d="M43 57c4 2 8 2 12 0" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round" />
    </svg>
  )
}
