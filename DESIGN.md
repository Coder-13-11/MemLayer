---
name: Project memory dashboard
description: A calm, dense local workbench where every AI-written claim shows its status and origin before anything else. Product name is configurable (memlayer/brand.py) and is not part of this system.
colors:
  accent: "#5b5bd6"
  accent-hover: "#4c4cc4"
  accent-soft: "#ededfc"
  accent-text: "#4040b0"
  focus: "#8b8cf0"
  selection: "#d9d9fb"
  bg: "#fbfbfc"
  surface: "#ffffff"
  sidebar: "#f2f2f7"
  sidebar-hover: "#e8e8f0"
  sidebar-active: "#e2e2ee"
  hover: "#f4f4f7"
  border: "#e6e6ec"
  border-strong: "#d6d6de"
  text: "#1b1c22"
  text-2: "#565968"
  text-3: "#6c6f7e"
  st-supported: "#2e8a57"
  st-unverified: "#8a8d9b"
  st-challenged: "#c27c0e"
  st-contradicted: "#d0423f"
  st-superseded: "#b3b5c0"
  st-resolved: "#3a7fc1"
  st-retracted: "#9a9cab"
  kind-objective: "#5b5bd6"
  kind-finding: "#2f8fa8"
  kind-decision: "#8e4ec6"
  kind-assumption: "#c27c0e"
  kind-hypothesis: "#d0743f"
  kind-evidence: "#2e8a57"
  kind-question: "#d6409f"
  kind-task: "#6b7280"
  kind-note: "#8a8d9b"
typography:
  headline:
    fontFamily: "-apple-system, BlinkMacSystemFont, Inter, Segoe UI, system-ui, sans-serif"
    fontSize: "24px"
    fontWeight: 650
    lineHeight: 1.25
    letterSpacing: "-0.02em"
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, Inter, Segoe UI, system-ui, sans-serif"
    fontSize: "19px"
    fontWeight: 600
    lineHeight: 1.35
    letterSpacing: "-0.015em"
  title-sm:
    fontFamily: "-apple-system, BlinkMacSystemFont, Inter, Segoe UI, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 600
    lineHeight: 1.5
  section:
    fontFamily: "-apple-system, BlinkMacSystemFont, Inter, Segoe UI, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    lineHeight: 1.5
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, Inter, Segoe UI, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "\"cv11\", \"ss01\""
  body-sm:
    fontFamily: "-apple-system, BlinkMacSystemFont, Inter, Segoe UI, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, Inter, Segoe UI, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 500
    lineHeight: 1.5
  mono:
    fontFamily: "ui-monospace, SF Mono, JetBrains Mono, Menlo, monospace"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.55
rounded:
  mark: "2px"
  xs: "4px"
  sm: "6px"
  md: "8px"
  overlay: "12px"
  full: "99px"
spacing:
  2xs: "4px"
  xs: "6px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "20px"
  2xl: "28px"
  3xl: "40px"
  4xl: "64px"
components:
  button:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.sm}"
    padding: "0 11px"
    height: "30px"
  button-hover:
    backgroundColor: "{colors.hover}"
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.surface}"
    rounded: "{rounded.sm}"
    padding: "0 11px"
    height: "30px"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
  button-ghost:
    textColor: "{colors.text-2}"
    rounded: "{rounded.sm}"
    padding: "0 11px"
    height: "30px"
  button-ghost-hover:
    backgroundColor: "{colors.hover}"
    textColor: "{colors.text}"
  button-danger:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.st-contradicted}"
    rounded: "{rounded.sm}"
  button-sm:
    height: "26px"
    padding: "0 8px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.sm}"
    padding: "6px 9px"
    height: "32px"
  nav-item:
    textColor: "{colors.text-2}"
    rounded: "{rounded.sm}"
    padding: "5px 8px"
    height: "30px"
  nav-item-hover:
    backgroundColor: "{colors.sidebar-hover}"
    textColor: "{colors.text}"
  nav-item-active:
    backgroundColor: "{colors.sidebar-active}"
    textColor: "{colors.text}"
  tab:
    textColor: "{colors.text-2}"
    rounded: "{rounded.sm}"
    padding: "5px 10px"
  tab-active:
    backgroundColor: "{colors.hover}"
    textColor: "{colors.text}"
  status-pill:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text-2}"
    typography: "{typography.label}"
    rounded: "{rounded.full}"
    padding: "1px 8px 1px 6px"
  list-row:
    textColor: "{colors.text}"
    rounded: "{rounded.sm}"
    padding: "9px 8px"
  list-row-hover:
    backgroundColor: "{colors.hover}"
  table-row-selected:
    backgroundColor: "{colors.accent-soft}"
  trust-bar:
    backgroundColor: "{colors.border}"
    rounded: "{rounded.xs}"
    height: "8px"
  trust-bar-lg:
    rounded: "{rounded.sm}"
    height: "12px"
  record-panel:
    backgroundColor: "{colors.surface}"
    width: "480px"
    padding: "18px 20px 48px"
  palette:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.overlay}"
    width: "640px"
  toast:
    backgroundColor: "{colors.text}"
    textColor: "{colors.bg}"
    rounded: "{rounded.md}"
    padding: "10px 14px"
  kbd:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.text-3}"
    typography: "{typography.mono}"
    rounded: "{rounded.xs}"
    padding: "1px 5px"
