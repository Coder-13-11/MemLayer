// Context receipts: exactly which records a model was handed, and what has changed since.
import { api } from "../api.js";
import { h, add, clear, ago, stamp, plural } from "../dom.js";
import { pageHead } from "./ui.js";

const CHANNEL = { gateway: "Gateway", mcp: "Connected app", api: "API", app: "This app" };

export async function render(ctx) {
  const el = h("div", { class: "view-inner receipts" });
  const detail = h("div", { class: "receipt-slot" });
  const list = h("div", { class: "receipt-list" });

  async function show(id) {
    if (!id) return clear(detail, h("p", { class: "empty" }, h("strong", {}, "Choose a receipt"),
      "Each one lists the records a model was given, at the versions it saw."));
    clear(detail, h("p", { class: "muted" }, "Loading…"));
    let r;
    try { r = await api.receipt(id); } catch (e) { return clear(detail, h("p", { class: "empty" }, h("strong", {}, "Receipt not available"), e.message)); }
    const changed = r.record_refs.filter((x) => x.changed_since);
    const line = (x) => h("button", {
      class: `rc-line ${x.changed_since ? "changed" : ""}`, type: "button", disabled: x.current_status === "deleted",
      onclick: () => { location.hash = `#/p/${encodeURIComponent(r.project_id)}/records?r=${x.id}`; },
    },
      h("span", { class: "rc-title" }, x.title || "(deleted record)"),
      h("span", { class: "rc-dots", "aria-hidden": "true" }),
      h("span", { class: "rc-val" }, `v${x.version} ${x.confirmed ? "confirmed" : x.status.replace("_", " ")}`),
      x.changed_since ? h("span", { class: "rc-since" },
        x.current_status === "deleted" ? "deleted since" : `now v${x.current_version}, ${x.current_status.replace("_", " ")}`) : null);
    clear(detail, h("article", { class: "receipt", "aria-label": `Receipt ${r.id}` },
      h("header", {},
        h("div", { class: "rc-brand" }, ctx.meta?.brand?.name || "NEXUS"),
        h("div", { class: "rc-kind" }, "Context receipt"),
        h("div", { class: "rc-no" }, "No. ", h("span", {}, r.id.replace("rcp_", "").toUpperCase()))),
      h("dl", { class: "rc-meta" },
        h("dt", {}, "Project"), h("dd", {}, r.project_name),
        h("dt", {}, "Handed to"), h("dd", {}, r.actor || "unknown"),
        r.model ? [h("dt", {}, "Model"), h("dd", {}, r.model)] : null,
        h("dt", {}, "Through"), h("dd", {}, CHANNEL[r.channel] || r.channel),
        h("dt", {}, "When"), h("dd", {}, stamp(r.at)),
        r.query ? [h("dt", {}, "Asked about"), h("dd", {}, r.query)] : null),
      h("div", { class: "rc-rule" }),
      r.record_refs.length ? r.record_refs.map(line) : h("p", { class: "muted" }, "No records were included."),
      h("div", { class: "rc-rule" }),
      h("div", { class: "rc-total" }, h("span", {}, plural(r.record_refs.length, "record")), h("span", {}, `~${r.token_estimate.toLocaleString()} tokens`)),
      h("p", { class: `rc-foot ${changed.length ? "changed" : ""}` }, changed.length
        ? `${plural(changed.length, "record")} changed after this model read ${changed.length === 1 ? "it" : "them"}. What it was told is no longer current.`
        : "Every record is still as the model saw it."),
      h("p", { class: "rc-hash", title: "SHA-256 of the exact text handed over" }, `sha256 ${r.briefing_sha256.slice(0, 32)}…`)));
    list.querySelectorAll("a").forEach((a) => a.classList.toggle("active", a.dataset.id === id));
  }

  async function load() {
    const { receipts } = await api.receipts({ limit: 100 });
    clear(list, receipts.length ? receipts.map((r) => h("a", {
      class: "list-row", href: `#/receipts?id=${r.id}`, dataset: { id: r.id },
    },
      h("span", { class: "grow" }, h("strong", {}, r.project_name), h("br"),
        h("span", { class: "meta" }, `${r.actor || "unknown"}${r.model ? ` · ${r.model}` : ""}`)),
      h("span", { class: "meta right" }, `${r.record_count} records`, h("br"), ago(r.at))))
      : h("p", { class: "empty" }, h("strong", {}, "No receipts yet"),
        "A receipt is written each time a model is handed a project: when a connected app asks for context, when the ",
        "gateway adds a briefing, or when you try the playground."));
    return receipts;
  }

  add(el, pageHead("Receipts", "What each model was told, itemised. When an agent does something surprising, start here: "
    + "the receipt shows the records it was given and whether any have changed since."),
    h("div", { class: "split" }, list, detail));
  const receipts = await load();
  await show(ctx.params.get("id") || receipts[0]?.id);
  return { el, update: async () => { await load(); } };
}
