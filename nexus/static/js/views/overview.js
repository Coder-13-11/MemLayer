import { api } from "../api.js";
import { h, clear, trustBar, STATUS, STATUS_ORDER, modelChip, statusPill, kindTag, toast, plural, ago } from "../dom.js";
import { feed, gapRow } from "./common.js";

export async function render(ctx) {
  const el = h("div", { class: "view-inner" });
  const base = `/p/${encodeURIComponent(ctx.pid)}`;

  const paint = (d) => {
    const total = d.record_total;
    const supported = d.status_counts.evidence_supported || 0;
    const urgent = d.gaps.filter((g) => g.severity === "high");
    const attention = (urgent.length ? urgent : d.gaps).slice(0, 6);
    const objective = d.project.objective;

    clear(el,
      h("div", { class: "page-head" },
        h("div", { style: { minWidth: 0 } },
          h("h1", {}, d.project.name),
          objective ? h("p", { class: "sub" }, objective) : h("p", { class: "sub muted" }, "No objective yet. ", h("a", { href: "#", onclick: (e) => { e.preventDefault(); editObjective(); } }, "Add one")),
          h("div", { class: "chip-row", style: { marginTop: "12px" } },
            Object.entries(d.model_counts).sort((a, b) => b[1] - a[1]).map(([m, n]) => h("span", { title: `${n} records from ${m}` }, modelChip(m))),
            h("span", { class: "muted", style: { fontSize: "12px", alignSelf: "center" } },
              `${plural(total, "record")} · ${plural(d.relation_total, "link")} · ${plural(d.source_total, "source")}`)))),

      // Signature: how much of what was said has actually been checked.
      h("div", { class: "trust-block" },
        h("div", { class: "trust-caption" },
          h("span", { class: "big" }, total ? [h("span", { class: "num" }, `${supported} of ${total}`), " records are backed by evidence"] : "Nothing saved yet"),
          total ? h("span", { class: "muted", style: { fontSize: "12px" } }, "Select a segment to see those records") : null),
        trustBar(d.status_counts, { large: true, onPick: (s) => ctx.navigate(`${base}/records?status=${s}`) }),
        h("div", { class: "trust-legend" }, STATUS_ORDER.filter((s) => d.status_counts[s]).map((s) =>
          h("button", { onclick: () => ctx.navigate(`${base}/records?status=${s}`) },
            h("span", { class: "swatch", style: { background: STATUS[s].color } }), STATUS[s].label, h("b", {}, d.status_counts[s]))))),

      h("div", { class: "cols section" },
        h("div", {},
          h("div", { class: "section-head" }, h("h2", {}, urgent.length ? "Needs attention" : "Open gaps"),
            h("span", { class: "meta" }, d.gaps.length), h("a", { class: "more", href: `#${base}/gaps` }, "All gaps")),
          attention.length ? attention.map((g) => gapRow(g, ctx, {}, { compact: true }))
            : h("div", { class: "empty" }, h("strong", {}, "No gaps found"), "Every claim has evidence and nothing conflicts, as far as the rules can tell.")),
        h("div", {},
          h("div", { class: "section-head" }, h("h2", {}, "Activity"), h("a", { class: "more", href: `#${base}/activity` }, "Full history")),
          feed(d.activity, ctx))),

      h("div", { class: "cols section" },
        list("Decisions", d.decisions, ctx, `${base}/records?kind=decision`, "No decisions recorded."),
        list("Open questions", d.open_questions, ctx, `${base}/records?kind=question`, "No open questions.")));
  };

  const editObjective = () => {
    const ta = h("textarea", { class: "textarea", style: { minHeight: "70px" } }, data.project.objective || "");
    const form = h("form", { class: "inline-form" }, h("label", { class: "field" }, h("span", {}, "Objective"), ta),
      h("div", { class: "btns" }, h("button", { type: "button", class: "btn ghost sm", onclick: () => paint(data) }, "Cancel"),
        h("button", { class: "btn primary sm" }, "Save")));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      try { await api.updateProject(ctx.pid, { objective: ta.value }); toast("Objective saved"); await update(); }
      catch (ex) { toast(ex.message, { error: true }); }
    });
    el.querySelector(".page-head .sub").replaceWith(form);
    ta.focus();
  };

  let data = await api.project(ctx.pid);
  paint(data);
  const update = async () => { data = await api.project(ctx.pid); paint(data); };
  return { el, update };
}

function list(title, items, ctx, href, emptyText) {
  return h("div", {},
    h("div", { class: "section-head" }, h("h2", {}, title), h("span", { class: "meta" }, items.length), h("a", { class: "more", href: `#${href}` }, "View all")),
    items.length ? h("div", {}, items.map((r) => h("button", { class: "list-row", onclick: () => ctx.openRecord(r.id) },
      h("span", { class: "t" }, r.title), statusPill(r.status), h("span", { class: "meta" }, ago(r.updated_at)))))
      : h("div", { class: "empty" }, emptyText));
}

export { kindTag };
