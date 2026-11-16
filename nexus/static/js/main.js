// App shell: routing, sidebar, top bar, record panel, live updates.
import { api, setWorkspace } from "./api.js";
import { h, clear, icon, trustBar, toast, KIND } from "./dom.js";
import { openPalette } from "./palette.js";
import { actorName } from "./views/common.js";
import { renderPanel } from "./panel.js";
import * as home from "./views/home.js";
import * as overview from "./views/overview.js";
import * as records from "./views/records.js";
import * as graph from "./views/graph.js";
import * as gaps from "./views/gaps.js";
import * as sources from "./views/sources.js";
import * as activity from "./views/activity.js";
import * as briefing from "./views/briefing.js";
import * as receipts from "./views/receipts.js";
import * as playground from "./views/playground.js";
import * as settings from "./views/settings.js";

const TABS = [
  { id: "overview", label: "Overview", icon: "overview", view: overview },
  { id: "records", label: "Records", icon: "records", view: records },
  { id: "gaps", label: "Review", icon: "gaps", view: gaps },
  { id: "briefing", label: "Briefing", icon: "sources", view: briefing },
  { id: "graph", label: "Graph", icon: "graph", view: graph },
  { id: "sources", label: "Sources", icon: "sources", view: sources },
  { id: "activity", label: "Activity", icon: "activity", view: activity },
];

const PAGES = {
  receipts: { title: "Receipts", view: receipts },
  playground: { title: "Playground", view: playground },
  settings: { title: "Settings", view: settings },
};
const state = { meta: null, me: null, projects: [], route: null, current: null, live: false, renderSeq: 0 };
const $ = (id) => document.getElementById(id);

