import { api } from "../api.js";
import { h, clear, icon, GAP_LABEL, toast, plural } from "../dom.js";
import { gapRow, CONF_HELP } from "./common.js";

export async function render(ctx) {
  const el = h("div", { class: "view-inner" });
  let typeFilter = "";

  const paint = (report) => {
    const gaps = typeFilter ? report.gaps.filter((g) => g.type === typeFilter) : report.gaps;
    const bySev = { high: [], medium: [], low: [] };
    gaps.forEach((g) => bySev[g.severity].push(g));
    const name = ctx.project?.name || "";
    const server = ctx.meta?.mcp?.server_name || "memlayer";

    clear(el,
      h("div", { class: "page-head" },
        h("div", {}, h("h1", {}, "Gaps"),
          h("p", { class: "sub" }, "Contradictions, missing evidence, untested assumptions, broken dependencies and open questions. Each one links the records it is based on."))),
      h("div", { class: "filters" },
        h("button", { class: `btn sm ${typeFilter ? "" : "primary"}`, onclick: () => { typeFilter = ""; paint(report); } }, `All ${report.gaps.length}`),
        Object.entries(report.counts).map(([t, n]) => h("button", {
          class: `btn sm ${typeFilter === t ? "primary" : ""}`, onclick: () => { typeFilter = t; paint(report); },
        }, `${GAP_LABEL[t] || t} ${n}`))),
      ["high", "medium", "low"].map((sev) => bySev[sev].length ? h("div", { class: "section" },
        h("div", { class: "section-head" }, h("h2", {}, `${sev[0].toUpperCase()}${sev.slice(1)} severity`), h("span", { class: "meta" }, bySev[sev].length)),
        bySev[sev].map((g) => gapRow(g, ctx))) : null),
      !gaps.length ? h("div", { class: "empty" }, h("strong", {}, "No gaps of this kind"), "The rules found nothing to flag.") : null,

      report.resolved_conflicts.length ? h("div", { class: "section" },
        h("div", { class: "section-head" }, h("h2", {}, "Already resolved"), h("span", { class: "meta" }, report.resolved_conflicts.length)),
        h("p", { class: "muted", style: { fontSize: "13px", margin: "8px 0" } }, "These conflicts were settled by superseding or retracting one side, so they are not counted as gaps."),
        report.resolved_conflicts.map((r) => h("div", { class: "gap compact" },
          h("span", { class: "sev", style: { background: "var(--st-supported)" } }),
          h("div", { class: "summary" }, r.resolution),
          h("div", { class: "refs" }, r.records.map((x) => h("button", { class: "rec-link", onclick: () => ctx.openRecord(x.id) }, h("span", { class: "t" }, x.title))))))) : null,

      h("div", { class: "section connect", style: { marginTop: "40px" } },
        h("div", {},
          h("h2", {}, "How these are found"),
          h("p", {}, report.method),
          h("ul", { style: { margin: 0, paddingLeft: "18px", color: "var(--text-2)", fontSize: "13px" } },
            Object.entries(CONF_HELP).map(([k, v]) => h("li", {}, h("strong", {}, k), `: ${v}`)),
            report.limitations.map((l) => h("li", {}, l)))),
        h("div", {},
          h("h2", {}, "Get an AI review"),
          h("p", {}, "Contradictions that only show in the wording need a model to read them. In Claude Code, run this prompt. Claude records what it finds as links in memory, and they show up on this page."),
          h("div", { class: "code" }, h("span", {}, `/mcp__${server}__review_project "${name}"`),
            h("button", { class: "icon-btn", "aria-label": "Copy", onclick: () => navigator.clipboard?.writeText(`/mcp__${server}__review_project "${name}"`).then(() => toast("Copied")) }, icon("copy"))),
          h("p", { class: "muted", style: { fontSize: "12px", marginTop: "8px" } }, "Nothing is sent anywhere from this page. The review runs in your own AI client."))));
  };

  let report = await api.gaps(ctx.pid);
  paint(report);
  return { el, update: async () => { report = await api.gaps(ctx.pid); paint(report); } };
}

export { plural };
