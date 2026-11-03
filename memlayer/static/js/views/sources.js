import { api } from "../api.js";
import { h, clear, icon, ago, stamp, statusPill, toast, plural, KIND } from "../dom.js";

export async function render(ctx) {
  const el = h("div", { class: "view-inner" });
  const listSlot = h("div");
  const bodySlot = h("div");
  let sources = [];
  let current = ctx.params.get("s") || null;

  const paintList = () => clear(listSlot,
    sources.length ? sources.map((s) => h("button", {
      class: `src-row ${current === s.id ? "active" : ""}`, onclick: () => { current = s.id; ctx.setParam("s", s.id); ctx.setParam("import", null); paintList(); showSource(); },
    },
      h("div", { class: "t" }, s.title || s.id),
      h("div", { class: "m" }, [s.origin_model, s.kind.replace("_", " "), plural(s.record_count, "record"), ago(s.imported_at)].filter(Boolean).join(" · "))))
      : h("div", { class: "empty" }, "No sources yet."));

  const showSource = async () => {
    if (!current) return showImport();
    clear(bodySlot, h("div", { class: "skeleton" }, "Loading…"));
    try {
      const s = await api.source(current);
      clear(bodySlot,
        h("h2", { style: { fontSize: "18px", fontWeight: 600 } }, s.title || s.id),
        h("p", { class: "muted", style: { margin: "6px 0 0", fontSize: "13px" } },
          [s.origin_model && `from ${s.origin_model}`, s.conversation_id && `conversation ${s.conversation_id}`, `imported ${stamp(s.imported_at)}`].filter(Boolean).join(" · ")),
        s.records.length ? h("div", { class: "section", style: { marginTop: "20px" } },
          h("div", { class: "section-head" }, h("h2", {}, "Records extracted from it"), h("span", { class: "meta" }, s.records.length)),
          s.records.map((r) => h("button", { class: "list-row", onclick: () => ctx.openRecord(r.id) },
            h("i", { style: { width: "8px", height: "8px", borderRadius: "2px", background: KIND[r.kind]?.color, flex: "none" } }),
            h("span", { class: "t" }, r.title), statusPill(r.status)))) : null,
        h("div", { class: "section-head", style: { marginTop: "28px" } }, h("h2", {}, "Original text"),
          h("span", { class: "meta" }, "Stored exactly as imported; never edited")),
        h("pre", { class: "src-body" }, s.content));
    } catch (e) {
      clear(bodySlot, h("div", { class: "empty" }, h("strong", {}, "Source not found"), e.message));
    }
  };

  const showImport = () => {
    current = null;
    paintList();
    const err = h("p", { class: "form-error", hidden: true });
    const form = h("form", {},
      h("label", { class: "field" }, h("span", {}, "Paste a conversation or summary"),
        h("textarea", { class: "textarea tall", name: "text", required: true, placeholder: "Paste text from ChatGPT, Claude or anywhere else.\n\nPlain text is stored as a searchable source. Text in the structured format (## Findings, - [F1] Title, status:, evidence:, depends_on: …) also becomes individual records. See samples/ for an example." }),
        h("span", { class: "hint" }, "The full text is kept exactly as pasted. Nothing in it is run or followed as an instruction.")),
      h("div", { class: "row" },
        h("label", { class: "field" }, h("span", {}, "Which AI wrote it?"), h("input", { class: "input", name: "origin_model", placeholder: "e.g. chatgpt", list: "origins-import" }),
          h("datalist", { id: "origins-import" }, ["chatgpt", "claude", "gemini", "human"].map((o) => h("option", { value: o })))),
        h("label", { class: "field" }, h("span", {}, "Title"), h("input", { class: "input", name: "title", placeholder: "e.g. Feasibility chat, Oct 28" }))),
      err,
      h("div", { style: { display: "flex", justifyContent: "flex-end", gap: "8px" } },
        h("button", { class: "btn primary", type: "submit" }, icon("import"), "Import")));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      try {
        const res = await api.importText(ctx.pid, { text: fd.get("text"), origin_model: fd.get("origin_model"), title: fd.get("title") });
        const n = res.records_created.length;
        toast(res.source.created ? `Imported: ${plural(n, "record")} extracted` : "That exact text was already imported");
        if (res.warnings.length) toast(res.warnings.slice(0, 2).join(" "), { error: true });
        current = res.source.id;
        ctx.setParam("import", null);
        ctx.setParam("s", current);
        await ctx.refresh();
      } catch (ex) { err.textContent = ex.message; err.hidden = false; }
    });
    clear(bodySlot, h("h2", { style: { fontSize: "18px", fontWeight: 600, marginBottom: "14px" } }, "Import text"), form);
    form.querySelector("textarea").focus();
  };

  el.append(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Sources"), h("p", { class: "sub" }, "The original conversations and outputs that records were extracted from, kept word for word.")),
      h("div", { class: "actions" }, h("button", { class: "btn", onclick: () => { ctx.setParam("s", null); ctx.setParam("import", "1"); showImport(); } }, icon("import"), "Import text"))),
    h("div", { class: "split" }, h("div", {}, listSlot), bodySlot));

  sources = (await api.sources(ctx.pid)).sources;
  if (!current && !ctx.params.get("import") && sources.length) current = sources[0].id;
  paintList();
  if (ctx.params.get("import") || !current) showImport(); else showSource();

  return {
    el,
    update: async () => {
      sources = (await api.sources(ctx.pid)).sources;
      paintList();
      if (current) showSource();
    },
  };
}
