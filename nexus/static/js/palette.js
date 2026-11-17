// ⌘K universal search across every project: projects, records and original sources.
import { api } from "./api.js";
import { h, clear, icon, statusPill, KIND } from "./dom.js";

let lastQuery = "";

export function openPalette(ctx) {
  const root = document.getElementById("palette");
  if (!root.hidden) return;
  const input = h("input", { type: "text", placeholder: "Search findings, decisions, sources, projects…", value: lastQuery,
    "aria-label": "Search memory", role: "combobox", "aria-expanded": "true", "aria-controls": "palette-results" });
  const results = h("div", { class: "res", id: "palette-results", role: "listbox" });
  let items = [];
  let sel = 0;
  let timer = null;
  let seq = 0;

  const close = () => { root.hidden = true; clear(root); document.removeEventListener("keydown", onKey, true); };
  const choose = (it) => { close(); it.go(); };

  const paint = () => {
    results.querySelectorAll(".it").forEach((el, i) => {
      el.classList.toggle("sel", i === sel);
      el.setAttribute("aria-selected", i === sel ? "true" : "false");
      if (i === sel) el.scrollIntoView({ block: "nearest" });
    });
  };

  const render = (groups) => {
    items = [];
    const nodes = [];
    for (const [label, list] of groups) {
      if (!list.length) continue;
      nodes.push(h("div", { class: "grp" }, label));
      for (const it of list) {
        const idx = items.length;
        items.push(it);
        nodes.push(h("div", { class: "it", role: "option", onclick: () => choose(it), onmousemove: () => { if (sel !== idx) { sel = idx; paint(); } } },
          it.lead, h("div", { class: "t" }, it.title, it.sub ? h("span", { class: "s" }, it.sub) : null), it.tail));
      }
    }
    if (!nodes.length) nodes.push(h("div", { class: "empty", style: { padding: "24px 12px" } },
      input.value.trim() ? "Nothing matches. Try fewer or different words." : "Type to search everything every AI has saved."));
    sel = 0;
    clear(results, nodes);
    paint();
  };

  const snippetNode = (s) => {
    // Snippets come back with [brackets] around matches; render them as <mark> without using innerHTML.
    if (!s) return null;
    const out = [];
    s.split(/(\[[^\]]*\])/).forEach((part) => {
      if (part.startsWith("[") && part.endsWith("]")) out.push(h("mark", {}, part.slice(1, -1)));
      else if (part) out.push(part);
    });
    return out;
  };

  const search = async () => {
    const q = input.value.trim();
    lastQuery = q;
    const mine = ++seq;
    if (!q) {
      const recent = (ctx.meta && (await api.activity({ limit: 8 }).catch(() => ({ activity: [] })))) || { activity: [] };
      if (mine !== seq) return;
      const seen = new Set();
      render([["Recently changed", recent.activity.filter((a) => a.record_id && !seen.has(a.record_id) && seen.add(a.record_id)).map((a) => ({
        title: a.title, sub: `${a.project} · ${(a.actor || "").replace(/^mcp:/, "")}`,
        lead: h("i", { style: { width: "8px", height: "8px", borderRadius: "2px", background: KIND[a.kind]?.color, flex: "none" } }),
        go: () => ctx.navigate(`/p/${encodeURIComponent(a.project_id)}/records?r=${a.record_id}`),
      }))]]);
      return;
    }
    const res = await api.search(q).catch(() => null);
    if (mine !== seq || !res) return;
    render([
      ["Projects", res.projects.map((p) => ({ title: p.name, sub: p.description, lead: icon("layers"), go: () => ctx.navigate(`/p/${encodeURIComponent(p.id)}`) }))],
      ["Records", res.results.map((r) => ({
        title: r.title, sub: snippetNode(r.snippet), tail: [statusPill(r.status), h("span", { class: "p" }, r.project)],
        lead: h("i", { style: { width: "8px", height: "8px", borderRadius: "2px", background: KIND[r.kind]?.color, flex: "none" }, title: r.kind }),
        go: () => ctx.navigate(`/p/${encodeURIComponent(r.project_id)}/records?r=${r.id}`),
      }))],
      ["Original sources", res.source_matches.map((s) => ({
        title: s.title || s.id, sub: snippetNode(s.snippet), lead: icon("sources"), tail: h("span", { class: "p" }, s.origin_model || ""),
        go: () => ctx.navigate(`/p/${encodeURIComponent(s.project_id)}/sources?s=${s.id}`),
      }))],
    ]);
  };

  function onKey(e) {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); sel = Math.min(items.length - 1, sel + 1); paint(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); sel = Math.max(0, sel - 1); paint(); }
    else if (e.key === "Enter" && items[sel]) { e.preventDefault(); choose(items[sel]); }
  }

  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(search, 120); });
  clear(root, h("div", { class: "palette", role: "dialog", "aria-label": "Search", onclick: (e) => e.stopPropagation() },
    h("div", { class: "q" }, icon("search"), input, h("kbd", {}, "esc")),
    results,
    h("div", { class: "foot" }, h("span", {}, "↑↓ to move"), h("span", {}, "↵ to open"), h("span", {}, "Searches titles, details and original source text"))));
  root.onclick = close;
  root.hidden = false;
  document.addEventListener("keydown", onKey, true);
  input.focus();
  input.select();
  search();
}
