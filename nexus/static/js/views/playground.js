// Send one message through the gateway and see exactly what the model was handed.
import { api } from "../api.js";
import { h, add, clear } from "../dom.js";
import { pageHead, onSubmit, field, notice } from "./ui.js";

export async function render(ctx) {
  const el = h("div", { class: "view-inner" });
  const { projects } = await api.projects();
  const providers = ctx.me?.workspace?.role && ["admin", "owner"].includes(ctx.me.workspace.role)
    ? (await api.providers()).credentials : [];
  const out = h("div", { class: "pg-out", "aria-live": "polite" });
  const answerText = (wire, d) => wire === "messages" ? (d.content || []).map((c) => c.text || "").join("")
    : wire === "responses" ? (d.output || []).flatMap((o) => o.content || []).map((c) => c.text || "").join("")
      : d.choices?.[0]?.message?.content || "";

  const form = onSubmit(h("form", { class: "pg-form" },
    h("div", { class: "row" },
      field("Project", h("select", { class: "select", name: "project" },
        h("option", { value: "" }, "None (no briefing added)"),
        projects.map((p) => h("option", { value: p.id, selected: p.id === ctx.params.get("project") || projects.length === 1 }, p.name)))),
      field("Records included", h("select", { class: "select", name: "context" },
        h("option", { value: "all" }, "Every record, labelled"),
        h("option", { value: "confirmed" }, "Only confirmed or evidenced")))),
    h("div", { class: "row" },
      field("Model", h("input", { class: "input", name: "model", value: "mock-1", required: true }),
        providers.length ? `Stored provider keys: ${providers.map((p) => p.provider).join(", ")}. Type one of their model names to call it.`
          : "mock-1 answers inside NEXUS and calls no provider. Add a provider key under Settings to use a real model."),
      field("Wire format", h("select", { class: "select", name: "wire" },
        h("option", { value: "chat" }, "Chat Completions"), h("option", { value: "messages" }, "Anthropic Messages"),
        h("option", { value: "responses" }, "Responses")))),
    field("Message", h("textarea", { class: "textarea", name: "message", required: true, rows: 3 }, "Should I add Jest tests for the cart reducer?")),
    h("div", { class: "btns" }, h("button", { class: "btn primary", type: "submit" }, "Send through the gateway"))),
  async (data) => {
    clear(out, h("p", { class: "muted" }, "Sending…"));
    let res;
    try { res = await api.playground(data); } catch (e) { clear(out); throw e; }
    const sent = res.request_sent;
    const briefing = (sent.messages || []).find((m) => m.role === "system")?.content
      || (Array.isArray(sent.system) ? sent.system.map((b) => b.text).join("\n") : sent.system) || sent.instructions || "";
    clear(out,
      h("div", { class: "pg-cols" },
        h("div", {}, h("h2", {}, "What the model was handed"),
          res.receipt_id ? h("p", { class: "meta" }, `${res.context_records} records, ~${res.context_tokens.toLocaleString()} tokens. `,
            h("a", { href: `#/receipts?id=${res.receipt_id}` }, `Receipt ${res.receipt_id}`)) : h("p", { class: "meta" }, "No project was chosen, so nothing was added."),
          briefing ? h("pre", { class: "briefing-text short" }, briefing) : null),
        h("div", {}, h("h2", {}, "What it answered"),
          h("p", { class: "meta" }, `Provider: ${res.provider}`),
          h("pre", { class: "answer" }, answerText(data.wire, res.response) || JSON.stringify(res.response, null, 2)),
          res.provider === "mock" ? notice("The mock model is not an AI. It repeats back what it was given so you can see the briefing arrive.") : null)));
  });

  add(el, pageHead("Playground", "Send a message through the gateway as a coding tool would, and see the briefing that is "
    + "added to it and the receipt it leaves. Nothing typed here is stored."), form, out);
  return { el };
}