// ------------------------------------------------------------------ routing
function parseRoute() {
  const raw = location.hash.replace(/^#/, "") || "/";
  const [path, query] = raw.split("?");
  const params = new URLSearchParams(query || "");
  const parts = path.split("/").filter(Boolean);
  if (parts[0] === "p" && parts[1]) {
    return { name: "project", pid: decodeURIComponent(parts[1]), tab: parts[2] || "overview", params };
  }
  if (PAGES[parts[0]] && state.me) return { name: parts[0], tab: parts[1] || "", params };
  return { name: "home", params };
}

export function navigate(path, { replace = false } = {}) {
  const url = `#${path}`;
  if (replace) history.replaceState(null, "", url);
  else history.pushState(null, "", url);
  route();
}

function setParam(key, value) {
  const r = parseRoute();
  if (value) r.params.set(key, value); else r.params.delete(key);
  const base = location.hash.replace(/^#/, "").split("?")[0] || "/";
  const q = r.params.toString();
  history.replaceState(null, "", `#${base}${q ? `?${q}` : ""}`);
}

export function openRecord(rid) {
  setParam("r", rid);
  showPanel(rid);
}

export function closePanel() {
  setParam("r", null);
  const panel = $("panel");
  panel.hidden = true;
  clear(panel);
  $("app").classList.remove("with-panel");
  document.querySelectorAll(".grid tr.selected").forEach((tr) => tr.classList.remove("selected"));
}

function showPanel(rid) {
  const panel = $("panel");
  panel.hidden = false;
  $("app").classList.add("with-panel");
  renderPanel(panel, rid, ctxFor(state.route));
  document.querySelectorAll(".grid tr").forEach((tr) => tr.classList.toggle("selected", tr.dataset.id === rid));
}

function ctxFor(r) {
  const project = r?.pid ? state.projects.find((p) => p.id === r.pid) : null;
  const role = state.me?.workspace?.role;
  return {
    meta: state.meta, me: state.me, pid: r?.pid, project, tab: r?.tab, params: r?.params || new URLSearchParams(),
    canWrite: !role || role !== "viewer",  // the local, single-user mode has no roles
    navigate, openRecord, closePanel, refresh, setParam, refreshShell,
  };
}

async function route() {
  const r = parseRoute();
  const prev = state.route;
  state.route = r;
  const sameView = prev && prev.name === r.name && prev.pid === r.pid && prev.tab === r.tab &&
    prev.params.get("import") === r.params.get("import") && prev.params.get("new") === r.params.get("new") &&
    prev.params.get("id") === r.params.get("id");
  if (!sameView) await renderView(r);
  renderSidebar();
  renderTopbar();
  const rid = r.params.get("r");
  if (rid) showPanel(rid); else if (!$("panel").hidden) closePanel();
}

async function renderView(r, { keepScroll = false } = {}) {
  const viewEl = $("view");
  const scroll = viewEl.scrollTop;
  state.current?.cleanup?.();
  state.current = null;
  const seq = ++state.renderSeq;
  const ctx = ctxFor(r);
  const mod = r.name === "home" ? home : PAGES[r.name] ? PAGES[r.name].view
    : (TABS.find((t) => t.id === r.tab) || TABS[0]).view;
  if (r.name === "project" && !ctx.project) {
    clear(viewEl, h("div", { class: "view-inner" }, h("div", { class: "empty" },
      h("strong", {}, "Project not found"), "It may have been deleted. ", h("a", { href: "#/" }, "Back to all projects"))));
    return;
  }
  if (!keepScroll) clear(viewEl, h("div", { class: "view-inner" }, h("div", { class: "skeleton" }, "Loading…")));
  try {
    const out = await mod.render(ctx);
    if (seq !== state.renderSeq) return;
    clear(viewEl, out.el);
    state.current = out;
    if (keepScroll) viewEl.scrollTop = scroll; else viewEl.scrollTop = 0;
  } catch (e) {
    if (seq !== state.renderSeq) return;
    clear(viewEl, h("div", { class: "view-inner" }, h("div", { class: "empty" }, h("strong", {}, "Could not load this view"), e.message)));
  }
}

// Re-render the current view in place (after a local edit or a live change).
export async function refresh({ shell = true } = {}) {
  if (shell) await refreshShell();
  if (state.current?.update) await state.current.update();
  else await renderView(state.route, { keepScroll: true });
  const rid = state.route?.params.get("r");
  if (rid) renderPanel($("panel"), rid, ctxFor(state.route), { quiet: true });
}

async function refreshShell() {
  try { state.projects = (await api.projects()).projects; } catch { /* keep last */ }
  renderSidebar();
  renderTopbar();
}

// ------------------------------------------------------------------ sidebar
function isDark() {
  const theme = document.documentElement.dataset.theme;
  return theme === "dark" || (!theme && matchMedia("(prefers-color-scheme: dark)").matches);
}

function workspaceSwitcher() {
  const me = state.me;
  if (!me) return null;
  const menu = h("div", { class: "ws-menu", hidden: true, role: "menu" },
    me.workspaces.map((w) => h("button", {
      class: `ws-option ${w.id === me.workspace.id ? "current" : ""}`, role: "menuitemradio", type: "button",
      "aria-checked": w.id === me.workspace.id ? "true" : "false",
      onclick: () => { setWorkspace(w.slug); location.hash = "#/"; location.reload(); },
    }, h("span", { class: "label" }, w.name), h("span", { class: "meta" }, w.role))),
    h("button", { class: "ws-option new", type: "button", role: "menuitem", onclick: async () => {
      const name = prompt("Name the new workspace");
      if (!name?.trim()) return;
      try {
        const ws = await api.createWorkspace(name.trim());
        setWorkspace(ws.slug);
        location.hash = "#/";
        location.reload();
      } catch (e) { toast(e.message, { error: true }); }
    } }, icon("plus"), "New workspace"));
  const button = h("button", { class: "ws-button", type: "button", "aria-haspopup": "menu", "aria-expanded": "false",
    onclick: (e) => {
      e.stopPropagation();
      menu.hidden = !menu.hidden;
      button.setAttribute("aria-expanded", String(!menu.hidden));
    } },
    h("span", { class: "label" }, me.workspace.name), icon("down"));
  document.addEventListener("click", () => { menu.hidden = true; button.setAttribute("aria-expanded", "false"); }, { once: true });
  return h("div", { class: "ws-switch" }, button, menu);
}

function renderSidebar() {
  const r = state.route || { name: "home" };
  const brand = state.meta?.brand?.name || document.title;
  const projects = state.projects;
  const me = state.me;
  const page = (id, label, ico) => h("a", { class: `nav-item ${r.name === id ? "active" : ""}`, href: `#/${id}` },
    icon(ico), h("span", { class: "label" }, label));
  clear($("sidebar"),
    h("div", { class: "brand" },
      h("svg", { class: "brand-glyph", viewBox: "0 0 24 24", "aria-hidden": "true" },
        h("path", { d: "M4 19V5l16 14V5" })),
      h("span", {}, brand)),
    workspaceSwitcher(),
    h("button", { class: "search-trigger", onclick: () => openPalette(ctxFor(state.route)) },
      icon("search"), h("span", {}, "Search the record"), h("kbd", {}, "⌘K")),
    h("a", { class: `nav-item ${r.name === "home" ? "active" : ""}`, href: "#/" }, icon("home"), h("span", { class: "label" }, "All projects")),
    me ? [page("receipts", "Receipts", "sources"), page("playground", "Playground", "sparkle")] : null,
    h("div", { class: "nav-section" }, h("span", {}, "Projects"),
      ctxFor(r).canWrite ? h("button", { class: "icon-btn", title: "New project", "aria-label": "New project",
        onclick: () => navigate("/?new=1") }, icon("plus")) : null),
    projects.length ? projects.map((p) => h("a", {
      class: `nav-item ${r.pid === p.id ? "active" : ""}`, href: `#/p/${encodeURIComponent(p.id)}`, title: p.name,
    },
      h("span", { class: "label" }, p.name),
      h("span", { class: "side-trust" }, trustBar(p.status_counts)))) :
      h("div", { class: "nav-item muted" }, "No projects yet"),
    h("div", { class: "sidebar-foot" },
      me ? page("settings", "Settings", "status") : null,
      h("div", { class: "foot-row" },
        h("span", { class: `live-dot ${state.live ? "on" : ""}` }),
        h("span", { class: "label", title: me?.user?.email || state.meta?.db_path || "" },
          me ? me.user.email : state.live ? "Live: watching for changes" : "Offline: reconnecting"),
        me ? h("button", {
          class: "icon-btn", "aria-label": "Sign out", title: `Sign out ${me.user.email}`,
          onclick: async () => {
            await fetch("/logout", { method: "POST" });
            location.href = "/";
          },
        }, icon("logout")) : null,
        h("button", {
          class: "icon-btn", "aria-label": "Toggle theme", title: "Toggle light/dark",
          onclick: () => {
            const next = isDark() ? "light" : "dark";
            document.documentElement.dataset.theme = next;
            try { localStorage.setItem("nx-theme", next); } catch { /* private mode */ }
            renderSidebar();
            refresh({ shell: false });
          },
        }, icon(isDark() ? "sun" : "moon")))));
}

// ------------------------------------------------------------------- topbar
function menuButton() {
  return h("button", { class: "icon-btn menu-btn", type: "button", "aria-label": "Open navigation",
    onclick: () => $("app").classList.toggle("nav-open") }, icon("menu"));
}

function renderTopbar() {
  const r = state.route || { name: "home" };
  const top = $("topbar");
  $("app").classList.remove("nav-open");  // a navigation happened: put the drawer away
  if (r.name !== "project") {
    clear(top, menuButton(), h("div", { class: "crumbs" },
      state.me ? [h("span", {}, state.me.workspace.name), h("span", {}, "/")] : null,
      h("span", { class: "here" }, PAGES[r.name]?.title || "All projects")),
      h("div", { class: "actions" },
        h("button", { class: "btn", onclick: () => openPalette(ctxFor(r)) }, icon("search"), "Search")));
    return;
  }
  const p = state.projects.find((x) => x.id === r.pid);
  const totalGaps = p ? Object.values(p.gap_counts || {}).reduce((a, b) => a + b, 0) : 0;
  const counts = { records: p ? Object.values(p.status_counts || {}).reduce((a, b) => a + b, 0) : 0, gaps: totalGaps };
  clear(top, menuButton(),
    h("div", { class: "crumbs" }, h("a", { href: "#/" }, "Projects"), h("span", {}, "/"),
      h("span", { class: "here" }, p ? p.name : "…")),
    h("nav", { class: "tabs", "aria-label": "Project views" }, TABS.map((t) => h("a", {
      class: `tab ${r.tab === t.id ? "active" : ""}`, href: `#/p/${encodeURIComponent(r.pid)}/${t.id}`,
      "aria-current": r.tab === t.id ? "page" : null,
    }, t.label, counts[t.id] ? h("span", { class: "count" }, counts[t.id]) : null))),
    ctxFor(r).canWrite ? h("div", { class: "actions" },
      h("button", { class: "btn", onclick: () => navigate(`/p/${encodeURIComponent(r.pid)}/sources?import=1`) },
        icon("import"), h("span", { class: "lbl" }, "Import")),
      h("button", { class: "btn primary", onclick: () => openRecord("new"), title: "New record" }, icon("plus"), h("span", { class: "lbl" }, "New record")))
      : h("div", { class: "actions" }, h("span", { class: "pill" }, "Read only")));
}

// --------------------------------------------------------------- live feed
const ACTION_TEXT = {
  created: "saved", updated: "updated", superseded: "superseded", evidence_added: "added evidence to",
  linked: "linked", source_imported: "imported",
};

let refreshTimer = null;

function onChange(data) {
    const mine = state.me?.user?.email || "dashboard";
    const external = (data.activity || []).filter((a) => a.actor !== mine);
    const seen = new Set();
    // Oldest first so "saved" wins over the "linked" that came with the same save.
    for (const a of [...external].reverse().slice(0, 6)) {
      const key = `${a.actor}|${a.record_id || a.detail}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const who = actorName(a);
      toast(`${who} ${ACTION_TEXT[a.action] || a.action} “${a.title}”`, { live: true });
    }
    window.__freshIds = new Set((data.activity || []).map((a) => `${a.at}|${a.action}|${a.record_id}`));
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => refresh(), 250);
}

function connectEvents() {
  if (state.meta?.live === "poll") return pollChanges();
  const es = new EventSource("/api/events");
  es.addEventListener("hello", () => { state.live = true; renderSidebar(); });
  es.addEventListener("change", (ev) => {
    let data = {};
    try { data = JSON.parse(ev.data); } catch { return; }
    onChange(data);
  });
  es.onerror = () => { state.live = false; renderSidebar(); };
  es.onopen = () => { state.live = true; renderSidebar(); };
}

// Hosted: serverless hosts cut long-lived streams, so ask what changed every few seconds instead.
async function pollChanges() {
  let rev = null, since = null;
  const tick = async () => {
    try {
      const res = await api.revision(since);
      if (!state.live) { state.live = true; renderSidebar(); }
      if (rev !== null && res.revision !== rev) onChange(res);
      rev = res.revision;
      since = res.latest || since;
    } catch {
      if (state.live) { state.live = false; renderSidebar(); }
    }
    setTimeout(tick, document.hidden ? 15000 : 4000);
  };
  tick();
}

// -------------------------------------------------------------------- boot
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    openPalette(ctxFor(state.route));
  } else if (e.key === "Escape" && !$("panel").hidden && $("palette").hidden) {
    const tag = document.activeElement?.tagName;
    if (tag !== "INPUT" && tag !== "TEXTAREA" && tag !== "SELECT") closePanel();
  }
});
window.addEventListener("hashchange", route);
$("view").addEventListener("click", () => $("app").classList.remove("nav-open"));
// Model chips and other theme-derived colours follow an OS light/dark switch while the page is open.
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (!document.documentElement.dataset.theme) { renderSidebar(); refresh({ shell: false }); }
});

(async function boot() {
  try {
    state.meta = await api.meta();
    document.title = state.meta.brand.name;
    if (state.meta.workspace) {  // hosted: a signed-in person in one of their workspaces
      state.me = await api.me();
      setWorkspace(state.me.workspace.slug);
    }
    state.projects = (await api.projects()).projects;
  } catch (e) {
    toast(`Could not reach the server: ${e.message}`, { error: true });
  }
  await route();
  connectEvents();
})();

export { KIND };