---

# Design System: Project memory dashboard

## Overview

**Creative North Star: "The Provenance Ledger"**

A calm, dense workbench in the Notion-meets-Linear category, built so that the first thing anyone sees about a claim is its standing: its verification status, which model wrote it, and what evidence backs it. The surface is cool graphite paper with a faintly indigo-tinted sidebar. Nearly everything is neutral. Colour is spent only where it carries meaning: status, record kind, origin model, and the single iris accent for selection, focus and the primary action.

Density is high and quiet. Lists are hairline-ruled rows, not cards; sections are announced by a small bold heading over a 1px rule; numbers are tabular. Depth comes from tone (sidebar darker than canvas, surface lighter than canvas) and 1px borders. Only two things cast a shadow: the record panel sliding in from the right, and overlays (command palette, tooltip, toast). Motion is brief and decelerating, and it only marks arrivals: the panel, the palette, toasts, and a fading tint on activity rows that just arrived live.

Both themes are first-class. Light is the `:root` default; dark applies through `prefers-color-scheme` unless the page is pinned with `data-theme`, and every neutral, accent and status token has a dark counterpart. The product name is a configurable string and the mark is a generic layers glyph on the accent. No identity in this system depends on the name.

**Key Characteristics:**
- Cool graphite neutrals, one iris accent, and colour that always encodes something.
- Status before content: every record surface carries a status pill or ring.
- Hairline rows and tonal layering; no card grids, no nested cards.
- System UI type at 14px with tabular numerals; mono for ids, key hints and verbatim source text.
- The trust bar, a segmented bar of record statuses, is the signature and appears at two sizes.
- Dashed means uncertain; struck through means no longer current.

## Colors

A near-monochrome graphite field where hue is reserved for meaning, and the meaning is always status, kind, origin, or selection.

### Primary
- **Iris** (accent): primary buttons, the brand mark tile, and the focus border on inputs. Its hover is **Deep Iris** (accent-hover).
- **Iris Wash** (accent-soft): selected table rows, the active source row, the input focus ring, and the fading background on rows that arrived live.
- **Iris Ink** (accent-text): links and search-match highlighting in the palette. It exists so that iris can be used as text at a readable contrast.
- **Focus Lilac** (focus): the 2px `:focus-visible` outline on every interactive element, the same in both themes.
- **Selection Lilac** (selection): the text-selection background.

### Secondary: status vocabulary
These seven hues are what the system is about. They drive the trust bar, status pills, graph node rings, severity marks and relation lines.
- **Verified Green** (st-supported): evidence-supported, "supports" relations, the live indicator when connected.
- **Slate** (st-unverified): unverified. It is always paired with a dashed shape (dot, ring) so it reads as an open state rather than as plain grey.
- **Amber** (st-challenged): challenged, "challenges" relations, medium severity.
- **Signal Red** (st-contradicted): contradicted, "contradicts" relations, high severity, destructive buttons and error toasts.
- **Ash** (st-superseded): superseded. It is paired with strikethrough text and a dotted ring.
- **Answer Blue** (st-resolved): resolved, "answers" relations.
- **Faded Slate** (st-retracted): retracted.

