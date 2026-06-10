# StudyPace Design Audit

This pass optimizes StudyPace for long study sessions: low visual noise, fast scanning, reading-first pages, and fewer decisions per screen.

## Audit Findings

Screenshots captured before the redesign:

- Today: `/Users/hassaneltahir/studypace-web/design-audit-shots/before-today.png`
- Plan: `/Users/hassaneltahir/studypace-web/design-audit-shots/before-calendar.png`
- Courses: `/Users/hassaneltahir/studypace-web/design-audit-shots/before-courses.png`
- Lesson: `/Users/hassaneltahir/studypace-web/design-audit-shots/before-lesson.png`
- Progress: `/Users/hassaneltahir/studypace-web/design-audit-shots/before-progress.png`

Main issues found:

- Cognitive load: too many equally loud surfaces, badges, buttons, and explanatory labels.
- Visual clutter: nested cards, thick rounded panels, shadows, and saturated button treatments made simple tasks feel heavy.
- Spacing: many components used large radii and padding inconsistently, making dense study information harder to scan.
- Typography: excessive `font-black`, all-caps labels, and large headings competed with lecture content.
- Hierarchy: dashboard metrics and decorative states often felt more important than today's actual slides.
- Navigation: header and bottom navigation were visually heavier than needed for an 8-hour study product.
- Accessibility: some muted text was low contrast, native selects looked inconsistent, and color carried too much semantic weight.
- Eye fatigue: bright cyan/green surfaces, high-contrast badges, and thick borders created constant visual interruption.

## Design System

Typography:

- Font stack: `Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`.
- Page titles: 28-36px, 720 weight, 1.12 line height.
- Section titles: 16-20px, 700 weight, 1.3 line height.
- Reading text: 17px, 450 weight, 1.75 line height, max width 820px.
- Controls: 14-15px, 600-650 weight, no negative letter spacing.

Spacing:

- 4px base grid.
- Control height: 40px minimum.
- Compact rows: 12-16px padding.
- Primary page width: 1120px.
- Reading page width: 820px.
- Radius: 8px for controls and rows, 12px for main surfaces.

Colors:

- Background: `#0f1110`.
- Secondary background: `#131615`.
- Surface: `#181c1b`.
- Raised surface: `#1d2220`.
- Border: soft white alpha, 8-14%.
- Primary text: `#f1f3ef`.
- Muted text: `#b6bdb2`.
- Faint text: `#848d82`.
- Accent: `#7dd3a5`.

Rules:

- Use the accent only for the next action, active state, or successful completion.
- Prefer whitespace and text hierarchy before borders.
- Avoid nested cards unless a form or repeated item needs containment.
- If a control is optional, put it in a collapsed details row.

## Component Redesigns

App shell:

Mockup: `Logo | current page content | theme/logout` with a quiet bottom nav.

Reasoning: navigation should be available, not visually dominant. The header is now 48px, neutral, and dark-first.

Accessibility: icons keep labels or titles, active nav is not color-only because position and text remain visible.

Plan:

Mockup: `Edit one course` collapsed row, `Add quiz or assignment` collapsed row, plan check, then day groups with compact study rows.

Reasoning: planning should be one clear setup area plus a readable output. Timeline decoration and loud badges were removed.

Accessibility: native selects now use dark color scheme and visible focus states; form labels remain explicit.

Today:

Mockup: date, one greeting line, progress bar, next slide rows, completed rows.

Reasoning: Today is the re-entry hook. It should answer "what do I do next?" with no dashboard clutter.

Accessibility: completion controls have descriptive aria labels and compact visible states.

Courses:

Mockup: small course list, selected course header, quiet slide upload, planned lecture cards.

Reasoning: courses are a slide library, not a management dashboard. Upload stays obvious without becoming a hero block.

Accessibility: course buttons retain text labels, status text is not only color-coded.

Lesson:

Mockup: back link, lecture title, metadata, slides, small completion panel.

Reasoning: the lecture deck is the product. The UI should disappear while reading slides.

Accessibility: images keep slide-specific alt text; reading width and line height reduce fatigue.

Progress:

Mockup: overall progress summary, three small metrics, simple course rows.

Reasoning: progress supports motivation but should not distract from studying.

Accessibility: progress values are written as text, not only chart graphics.

Hidden or deferred:

- AI Tutor remains removed.
- Practice remains hidden until its purpose is clear.
- Flashcards/search/onboarding/settings should follow the same token system when they are reintroduced: one primary action, compact rows, no decorative cards.

## Quality Bar

Before adding any future UI, ask:

- Does this help the student decide what to study next?
- Can the student understand it in under two seconds?
- Does the text still matter if the color is removed?
- Would this remain comfortable after six hours?
- Can an optional control be hidden until needed?
