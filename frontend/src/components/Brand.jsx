export function LogoMark({ size = 'sm', className = '' }) {
  const sizeClass = size === 'lg' ? 'h-11 w-11 rounded-2xl' : 'h-6 w-6 rounded-lg'

  return (
    <span
      className={`grid shrink-0 place-items-center border text-[var(--accent-text)] ${sizeClass} ${className}`}
      style={{
        borderColor: 'color-mix(in srgb, var(--accent) 70%, transparent)',
        background: 'var(--accent)',
        boxShadow: 'inset 0 -1px 0 rgba(0, 0, 0, 0.18)',
      }}
      aria-hidden="true"
    >
      <svg className={size === 'lg' ? 'h-7 w-7' : 'h-4 w-4'} viewBox="0 0 24 24" fill="none">
        <path
          d="M4.75 6.4h4.3c1.35 0 2.35.42 2.95 1.25.6-.83 1.6-1.25 2.95-1.25h4.3v10.85h-4.05c-1.45 0-2.5.38-3.2 1.15-.7-.77-1.75-1.15-3.2-1.15H4.75Z"
          fill="currentColor"
          opacity="0.18"
        />
        <path
          d="M4.75 6.4h4.3c1.35 0 2.35.42 2.95 1.25.6-.83 1.6-1.25 2.95-1.25h4.3v10.85h-4.05c-1.45 0-2.5.38-3.2 1.15-.7-.77-1.75-1.15-3.2-1.15H4.75Z"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinejoin="round"
        />
        <path d="M12 7.65v10.55" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        <path d="M8.1 10.8h2.1M14 10.8h2.1" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        <path d="M9 14.05c.65.65 1.55.95 3 .95s2.35-.3 3-.95" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
    </span>
  )
}

export default function Brand({ size = 'sm', className = '' }) {
  const textClass = size === 'lg' ? 'text-2xl font-semibold' : 'text-[0.95rem] font-semibold'

  return (
    <span className={`inline-flex items-center gap-2 text-[var(--text)] ${className}`}>
      <LogoMark size={size === 'lg' ? 'lg' : 'sm'} />
      <span className={textClass}>StudyPace</span>
    </span>
  )
}