Severity has its own tokens (`--sev-high`, `--sev-medium`, `--sev-low`), but they are aliases with the same values as contradicted, challenged and unverified.

### Tertiary: kind and origin
- **Kind hues** (kind-*): nine hues mark record kinds as small 8px square swatches next to the kind label, and as the fill of graph nodes. Some are deliberately shared with other roles: objective uses Iris, evidence uses Verified Green, assumption uses Amber, and note uses Slate.
- **Model hues**: chips for the origin model take their colour from a hash of the model name, chosen from eight fixed hues (250, 190, 145, 30, 330, 280, 95, 210). In light mode the chip is `hsl(h 60% 94%)` with `hsl(h 55% 32%)` text; in dark mode it is `hsl(h 35% 20%)` with `hsl(h 70% 78%)` text. A human or unknown author gets a neutral chip (hover background, text-2).

### Neutral
- **Graphite Paper** (bg): the canvas, the topbar, and recessed wells (excerpts, inline forms, kbd).
- **Sheet** (surface): raised surfaces: buttons, inputs, the record panel, the palette, pills, the source body.
- **Indigo Graphite** (sidebar), with **sidebar-hover** and **sidebar-active**: the faintly indigo-tinted navigation column and its row states.
- **Hover Mist** (hover): row and tab hover, the active tab, and activity icon tiles.
- **Hairline** (border) and **Firm Hairline** (border-strong): every rule, outline and divider. The firm hairline is used for hover outlines, scrollbars and history dots.
- **Ink** (text), **Graphite** (text-2), **Pewter** (text-3): primary content, secondary content, and meta (timestamps, counts, labels, placeholders).

### Named Rules
**The Colour Does Jobs Rule.** Every hue on screen encodes status, kind, origin model, or selection. A colour that only decorates does not ship.

**The Iris Is Selection Rule.** Iris is used for selection, focus, links and the one primary action in a view. It is never a status and never decoration.

**The Neutral Models Rule.** Origin-model colours come from a hash of the name, using one fixed lightness and chroma for every model. No provider gets a brand colour or a privileged hue.

## Typography

**Body Font:** the system UI stack (-apple-system, BlinkMacSystemFont, Inter, Segoe UI, system-ui), with `cv11` and `ss01` enabled.
**Mono Font:** ui-monospace (SF Mono, JetBrains Mono, Menlo).

**Character:** a single native sans at working size, tightened slightly at heading sizes, with no display face. The type reads like a well-made tool, not a publication.

### Hierarchy
- **Headline** (650, 24px, 1.25, -0.02em): page titles only (project name, view title).
- **Title** (600, 19px, 1.35, -0.015em): the record title in the detail panel.
- **Title-sm** (600, 15px): the trust bar caption figure and the headings of the Connect block.
- **Section** (600, 13px): section headings over a hairline rule. They are sentence case and never uppercase.
- **Body** (400, 14px, 1.5): the default for rows, panel content (at 1.6) and descriptions. Prose blocks are limited to 72ch for page subtitles and 80ch for gap summaries.
- **Body-sm** (13px): provenance lists, evidence, relations, legends, toasts.
- **Label** (500, 12px): field labels, table headers, sidebar section names, meta and counts (meta and counts use weight 400).
- **Mono** (12px): record ids, key hints, verbatim source text (12.5px, 1.6) and code snippets.

Weight 550 is used once, for project names in the home list.

### Named Rules
**The Tabular Rule.** Every count, date, version and statistic uses `font-variant-numeric: tabular-nums`, so columns of figures line up.

**The Verbatim Is Mono Rule.** Mono is used for things that are literally what they are: ids, keys, commands and imported source text. It is never used for UI prose.

## Layout

The shell is a fixed three-column grid: a 248px sidebar, a fluid main column, and a 480px record panel that appears when a record is open. The whole app is exactly the height of the viewport (100vh); each column scrolls on its own. The main column has a 48px topbar (breadcrumbs, view tabs with counts, actions) above the view. View content is capped at 1120px and centred, with 28px 40px 64px padding. The graph view is full-bleed and has no cap.

Overview content runs in two columns at a 1.25 : 1 ratio with a 40px gap. Sources use a 340px list beside the body. Home rows are a four-column grid: name, trust bar, counts, status.

