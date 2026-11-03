import { h, icon, ago, stamp, GAP_LABEL, SEV_COLOR, KIND, modelChip } from "../dom.js";

const ACTION = {
  created: ["plus", "saved"],
  updated: ["edit", "updated"],
  superseded: ["replace", "superseded"],
  evidence_added: ["evidence", "added evidence to"],
  linked: ["link", "linked"],
  source_imported: ["import", "imported source"],
};

export function feed(items, ctx, { showProject = false } = {}) {
  if (!items.length) return h("div", { class: "empty" }, h("strong", {}, "No activity yet"), "Saves from any AI client or this dashboard appear here as they happen.");
  const fresh = window.__freshIds || new Set();
  return h("ul", { class: "feed" }, items.map((a) => {
    const [ic, verb] = ACTION[a.action] || ["records", a.action];
    const actor = actorName(a);
    const key = `${a.at}|${a.action}|${a.record_id}`;
    return h("li", { class: fresh.has(key) ? "fresh" : "" },
      h("span", { class: "ic" }, icon(ic)),
      h("div", { class: "what" },
        h("span", {}, modelChip(actor), " ", verb, " "),
        a.record_id
          ? h("span", { class: "t", role: "link", tabindex: "0",
            onclick: () => (showProject ? ctx.navigate(`/p/${encodeURIComponent(a.project_id)}/records?r=${a.record_id}`) : ctx.openRecord(a.record_id)),
            onkeydown: (e) => { if (e.key === "Enter") e.currentTarget.click(); } }, a.title)
          : h("a", { href: `#/p/${encodeURIComponent(a.project_id)}/sources?s=${a.detail}` }, a.title || "source"),
        a.detail && a.action !== "source_imported" ? h("span", { class: "d" }, a.detail) : null,
        showProject && a.project ? h("span", { class: "d" }, a.project) : null),
      h("time", { datetime: a.at, title: stamp(a.at) }, ago(a.at)));
  }));
}

// Who did it, in the user's terms: the model for AI writes and imports, "you" for the dashboard and CLI.
export function actorName(a) {
  const raw = (a.actor || "").replace(/^mcp:/, "");
  if (!raw || ["import_source", "importer", "cli:import"].includes(raw)) return a.origin_model || "import";
  if (raw === "dashboard" || raw.startsWith("cli")) return "you";
  return raw;
}

export function gapRow(g, ctx, recordsById = {}, { compact = false } = {}) {
  return h("div", { class: `gap ${compact ? "compact" : ""}` },
    h("span", { class: "sev", style: { background: SEV_COLOR[g.severity] }, title: `${g.severity} severity` }),
    h("div", { class: "head" },
      h("span", { class: "type" }, GAP_LABEL[g.type] || g.type),
      h("span", { class: `conf ${g.confidence}`, title: CONF_HELP[g.confidence] }, g.confidence),
      h("span", {}, `${g.severity} severity`)),
    h("div", { class: "summary" }, g.summary),
    h("div", { class: "refs" }, (g.records || g.record_ids.map((id) => recordsById[id] || { id })).map((r) =>
      h("button", { class: "rec-link", onclick: () => ctx.openRecord(r.id), title: r.title || r.id },
        r.kind ? h("i", { style: { width: "7px", height: "7px", borderRadius: "2px", background: KIND[r.kind]?.color, flex: "none" } }) : null,
        h("span", { class: "t" }, r.title || r.id)))),
    compact ? null : h("div", { class: "action" }, g.suggested_action));
}

export const CONF_HELP = {
  certain: "A structural fact about the stored data",
  stated: "Someone explicitly recorded this conflict",
  possible: "A heuristic match; check it before acting",
};
