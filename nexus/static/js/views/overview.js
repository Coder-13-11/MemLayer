// Project overview: what this project is, what needs a person, what is in force. Detail lives in the other tabs.
import { api } from "../api.js";
import { h, clear, trustBar, icon, toast, plural, ago, GAP_LABEL, SEV_COLOR } from "../dom.js";
import { feed } from "./common.js";

export async function render(ctx) {
  const el = h("div", { class: "view-inner calm" });
  const base = `/p/${encodeURIComponent(ctx.pid)}`;

  const paint = (d) => {
    const total = d.record_total;
    const supported = d.status_counts.evidence_supported || 0;
    const urgent = d.gaps.filter((g) => g.severity === "high");
    const attention = (urgent.length ? urgent : d.gaps).slice(0, 3);
    const waiting = urgent.length || d.gaps.length;
    const objective = d.project.objective;

    clear(el,
      h("div", { class: "page-head" },
        h("div", { style: { minWidth: 0 } },
          h("h1", {}, d.project.name),
          objective ? h("p", { class: "sub" }, objective)
            : ctx.canWrite ? h("p", { class: "sub muted" }, "No objective yet. ",
              h("a", { href: "#", onclick: (e) => { e.preventDefault(); editObjective(); } }, "Add one")) : null)),

      total ? h("div", { class: "summary" },
        h("p", {}, h("b", {}, plural(total, "entry", "entries")), ". ",
          `${d.confirmed_total} confirmed by a person, ${supported} backed by evidence, `,
          `${d.status_counts.unverified || 0} not yet checked.`),
        trustBar(d.status_counts, { large: true, onPick: (s) => ctx.navigate(`${base}/records?status=${s}`) }))
        : h("div", { class: "empty-block" },
          h("strong", {}, "Nothing recorded yet"),
          h("p", {}, "Add the first decision yourself, or connect an AI tool and ask it to save what you decide."),
          ctx.canWrite ? h("div", { class: "btns" },
            h("button", { class: "btn primary", onclick: () => ctx.openRecord("new") }, icon("plus"), "Add an entry"),
            h("a", { class: "btn", href: "#/settings/connect" }, "Connect a tool")) : null),

      total ? h("div", { class: "section" },
        h("div", { class: "section-head" }, h("h2", {}, waiting ? "Needs a person" : "Nothing needs you"),
          waiting ? h("span", { class: "meta" }, waiting) : null,
          waiting > attention.length ? h("a", { class: "more", href: `#${base}/gaps` }, `Review all ${waiting}`) : null),
        attention.length ? attention.map((g) => h("a", { class: "need-row", href: `#${base}/gaps` },
          h("span", { class: "sev", style: { background: SEV_COLOR[g.severity] } }),
          h("span", { class: "what" }, h("span", { class: "type" }, GAP_LABEL[g.type] || g.type), g.summary)))
          : h("p", { class: "empty" }, "No conflicts and nothing unproven that something depends on, as far as the rules can tell.")) : null,

      d.decisions.length ? h("div", { class: "section" },
        h("div", { class: "section-head" }, h("h2", {}, "Decisions in force"), h("span", { class: "meta" }, d.decisions.length),
          h("a", { class: "more", href: `#${base}/records?kind=decision` }, "All decisions")),
        d.decisions.slice(0, 5).map((r) => h("button", { class: "list-row", onclick: () => ctx.openRecord(r.id) },
          h("span", { class: "grow t" }, r.title),
          r.confirmed ? h("span", { class: "confirmed-mark" }, icon("check"), "Confirmed") : h("span", { class: "meta" }, "Not confirmed")))) : null,

      d.activity.length ? h("details", { class: "section fold" },
        h("summary", {}, h("h2", {}, "Recent activity"), h("span", { class: "meta" }, `last change ${ago(d.activity[0].at)}`)),
        feed(d.activity.slice(0, 6), ctx),
        h("a", { class: "more-link", href: `#${base}/activity` }, "Full history")) : null);
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