The spacing rhythm is tight: 4, 6 and 8px inside controls, 10–12px between items in a row, 14–20px of padding on rows and panels, and 28–40px between sections. Rows bleed 8px into the gutter (negative margin) so that their hover fill extends past the text edge.

Responsive behaviour:
- **At 1180px and below**, the record panel becomes a fixed overlay on the right edge, and the two-column layouts stack.
- **At 820px and below**, the sidebar is hidden, view padding drops to 20px 16px, the topbar scrolls horizontally, breadcrumbs and button labels are hidden, and the home list keeps only name and one meta column.
- **With reduced motion**, all animation and transition durations collapse to near zero.

## Elevation & Depth

Depth comes from tone and hairlines. The canvas (bg) sits between the darker sidebar and the lighter sheet (surface), and 1px borders separate everything else. Surfaces are flat at rest; hovering changes a fill or a border, never adds a shadow.

### Shadow Vocabulary
- **Panel edge** (`--shadow-panel`): a soft leftward shadow on the record panel, which marks it as a drawer above the main column.
- **Pop** (`--shadow-pop`): a two-layer lift used only by floating overlays (the command palette, the graph tooltip, toasts).

### Named Rules
**The Two Shadows Rule.** Only the record drawer and floating overlays cast a shadow. Rows, sections, buttons and inline boxes stay flat.

**The Hairline Rule.** Structure is drawn with 1px border lines and changes of tone. Lists are ruled rows, never a grid of cards, and a bordered box is never placed inside another bordered box.

## Shapes

Corners are small and consistent. The default is 6px (sm) for controls, rows and small boxes, and 8px (md) for containers such as inline forms, the source body, graph tool panels and toasts. The command palette is the only element at 12px. Status pills are fully rounded. Colour marks (kind swatches, legend swatches) are 8px squares at 2px radius; status dots are circles. The trust bar is a rounded capsule (4px at 8px tall, 6px at 12px tall) whose segments are separated by 2px gaps.

Line style carries meaning:
- **Dashed** means uncertain: the unverified status dot and node ring, and the "possible" confidence badge.
- **Dotted or short-dashed** marks superseded and retracted rings, and "supersedes" and "relates to" relation lines.
- **Strikethrough** marks content that is no longer current (superseded pill, inactive titles, inactive graph labels).

**The Dashed Means Uncertain Rule.** A dashed or dotted stroke always signals a weaker epistemic state. Never use dashes as decoration.

## Components

### Buttons
Quiet and outlined; one primary per context.
- **Shape:** gently rounded (sm), 30px tall, or 26px for the small size; icon and label separated by 6px.
- **Default:** surface fill with a hairline border; on hover, the fill changes to hover mist and the border to firm hairline (150ms).
- **Primary:** iris fill and border with white text; hover changes to deep iris.
- **Ghost:** no border or fill, graphite text; on hover, a hover mist fill.
- **Danger:** red text on the default button. Confirming a deletion is a second click on the same button, which then fills red.
- **Icon button:** 24px square, no chrome, pewter icon; on hover, a sidebar-hover fill.
- **Disabled:** 50% opacity.

### Status pills and chips
- **Status pill:** fully rounded, with a hairline border, a surface fill, 12px graphite text, and a 7px status dot. Unverified uses a dashed hollow ring instead of a dot; superseded strikes through its label.
- **Model chip:** a 5px-radius chip, 12px/500, filled with the hashed model hue (see Colors). Shown as a chip row under the project title and inline in provenance.
- **Kind tag:** an 8px kind swatch followed by a 12px graphite label, with no container.
- **Record link:** a compact outlined chip (hairline, surface fill, 12px) that opens a record. Used in gap references and relation lists.
- **Confidence badge:** an 11px outlined tag; for "possible" the border is dashed.

### Lists, tables and feeds
- **List row:** full width, 9px 8px padding, bleeding 8px into the gutter, 6px radius; on hover, a hover mist fill. The title is ellipsized and meta sits right-aligned in pewter with tabular numerals.
- **Records table:** collapsed borders, a 12px pewter header row with sort buttons, 8px 10px cells, and hairline row rules. Hovering a row fills it with hover mist; a selected row takes iris wash. Inactive titles are pewter and struck through.
- **Gap item:** a two-column grid with a 10px severity square, a meta line (type, confidence, date), the summary, chips for the referenced records, and a suggested action in pewter.
- **Activity feed:** 22px icon tile, what changed, time. Rows that arrive live fade from iris wash to transparent over 2.4s.

