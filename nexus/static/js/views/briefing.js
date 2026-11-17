// What a model is handed about this project, and a check of a plan against it.
import { api } from "../api.js";
import { h, add, clear, ago, statusPill, toast } from "../dom.js";
import { copyText, section, onSubmit, notice, num } from "./ui.js";

const VERDICT = {
  conflict: { label: "Conflicts with a recorded decision", cls: "bad" },
  resembles_replaced: { label: "Resembles something the team replaced", cls: "bad" },
  review: { label: "Related records to read", cls: "warn" },
  clear: { label: "Nothing found", cls: "ok" },
};

export async function render(ctx) {
  const el = h("div", { class: "view-inner" });
  const state = { budget: 1200, policy: "all" };
  const block = h("pre", { class: "briefing-text", tabindex: "0", "aria-label": "Briefing text" });
  const facts = h("div", { class: "briefing-facts" });
  const receipts = h("div");

  async function load() {
    const out = await api.briefing(ctx.pid, { budget: state.budget, policy: state.policy, receipt: 0 });
    block.textContent = out.text;
    const left = Object.values(out.omitted || {}).reduce((a, b) => a + b, 0);
    clear(facts,
      h("span", {}, h("b", {}, num(out.record_refs.length)), " records"),
      h("span", {}, h("b", {}, `~${num(out.token_estimate)}`), " tokens"),
      left ? h("span", { class: "warn" }, `${left} left out for length`) : h("span", {}, "nothing left out"),
      h("button", { class: "btn sm", type: "button", onclick: () => copyText(out.text, "Briefing copied") }, "Copy"));
  }

  async function loadReceipts() {
    if (!ctx.me) return;
    const list = (await api.receipts({ project: ctx.pid, limit: 8 })).receipts;
    clear(receipts, list.length ? list.map((r) => h("a", { class: "list-row", href: `#/receipts?id=${r.id}` },
      h("span", { class: "mono" }, r.id),
      h("span", { class: "grow" }, `${r.actor || "unknown"}${r.model ? ` · ${r.model}` : ""}`),
      h("span", { class: "meta" }, `${r.record_count} records · ${r.channel} · ${ago(r.at)}`)))
      : h("p", { class: "empty" }, "No model has been handed this project yet. A receipt appears here each time one is, ",
        "through a connected app, the gateway or the API."));
  }

  const controls = h("div", { class: "toolbar" },
    h("label", { class: "inline" }, "Size",
      h("select", { class: "select", onchange: (e) => { state.budget = Number(e.target.value); load(); } },
        [[600, "Small (600 tokens)"], [1200, "Standard (1,200)"], [2400, "Large (2,400)"], [6000, "Everything that fits (6,000)"]]
          .map(([v, l]) => h("option", { value: v, selected: v === state.budget }, l)))),
    h("label", { class: "inline" }, "Include",
      h("select", { class: "select", onchange: (e) => { state.policy = e.target.value; load(); } },
        h("option", { value: "all" }, "Every record, labelled"),
        h("option", { value: "confirmed" }, "Only records a person confirmed"))));

  // ---- plan check
  const results = h("div", { class: "check-results", "aria-live": "polite" });
  const form = onSubmit(h("form", { class: "check-form" },
    h("label", { class: "field" }, h("span", {}, "What is about to be done, one step per line"),
      h("textarea", { class: "textarea", name: "plan", required: true, rows: 3,
        placeholder: "Add Jest tests for the cart reducer\nCall the payment SDK from the refund handler" })),
    h("div", { class: "btns" }, h("button", { class: "btn primary sm", type: "submit" }, "Check against the record"))),
  async (data) => {
    const statements = data.plan.split("\n").map((s) => s.trim()).filter(Boolean);
    const out = await api.check(ctx.pid, statements);
    clear(results, out.results.map((r) => {
      const v = VERDICT[r.verdict];
      const line = (x, lead) => h("button", { class: "check-hit", type: "button", onclick: () => ctx.openRecord(x.id) },
        h("span", { class: "lead" }, lead), h("span", { class: "grow" }, x.title),
        x.confirmed ? h("span", { class: "confirmed-mark" }, "confirmed") : statusPill(x.status));
      return h("div", { class: `check-row ${v.cls}` },
        h("div", { class: "check-head" }, h("span", { class: "verdict" }, v.label), h("span", { class: "stmt" }, r.statement)),
        r.conflicts.map((c) => [line(c, "Recorded"),
          h("p", { class: "check-note" }, `The record says ${c.subject} is “${c.recorded_value}”. This says “${c.stated_value}”.`)]),
        r.resembles_replaced.map((x) => [line(x, x.status === "retracted" ? "Withdrawn" : "Replaced"),
          x.replaced_by ? line(x.replaced_by, "Now") : null]),
        r.related.map((x) => line(x, "Related")),
        r.verdict === "clear" ? h("p", { class: "check-note" }, "Nothing in the record matches. That is not confirmation it is right.") : null);
    }), notice(`${out.method}. ${out.limitations[1]}`));
  });

  add(el, 
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Briefing"),
      h("p", { class: "sub" }, "This is the text an AI tool is handed for this project when it does not ask through tools: ",
        "at the start of a coding-agent session, or added to a request by the gateway. Decisions come first, replaced ",
        "records are listed so they are not proposed again, and every line carries how far it was checked."))),
    controls, facts, block,
    section("Check a plan", null,
      h("p", { class: "section-lede" }, "Before an agent acts, compare what it intends to do with what is already settled. ",
        "Connected apps do this with the check_plan tool; this is the same check."),
      form, results),
    ctx.me ? section("Receipts for this project", null, receipts) : null);

  try { await load(); await loadReceipts(); } catch (e) { toast(e.message, { error: true }); }
  return { el, update: async () => { await load(); await loadReceipts(); } };
}
