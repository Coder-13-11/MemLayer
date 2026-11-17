---
name: NEXUS, the carbon copy
description: Multipart carbonless forms and till receipts. A bright white original, canary / pink / sky / mint copy sheets, carbon-blue ink, red serial numbers and stamps. The public site commits to the world; the app keeps the same ink and paper at working density.
colors:
  ink: "#15154a"
  ink-2: "#3a3b6c"
  ink-3: "#5c5d87"
  blue: "#2b2b9e"
  blue-deep: "#1e1e7c"
  carbon: "#4040b4"
  paper: "#ffffff"
  desk: "#f3f4fa"
  canary: "#ffe14d"
  canary-soft: "#fff6c4"
  pink: "#f9b9cf"
  pink-soft: "#fde8ef"
  sky: "#c5ddf6"
  mint: "#c6e9d1"
  red: "#c9241b"
  app-border: "#dfe1ee"
  app-border-strong: "#c2c6dc"
  st-supported: "#1f7a4c"
  st-unverified: "#777aa0"
  st-challenged: "#a8690a"
  st-contradicted: "#c9241b"
  st-superseded: "#a4a7c2"
  st-resolved: "#2a69b8"
  st-retracted: "#8f91ae"
  site-bg: "#090a0f"
  site-bg-2: "#0c0e15"
  site-card: "#11131c"
  site-card-2: "#161925"
  site-line: "#1e2230"
  site-line-2: "#2a3044"
  site-text: "#f4f5f8"
  site-text-2: "#c3c7d6"
  site-muted: "#8a90a6"
  site-mint: "#10b981"
  site-crimson: "#f43f5e"
  site-amber: "#f59e0b"
  site-cobalt: "#3b82f6"
  site-cobalt-2: "#60a5fa"
typography:
  display:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "clamp(2.5rem, 4.6vw + .7rem, 5rem)"
    fontWeight: 800
    lineHeight: 1
    letterSpacing: "-0.035em"
  heading:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "clamp(1.9rem, 2.6vw + .8rem, 3.25rem)"
    fontWeight: 800
    lineHeight: 1
    letterSpacing: "-0.035em"
  body:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 400
    lineHeight: 1.55
  form-label:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "0.6875rem"
    fontWeight: 700
    letterSpacing: "0.1em"
    textTransform: uppercase
  print:
    fontFamily: "ui-monospace, SF Mono, JetBrains Mono, Menlo, Consolas, monospace"
    fontSize: "0.8125rem"
    fontWeight: 400
  app-body:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
---

# NEXUS design system

> **Public site, from 0.2.1: "archival technical".** The landing page, documentation and sign-in pages moved to
> a dark system at the owner's direction: obsidian ground `#090A0F`, ledger cards `#11131C` with `#1E2230`
> hairlines, text `#F4F5F8`, metadata `#8A90A6`, mint `#10B981` (confirmed), crimson `#F43F5E` (voided), amber
> `#F59E0B` (disputed), cobalt `#3B82F6` (the one action). A geometric sans (Geist / Inter, falling back to the
> system sans) for reading and a monospace (Geist Mono / JetBrains Mono, falling back to the system mono) for
> record numbers, timestamps and commands. Motion is 150 to 300 ms state feedback, a blinking cursor on live
> text, and scroll reveals that are off without JavaScript or with reduced motion. The tokens live at the top of
> `nexus/static/site/site.css`. The sections below still describe the signed-in app, which keeps the light
> carbon-ink system until it is moved across.

## The world

A decision written once on the top sheet of a multipart form presses through to a copy for every AI tool. A
receipt itemises what a model was handed. A replaced decision is stamped, not erased. Every visual choice comes
from that object: forms, copies, stamps, receipts.

It was chosen over two defaults in this category: the dark page with a glowing node graph, and the cream
editorial serif (which the previous version used). The reader is an engineering lead at a desk in daylight, so
the ground is light.

## Colour

The strategy is committed on the public site and restrained in the app.

- **Paper** `#ffffff` is the original: bright white, not cream.
- **Canary** `#ffe14d` owns whole regions on the site: the hero, the close, the sign-in pages, the header on the
  landing page. In the app it is selection and highlight only (text selection, the current workspace, a stored
  answer).
- **Carbon blue** `#2b2b9e` is the primary action and the ink of anything a person entered. One drenched band
  on the landing page (the receipt) uses it as ground.
- **Ink** `#15154a` is all text. Secondary text is tinted from it (`#3a3b6c`, `#5c5d87`), never grey.
- **Pink, sky, mint** are copy sheets. They mean "a copy held by a tool" on the site and are not used for status.
- **Red** `#c9241b` is serial numbers, stamps, conflicts and destructive actions. Nothing else.
- Status hues in the app keep one meaning everywhere: green evidence-supported, grey-blue unverified, amber
  challenged, red contradicted, pale superseded, blue resolved.

Dark theme exists in the app only: deep carbon grounds (`#0e0f2c`), periwinkle accent, canary focus ring.

## Type

One family, Archivo, self-hosted. Weight and case do the work a second family would.

- Display and headings: 800, tight tracking, sentence case, balanced wrapping.
- Form labels: 11px, 700, uppercase, tracked 0.1em. Used for field labels, stubs and copy designations.
- Print voice: the system monospace, only for what a form or till would print or a person would type into a
  field: entries on the hero form, receipt lines, record numbers, commands. Never as decoration.
- App: 14px body, 1.5 line height, fixed sizes (no fluid type in the app).

## Components

**Sheet.** White (or a copy colour), soft offset shadow `0 26px 44px -30px` in ink, no border, no radius. A
*stub* at the top: a dashed rule under two small uppercase labels, the right one often a red serial number.
A *designation* at the bottom: a solid rule over one uppercase line.

**Field.** A 1.5px ink box with its label inside the top-left corner. Fields butt against each other and share
borders, as on a printed form.

**Stamp.** Red (or blue for "clear"), 2.5 to 3.5px border, heavy uppercase, rotated 2 to 3 degrees. It arrives
with one motion: scaled down from large with a short blur.

**Receipt.** A narrow white column in the print voice: centred header, dashed rules, lines with dotted leaders
to a right-aligned value, a zig-zag or perforated edge. The app's receipt view and the landing page's use the
same construction.

**Buttons.** 3px radius, 1.5px ink border; the primary is carbon blue. They lift one pixel on hover.

**App chrome.** Sidebar on a blue-tinted paper (`#f3f4fa`), 1px hairlines, 6px radius, the active project tab
underlined in carbon blue. Tables are plain: hairline rows, uppercase 12px headers.

## Motion

One authored moment per surface. On the landing page it is the transfer: the decision is typed once and appears
on each copy 110 to 350 ms later, each copy dipping 1.5px as it "presses". The stamp is the second, on demand.
Everything else is 150 to 250 ms state feedback on an exponential ease-out (`cubic-bezier(.16, 1, .3, 1)`).
With reduced motion, text is set immediately and animations are removed.

## Rules

- Stored text is inserted as text, never as HTML, on every surface.
- No kicker above a heading, no card grids as page structure, no gradient text, no decorative blur.
- Rotation is for copies, slips, stamps and the closing sheet only, and never more than 3 degrees.
- Canary never carries a status. Red never decorates.
- Every invented example is labelled as invented where a visitor could take it for real.