### Inputs / Fields
- **Style:** surface fill, hairline border, 6px radius, minimum height 32px, padding 6px 9px; pewter placeholder.
- **Hover:** border changes to firm hairline.
- **Focus:** border changes to iris, with a 3px iris-wash ring (no outline).
- **Field label:** 12px/500 graphite, above the control, 5px gap; a pewter hint may follow below.
- **Error:** a 13px red line above the actions.

### Navigation
- **Sidebar:** indigo graphite column with 12px 8px padding. From top to bottom: the brand mark (24px iris tile with the layers icon) and name, the search trigger (an outlined field with a `⌘K` key hint), Home, a "Projects" section label, then one project per row with a miniature trust bar (36px) at the right. A live indicator and the theme toggle sit at the foot.
- **Nav item:** 30px row, graphite text; on hover, sidebar-hover fill with ink text; when active, sidebar-active fill at weight 500.
- **Topbar tabs:** 5px 10px with a 6px radius and a pewter count; the active tab gets a hover-mist fill and weight 500.

### Record panel (drawer)
A 480px sheet on the right with a left hairline and the panel-edge shadow. It slides in over 280ms. A sticky header holds the record id (mono, click to copy) and actions. The body runs in this order: status and kind, the title, the content (pre-wrapped, 1.6 leading), then a two-column provenance list (110px pewter terms). Below that are sections with 12px headings and tabular counts: evidence (outlined boxes, italic quotes), relations (an 88px pewter verb plus a record link), an excerpt of the verbatim source (mono well), and history (a vertical hairline with dots).

### Command palette
640px, centred 12vh from the top over a dimmed backdrop. It has a 12px radius and the pop shadow, and enters with a 200ms drop-and-scale. Below the 50px query field (15px text), results are grouped under 11.5px pewter group names; the highlighted row has a hover-mist fill, and matched text is shown in iris ink at weight 600. A footer lists the keyboard hints.

### Trust bar (signature)
A segmented horizontal bar showing the project's records by status, always in the fixed order supported → unverified → challenged → contradicted → resolved → superseded → retracted. Each segment's width is proportional to its count, with a 3px minimum and 2px gaps over a hairline-coloured track.
- **Large** (12px tall, 6px radius): full width on the overview, under a caption giving the share that is evidence-supported. It has a legend of clickable swatches with bold tabular counts. Clicking a segment or a legend entry filters the records view to that status; hovering a segment brightens it.
- **Small** (8px tall, 4px radius): 36px wide beside each project in the sidebar, and in the trust column of the home list.
- It always carries an aria-label that lists the counts.

### Toasts
Bottom-right, with a max width of 380px. The fill is inverted (ink background, paper text), with an 8px radius and the pop shadow. Live-write toasts start with the green live dot; error toasts fill signal red.

## Do's and Don'ts

### Do:
- **Do** show a status pill (or a status ring in the graph) wherever a record title appears.
- **Do** take every colour from a token on `:root` and give it a counterpart in both the dark media query and the `data-theme="dark"` block.
- **Do** colour origin models only through the name hash, and give human or unknown authors the neutral chip.
- **Do** keep the trust bar's fixed status order and its two sizes (12px on the overview, 8px in lists).
- **Do** use the 16px single-weight (1.6) line icon set for every icon, including new ones.
- **Do** use tabular numerals for every figure and pewter (text-3) for meta.
- **Do** keep motion to arrivals (drawer, palette, toast, live row) on the shared decelerating ease, and respect reduced motion.

### Don't:
- **Don't** use a status hue, a kind hue or iris for decoration, backgrounds or emphasis that encodes nothing.
- **Don't** give an AI provider a fixed or brand colour.
- **Don't** lay lists out as card grids or place a bordered box inside another bordered box; use ruled rows.
- **Don't** add shadows to rows, sections or inline boxes; only the drawer and floating overlays are lifted.
- **Don't** use dashed or dotted strokes except to signal uncertainty or a superseded state.
- **Don't** add hero metrics, gradients or glow to the overview; the trust bar is the summary.
- **Don't** put the product name into tokens, colours or shapes; it comes from configuration.
