# Design System — AutoResearch

## Product Context
- **What this is:** An adversarial research pipeline dashboard built on OMC (OneManCompany) backbone
- **Who it's for:** ML/AI researchers who want automated, quality-first research paper generation
- **Space/industry:** AI research tools, scientific workflow automation
- **Project type:** App UI (workspace-driven, data-dense, task-focused dashboard)

## Memorable Thing
"A research lab you can trust, because it's skeptical of itself."

## Aesthetic Direction
- **Direction:** Scientific Instrument — polished, modern, premium SaaS with editorial warmth
- **Decoration level:** Intentional — subtle paper grain texture, warm-toned shadows, no decoration for decoration's sake
- **Mood:** Feels like a well-funded research lab's internal tool. Precise, warm, trustworthy. Not cold SaaS, not playful startup. Academic credibility meets modern craft.
- **Inspiration:** Ivy Collection (green-gold-cream palette), Nature journal (editorial hierarchy), Linear (polished app UI)

## Typography
- **Display/Hero:** Fraunces (serif, Google Fonts) — editorial gravitas, warm personality, distinctive serifs that feel academic without being stuffy
- **Body:** DM Sans (sans-serif, Google Fonts) — clean, readable, modern. Pairs well with Fraunces without competing
- **UI/Labels:** DM Sans (same as body)
- **Data/Tables/Code:** JetBrains Mono (monospace, Google Fonts) — optimized for code and data readability, supports tabular-nums
- **Loading:** Google Fonts CDN with preconnect
- **Scale:** 15px base
  - h1: 2.3rem (34.5px) / 700
  - h2: 1.3rem (19.5px) / 600 (meeting card titles)
  - Body: 0.85rem (12.75px)
  - Labels: 0.65rem (9.75px) uppercase mono
  - Data: 0.78rem (11.7px) mono

## Color

### Palette: Ivy Collection (green-gold-cream)
- **Approach:** Restrained — green as primary, gold as accent, cream as canvas. Color is intentional, not decorative.

### Core Colors
- **Primary (Forest Green):** #245A40 — trust, rigor, academic credibility. Used for: buttons, active states, producer agent identity, pass indicators
- **Primary Light:** #367A56 — hover states
- **Primary Subtle:** #E4EDE8 — backgrounds for success/pass elements
- **Gold Accent:** #C5A55A — premium accent, breakpoints, warnings, badges. The "editorial" signal.
- **Gold Light:** #D4B96E — hover states on gold elements
- **Gold Subtle:** #FAF5E8 — badge backgrounds, action panel background
- **Critic (Warm Terracotta):** #B85C4A — adversarial critic identity, rejection, errors
- **Critic Subtle:** #F6ECE8 — critic column background, rejection trace background

### Canvas
- **Page Background:** #F5F3ED — warm cream (Ivy Collection)
- **Card/Surface:** #FDFCFA — near-white with warmth
- **Inset/Subtle:** #EAE7E0 — for borders, dividers

### Text
- **Primary:** #1A1A18 — near-black, high contrast on cream
- **Secondary:** #3D3B36 — body text, descriptions
- **Tertiary:** #746144 — warm taupe, labels, metadata
- **Muted:** #6B6560 — timestamps, least important text. WCAG AA compliant on #FDFCFA.

### Semantic
- **Success:** #245A40 (same as primary — green IS success)
- **Warning:** #C5A55A (same as gold accent)
- **Error:** #B85C4A (same as critic)
- **Info:** #746144 (tertiary text)

### Borders
- **Default:** #DDD9D0
- **Light:** #EAE7E0

### Shadows (warm-toned, not blue-gray)
- **sm:** 0 1px 3px rgba(36,90,64,.04)
- **md:** 0 4px 14px rgba(36,90,64,.07)
- **lg:** 0 8px 28px rgba(36,90,64,.09)

### Dark Mode
Not implemented. Deferred. When added: reduce green saturation 15%, use elevation-based surfaces (#1A1A18 base, #222220 elevated, #2A2A28 cards), text #E0DDD8.

## Spacing
- **Base unit:** 4px
- **Density:** Comfortable
- **Scale:** 2xs(2px) xs(4px) sm(8px) md(16px) lg(24px) xl(32px) 2xl(48px) 3xl(64px)

## Layout
- **Approach:** Three-column app layout (sidebar + main workspace + context panel)
- **Sidebar:** 272px (left) — Research Director, Talent Roster, Pipeline Stages, Recent Runs
- **Main:** Flexible — hero input or meeting card stream
- **Panel:** 320px (right) — Events, Team, Calibration tabs
- **Max content width:** 720px for meeting cards in main area
- **Border radius:** sm: 6px, md: 12px, lg: 16px, pill: 999px
- **Breakpoints:** mobile (860px collapse to single column), mid (1100px reduce widths)

## Motion
- **Approach:** Intentional — animations communicate state changes, not decorate
- **Easing:** enter(ease-out) exit(ease-in) move(ease-in-out)
- **Duration:** micro(50-100ms) short(150-250ms) medium(250-400ms)
- **Specific animations:**
  - Meeting card entrance: opacity 0→1 + translateY(12px→0), 400ms
  - Pulse on active status dots: opacity 1→0.35, 1.5s infinite
  - Button hover: translateY(-1px), 150ms
  - Approve button glow: box-shadow pulse, 2s infinite
- **Accessibility:** `prefers-reduced-motion: reduce` disables all animations

## Component Identity

### OMC Concepts
- **Producer agents:** Green (#245A40) avatar backgrounds, green labels
- **Critic agents:** Terracotta (#B85C4A) avatar backgrounds, terracotta labels
- **Research Director:** Gold (#C5A55A) avatar, green-subtle card with green left border
- **Breakpoints:** Terracotta dots when set, hover shows terracotta border

### Meeting Cards
- **Active:** Green border, medium shadow
- **Paused (breakpoint):** Gold border, gold-subtle glow
- **Rejected:** Terracotta border
- **Completed:** Default border, 85% opacity, collapsible
- **Collapse behavior:** Click header to toggle. Collapsed shows only: title, badge, confidence mini-score

### Action Panel
- **Position:** Fixed bottom, between sidebar and panel
- **Border:** 2px solid gold top
- **Background:** Gold-subtle
- **Tabs:** Edit Output, Add Instructions, Override Critic, Skip Stages, Artifacts, Export

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-04-23 | Ivy Collection palette (green-gold-cream) | User preferred this over teal-terracotta-cream. Feels more premium and academic. |
| 2026-04-23 | Fraunces for display | Chosen over Cormorant Garamond (V1). More modern serif with better weight range. |
| 2026-04-23 | 15px base font size | User tested 16px (too big) and 17px (way too big). 15px is the sweet spot. |
| 2026-04-23 | OMC Meeting card layout | V2 introduced producer-vs-critic meeting cards. V3 added breakpoint system and action panel. |
| 2026-04-23 | Fixed action panel at bottom | Tried card-embedded (hidden by collapse). Fixed position on body ensures visibility. Uses addEventListener, not inline onclick. |
| 2026-04-23 | Event tags: text-only, no backgrounds | Colored backgrounds clashed. Pure text color differentiation is cleaner. Fixed width 54px for alignment. |
| 2026-04-23 | director CSS class rename to director-card | Sidebar `.director::before` green bar leaked to event tag `.director`. Renamed to avoid collision. |
