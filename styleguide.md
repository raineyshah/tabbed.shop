# tabbed.shop — UI styleguide

This document summarizes reusable visual patterns and the **homepage (landing)** layout. Implementation lives primarily in `static/styles.css`.

## Liquidy frosted glass (`--tabbed-liquid-*`)

Shared translucent surfaces: soft blue/lavender gradients + blur + light border. Defined on `:root`:

| Token | Role |
|--------|------|
| `--tabbed-liquid-bg` | Base fill (semi-transparent white) |
| `--tabbed-liquid-layers` | Stacked radial + diagonal gradients |
| `--tabbed-liquid-blur` | `blur(14px) saturate(1.18)` (+ `-webkit-` equivalent where applied) |
| `--tabbed-liquid-edge` | Border color (slate, soft) |
| `--tabbed-liquid-inset-top` | Inner highlight along top edge |
| `--tabbed-liquid-shadow-bar` | Header strip shadow |
| `--tabbed-liquid-shadow-panel` | Dropdown / panel drop shadow |

**Used on:** `.header`, header dropdowns (categories, simple nav, search field, profile menu), `.shop-sidebar` / `.sidebar`, shop filter panels (lighter inner frost), and **`.landing-category-card`** on the homepage.

## Landing page (`/`)

**Template:** `templates/index.html`  
**Page shell:** `.page-wrap.page-wrap--about.page-wrap--landing` (full width, no side margins like inner about pages; safe-area padding only).

### Regions (top → bottom)

1. **`.landing`** — Full-width column; bottom padding is slightly tightened for a shorter scroll.
2. **`.landing-hero`** — Hero block: logo, headline, tagline.
   - **`.landing-hero__glow`** — Reserved glow slot (currently minimal); size scales with a **smaller** clamp than before so the hero uses less vertical space.
   - **`.landing-hero__content`** — Centers content above the glow layer.
3. **`.landing-logo__img`** — Animated logo (`logo-animated.svg`). On the homepage only, size is **reduced** (e.g. 104px base, modest clamp up to ~900px wide).
4. **`.landing-headline`** — `h1`. **Animated tab-gradient text** synchronized with logo colors (`@property` + keyframes, disabled under `prefers-reduced-motion`).
5. **`.landing-tagline`** — Muted body intro (`#4a5568`), balanced wrapping.
6. **`.landing-categories`** — Category section (only if `nav_categories` is non-empty).
   - **`.landing-section-heading`** — Uppercase micro-label (“Start browsing by category:”).
   - **`.landing-category-grid`** — CSS grid: **3 columns**, centered, `max-width: 52rem`, **tighter gap** (8px + small clamp).
   - **`.landing-category-card`** — Each category link uses the **liquidy glass** treatment (same tokens as header), with hover/focus states. **Shorter** `min-height`, padding, and type size than before so more fits in one viewport.
7. **`.landing-site-links`** — Footer row: About · Contact · FAQ (dot separators).

### Responsive behavior

From **667px** upward, hero, logo, headline, category grid, cards, and footer links scale with **`clamp()`** between 667px and ~900px viewport formulas (see `@media (min-width: 667px)` under “Landing page”). Caps and bases were **reduced** so the layout stays shorter on typical laptop heights.

### Other landing-related styles (elsewhere)

- **`.landing-btn`**, **`.landing-btn--primary`**, **`.landing-btn--secondary`**, **`.landing-btn--ghost`** — CTA buttons (not used on current `index.html`, but available for hero modules).
- **`.landing-section-lead`** — Optional section intro (not on current index).
- **`.profile-v2-meta-tile.landing-category-card`** — Reuses the category **class name** on profile/hamburger for shape; **profile-specific rules override** the glass so public profile tiles stay neutral.

## Keeping this guide

When you add new surfaces that should match the shell, reuse the `--tabbed-liquid-*` variables instead of one-off whites and grays.
