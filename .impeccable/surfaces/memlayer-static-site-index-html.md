---
version: 1
slug: "memlayer-static-site-index-html"
primary_target: "memlayer/static/site/index.html"
related_targets: ["memlayer/hosted.py"]
---

# Surface: public site (landing + sign-in)

Scope: the hosted service's public pages: `/` (landing, Persuade) and `/consent` (sign-in and approve, Operate).
Visitor: researchers, analysts and builders who split long research across ChatGPT, Claude, Gemini and Perplexity.
Action: request an invite to the private beta (email, optional "what are you researching"). Proof: the product
working, shown as an illustrative live demo; real security mechanics; an honest limitations section. No customers,
metrics, logos-of-customers or testimonials exist; none are invented. Demo data is synthetic and labelled.

## Direction contract

THESIS: MemLayer's site is a research paper about its own product: the title is the promise, the byline is the
AIs that write into it, and every claim on the page carries a red citation you can follow. It refuses the dark
AI-startup hero with a glowing screenshot and the warm-cream editorial page.

OWN-WORLD: White coated journal stock, black ink, one journal red (#c8102e) for citations, the primary action
and the contradiction flag. STIX Two Text (the publishers' own face) for titles and prose; Archivo for
captions, tables, controls; system mono only for tool calls and ids. Thick black masthead rule, figure plates
on a pale grey wash, bold "Fig. 1 |" captions, numbered references. Status hues only inside figures.

STORY: A visitor learns in one line that every AI they use can share one memory; watches Fig. 1 show ChatGPT
filing, Claude catching a contradiction and Gemini answering with citations that light the records; checks
how to connect each app, what each record keeps, how data is protected, and the limitations; requests an invite.

FIRST VIEWPORT: Masthead (wordmark left, section links, red "Request access" right) over a 4px rule. Left
two-thirds: the title in STIX ~76px, the byline of assistants with red superscripts, the abstract. Right
third: the submission box (email, research topic, red button). The top edge of Fig. 1, the live demo, shows
below the fold line on a 1440x900 screen.

FORM: The Journal, position 1 on the ordered list (picked over the assigned Catalog). seed:ff6c60be0f167423.
Signature interaction: Fig. 1 plays a scripted session in which tool calls file records into a live ledger and
answer citations light the exact records; play, pause, step; static final state under reduced motion.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
