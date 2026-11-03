// DOM helpers. Every piece of stored data is inserted as text (textContent),
// never as HTML: imported conversations are untrusted input.

export function h(tag, attrs = {}, ...children) {
  const svgTags = new Set(["svg", "path", "circle", "line", "g", "rect", "text", "marker", "defs", "polyline"]);
  const el = svgTags.has(tag)
    ? document.createElementNS("http://www.w3.org/2000/svg", tag)
    : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.setAttribute("class", v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k === "value" && "value" in el) el.value = v;
    else if (k === "checked" || k === "selected" || k === "disabled" || k === "hidden") el[k] = Boolean(v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(el, ...children) {
  el.replaceChildren();
  append(el, children);
  return el;
}

// ------------------------------------------------------------------ icons
// Authored 16px line icons, one stroke weight.
const PATHS = {
  home: "M3 7.5 8 3.5l5 4V13H9.5V9.5h-3V13H3z",
  search: "M7 12A5 5 0 1 0 7 2a5 5 0 0 0 0 10zM14 14l-3.5-3.5",
  plus: "M8 3v10M3 8h10",
  overview: "M2.5 2.5h4.5v5H2.5zM9 2.5h4.5v3H9zM9 7.5h4.5v6H9zM2.5 9.5h4.5v4H2.5z",
  records: "M3 4h10M3 8h10M3 12h6",
  graph: "M4 4.5a1.5 1.5 0 1 0 0-.01M12 3.5a1.5 1.5 0 1 0 0-.01M11 12.5a1.5 1.5 0 1 0 0-.01M5.3 4.2l5.2-.6M4.8 5.8l5.1 5.6M12 5v6",
  gaps: "M8 2.5 14 13H2zM8 6.5v3M8 11.3v.2",
  sources: "M4 2h5.5L12.5 5v9H4zM9.5 2v3h3M6 8h4.5M6 10.5h4.5",
  activity: "M8 14A6 6 0 1 0 8 2a6 6 0 0 0 0 12zM8 4.8V8l2.2 1.4",
  import: "M8 2.5v8M5 7.5l3 3 3-3M3 12.5v1h10v-1",
  close: "M4 4l8 8M12 4l-8 8",
  check: "M3.5 8.5 6.5 11.5 12.5 4.5",
  link: "M6.8 9.2a2.6 2.6 0 0 0 3.7 0l2-2a2.6 2.6 0 1 0-3.7-3.7l-.6.6M9.2 6.8a2.6 2.6 0 0 0-3.7 0l-2 2a2.6 2.6 0 1 0 3.7 3.7l.6-.6",
  trash: "M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 9h5.8l.6-9",
  edit: "M10.5 3 13 5.5 6 12.5H3.5V10z",
  replace: "M3 5.5h8l-2.5-2.5M13 10.5H5l2.5 2.5",
  status: "M8 14A6 6 0 1 0 8 2a6 6 0 0 0 0 12zM5.5 8.2l1.8 1.8 3.3-3.6",
  evidence: "M5 2.5h6M6 2.5v4L3 12.5a1 1 0 0 0 .9 1.5h8.2a1 1 0 0 0 .9-1.5L10 6.5v-4",
  sun: "M8 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M3.4 12.6l1-1M11.6 4.4l1-1",
  moon: "M13 9.5A5.5 5.5 0 0 1 6.5 3a5.5 5.5 0 1 0 6.5 6.5z",
  layers: "M2.5 6 8 3l5.5 3L8 9z M2.5 9 8 12l5.5-3",
  copy: "M5.5 5.5h7v7h-7zM10.5 5.5v-2h-7v7h2",
  external: "M9 3h4v4M13 3 7.5 8.5M11 9.5V13H3V5h3.5",
  up: "M8 12.5v-9M4.5 7 8 3.5 11.5 7",
  down: "M8 3.5v9M4.5 9 8 12.5 11.5 9",
  sparkle: "M8 2.5v3M8 10.5v3M2.5 8h3M10.5 8h3M4.2 4.2l2 2M9.8 9.8l2 2M4.2 11.8l2-2M9.8 6.2l2-2",
};

export function icon(name, cls = "") {
  return h("svg", { class: `icon ${cls}`, viewBox: "0 0 16 16", "aria-hidden": "true" },
    h("path", { d: PATHS[name] || PATHS.records }));
}

// ----------------------------------------------------------- vocabulary
export const STATUS = {
  evidence_supported: { label: "Evidence-supported", color: "var(--st-supported)" },
  unverified: { label: "Unverified", color: "var(--st-unverified)" },
  challenged: { label: "Challenged", color: "var(--st-challenged)" },
  contradicted: { label: "Contradicted", color: "var(--st-contradicted)" },
  superseded: { label: "Superseded", color: "var(--st-superseded)" },
  resolved: { label: "Resolved", color: "var(--st-resolved)" },
  retracted: { label: "Retracted", color: "var(--st-retracted)" },
};
export const STATUS_ORDER = ["evidence_supported", "unverified", "challenged", "contradicted", "resolved", "superseded", "retracted"];

export const KIND = {
  objective: { label: "Objective", color: "var(--kind-objective)" },
  finding: { label: "Finding", color: "var(--kind-finding)" },
  decision: { label: "Decision", color: "var(--kind-decision)" },
  assumption: { label: "Assumption", color: "var(--kind-assumption)" },
  hypothesis: { label: "Hypothesis", color: "var(--kind-hypothesis)" },
  evidence: { label: "Evidence", color: "var(--kind-evidence)" },
  question: { label: "Question", color: "var(--kind-question)" },
  task: { label: "Task", color: "var(--kind-task)" },
  note: { label: "Note", color: "var(--kind-note)" },
};

export const RELATION = {
  supports: { label: "supports", color: "var(--st-supported)" },
  challenges: { label: "challenges", color: "var(--st-challenged)" },
  contradicts: { label: "contradicts", color: "var(--st-contradicted)" },
  depends_on: { label: "depends on", color: "var(--text-3)" },
  requires: { label: "requires", color: "var(--text-3)" },
  supersedes: { label: "supersedes", color: "var(--st-superseded)", dash: "4 3" },
  answers: { label: "answers", color: "var(--st-resolved)" },
  relates_to: { label: "relates to", color: "var(--border-strong)", dash: "2 3" },
};
export const REL_IN = {
  supports: "supported by", challenges: "challenged by", contradicts: "contradicted by", depends_on: "needed by",
  requires: "required by", supersedes: "superseded by", answers: "answered by", relates_to: "related to",
};

export const GAP_LABEL = {
  contradiction: "Contradiction", possible_contradiction: "Possible contradiction", challenged_claim: "Challenged claim",
  missing_evidence: "Missing evidence", unresolved_assumption: "Unresolved assumption",
  incomplete_dependency: "Incomplete dependency", open_question: "Open question",
  candidate_answer_unreviewed: "Answer to review",
};
export const SEV_COLOR = { high: "var(--sev-high)", medium: "var(--sev-medium)", low: "var(--sev-low)" };

// Model colours are hashed from the name so no provider is favoured.
const MODEL_HUES = [250, 190, 145, 30, 330, 280, 95, 210];
export function modelColor(name) {
  if (!name || name === "human" || name === "unknown") return { bg: "var(--hover)", fg: "var(--text-2)" };
  let n = 0;
  for (const ch of name.toLowerCase()) n = (n * 31 + ch.charCodeAt(0)) >>> 0;
  const hue = MODEL_HUES[n % MODEL_HUES.length];
  const dark = document.documentElement.dataset.theme === "dark" ||
    (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
  return dark
    ? { bg: `hsl(${hue} 35% 20%)`, fg: `hsl(${hue} 70% 78%)` }
    : { bg: `hsl(${hue} 60% 94%)`, fg: `hsl(${hue} 55% 32%)` };
}

export function modelChip(name) {
  const label = name || "unknown";
  const c = modelColor(label);
  return h("span", { class: "chip", style: { background: c.bg, color: c.fg }, title: `Origin: ${label}` }, label);
}

export function statusPill(status) {
  const s = STATUS[status] || { label: status, color: "var(--text-3)" };
  return h("span", { class: `pill st-${status}` },
    h("span", { class: "dot", style: status === "unverified" ? {} : { background: s.color } }), s.label);
}

export function kindTag(kind) {
  const k = KIND[kind] || { label: kind, color: "var(--text-3)" };
  return h("span", { class: "kind" }, h("i", { style: { background: k.color } }), k.label);
}

export function trustBar(counts, { large = false, onPick = null } = {}) {
  const total = Object.values(counts || {}).reduce((a, b) => a + b, 0);
  const bar = h("div", { class: `trust ${large ? "lg" : ""}`, role: "img",
    "aria-label": STATUS_ORDER.filter((s) => counts?.[s]).map((s) => `${counts[s]} ${STATUS[s].label}`).join(", ") || "No records" });
  if (!total) return bar;
  for (const s of STATUS_ORDER) {
    if (!counts[s]) continue;
    bar.append(h("span", {
      style: { flex: String(counts[s]), background: STATUS[s].color },
      title: `${counts[s]} ${STATUS[s].label.toLowerCase()}`,
      onclick: onPick ? () => onPick(s) : null,
    }));
  }
  return bar;
}

// ------------------------------------------------------------------- time
export function ago(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const s = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  if (s < 86400 * 7) return `${Math.round(s / 86400)}d ago`;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: d.getFullYear() === new Date().getFullYear() ? undefined : "numeric" });
}
export function stamp(iso) {
  return iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
}

// ----------------------------------------------------------------- toasts
export function toast(message, { error = false, live = false } = {}) {
  const box = document.getElementById("toasts");
  const t = h("div", { class: `toast ${error ? "err" : ""}`, role: error ? "alert" : "status" },
    live ? h("span", { class: "live-dot on" }) : null, h("span", {}, message));
  box.append(t);
  setTimeout(() => t.remove(), error ? 6000 : 4200);
}

export function plural(n, word, many = `${word}s`) {
  return `${n} ${n === 1 ? word : many}`;
}
