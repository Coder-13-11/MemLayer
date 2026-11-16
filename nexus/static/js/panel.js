// Right-hand record panel: full detail, provenance, editing, new-record form.
import { api } from "./api.js";
import {
  h, clear, icon, statusPill, kindTag, modelChip, KIND, STATUS, RELATION, REL_IN, stamp, ago, toast,
} from "./dom.js";

let openForm = null; // { rid, name } of the inline form being edited; protects typing from live refreshes

export async function renderPanel(panel, rid, ctx, { quiet = false } = {}) {
  if (quiet && openForm && openForm.rid === rid) return;
  if (!quiet) openForm = null;
  if (rid === "new") return renderNew(panel, ctx);
  if (!quiet) clear(panel, head(ctx, null), h("div", { class: "panel-body muted" }, "Loading…"));
  let rec;
  try {
    rec = await api.record(rid);
  } catch (e) {
    clear(panel, head(ctx, null), h("div", { class: "panel-body" }, h("div", { class: "empty" },
      h("strong", {}, "Record not available"), e.message)));
    return;
  }
  draw(panel, rec, ctx);
}

function head(ctx, rec, extra = []) {
  return h("div", { class: "panel-head" },
    rec ? kindTag(rec.kind) : null,
    rec ? h("span", {
      class: "id mono", title: "Copy id",
      onclick: () => navigator.clipboard?.writeText(rec.id).then(() => toast("Record id copied")),
    }, rec.id) : null,
    h("div", { class: "actions" }, ...extra,
      h("button", { class: "icon-btn", "aria-label": "Close panel", title: "Close (Esc)", onclick: ctx.closePanel }, icon("close"))));
}

function draw(panel, rec, ctx) {
  const redraw = () => draw(panel, rec, ctx);
  const reload = async (message) => {
    openForm = null;
    if (message) toast(message);
    await ctx.refresh();
    renderPanel(panel, rec.id, ctx, { quiet: false });
  };
  const formSlot = h("div");
  const showForm = (name, builder) => {
    openForm = { rid: rec.id, name };
    clear(formSlot, builder(() => { openForm = null; clear(formSlot); }, reload));
    formSlot.querySelector("input, textarea, select")?.focus();
  };

  const evidence = rec.evidence || [];
  const outRels = rec.relations_out || [];
  const inRels = rec.relations_in || [];
  const superseded = rec.status === "superseded";

  clear(panel,
    head(ctx, rec, [
      h("button", { class: "btn ghost sm", onclick: () => showForm("edit", (close, done) => editForm(rec, close, done)) }, icon("edit"), "Edit"),
    ]),
    h("div", { class: "panel-body" },
      h("h2", { class: "title" }, rec.title),
      h("div", { class: "chip-row" }, statusPill(rec.status), modelChip(rec.origin_model),
        h("span", { class: "pill" }, `${rec.importance_label} importance`)),
      superseded && rec.superseded_by ? h("p", { class: "warn-note", style: { marginTop: "14px" } },
        "This record was replaced. ", recLink({ id: rec.superseded_by, title: "Open the newer record" }, ctx),
        " It stays here as history.") : null,
      rec.content ? h("p", { class: "content" }, rec.content) : null,

      h("dl", { class: "prov" },
        h("dt", {}, "Origin"), h("dd", {}, rec.origin_model || "unknown",
          rec.conversation_id ? h("span", { class: "muted" }, ` · conversation ${rec.conversation_id}`) : null),
        h("dt", {}, "Saved by"), h("dd", {}, savedBy(rec.created_by)),
        rec.source ? [h("dt", {}, "Source"), h("dd", {}, h("a", {
          href: `#/p/${encodeURIComponent(ctx.pid)}/sources?s=${rec.source.id}`,
        }, rec.source.title || rec.source.id), h("span", { class: "muted" }, ` · ${rec.source.kind.replace("_", " ")}`))] : null,
        rec.subject ? [h("dt", {}, "Claim key"), h("dd", {}, h("span", { class: "mono" }, rec.subject), " = ",
          h("strong", {}, rec.value ?? "—"))] : null,
        h("dt", {}, "Created"), h("dd", { title: rec.created_at }, stamp(rec.created_at)),
        h("dt", {}, "Last change"), h("dd", { title: rec.updated_at }, `${ago(rec.updated_at)} · version ${rec.version}`)),

      h("div", { class: "chip-row", style: { marginTop: "20px" } },
        h("button", { class: "btn sm", onclick: () => showForm("status", (c, d) => statusForm(rec, c, d)) }, icon("status"), "Change status"),
        h("button", { class: "btn sm", onclick: () => showForm("evidence", (c, d) => evidenceForm(rec, c, d)) }, icon("evidence"), "Add evidence"),
        h("button", { class: "btn sm", onclick: () => showForm("link", (c, d) => linkForm(rec, ctx, c, d)) }, icon("link"), "Link"),
        !superseded ? h("button", { class: "btn sm", onclick: () => showForm("supersede", (c, d) => supersedeForm(rec, ctx, c, d)) }, icon("replace"), "Supersede") : null),
      formSlot,

      h("section", { class: "psec" },
        h("h3", {}, "Evidence", h("span", { class: "n" }, evidence.length)),
        evidence.length ? evidence.map((e) => h("div", { class: "ev" },
          e.url ? h("a", { href: e.url, target: "_blank", rel: "noopener noreferrer" }, e.citation || e.url) : h("span", {}, e.citation || "Excerpt"),
          e.excerpt ? h("blockquote", {}, e.excerpt) : null,
          h("div", { class: "when" }, e.checked_at ? `Checked ${e.checked_at}` : "Check date not recorded",
            ` · added by ${savedBy(e.added_by)}`)))
          : h("p", { class: "muted", style: { margin: 0, fontSize: "13px" } },
            rec.kind === "question" ? "Questions don't need evidence." : "No evidence attached. Treat this as an unchecked claim.")),

      (outRels.length || inRels.length) ? h("section", { class: "psec" },
        h("h3", {}, "Connections", h("span", { class: "n" }, outRels.length + inRels.length)),
        outRels.map((r) => relRow(RELATION[r.type]?.label || r.type, r, ctx, reload)),
        inRels.map((r) => relRow(REL_IN[r.type] || r.type, r, ctx, reload))) : null,

      rec.source_excerpt ? h("section", { class: "psec" },
        h("h3", {}, "Original wording"),
        h("pre", { class: "excerpt" }, rec.source_excerpt)) : null,

      h("section", { class: "psec" },
        h("h3", {}, "History", h("span", { class: "n" }, rec.history.length ? `${rec.history.length} earlier version${rec.history.length > 1 ? "s" : ""}` : "")),
        h("div", { class: "hist" },
          [...rec.history].reverse().map((v) => h("div", { class: "h" },
            h("div", {}, v.change_reason),
            h("div", { class: "when" }, `${stamp(v.changed_at)} · ${savedBy(v.changed_by)} · replaced version ${v.version}`),
            h("details", {}, h("summary", {}, "Show that version"),
              h("pre", { class: "excerpt" }, `${v.snapshot.title}\n\nstatus: ${v.snapshot.lifecycle === "active" ? v.snapshot.verification : v.snapshot.lifecycle}\n\n${v.snapshot.content || ""}`)))),
          h("div", { class: "h" }, h("div", {}, "Created"),
            h("div", { class: "when" }, `${stamp(rec.created_at)} · ${savedBy(rec.created_by)}`)))),

      h("section", { class: "psec" }, deleteButton(rec, ctx))));
}

function localDate() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function savedBy(raw) {
  if (!raw) return "unknown";
  if (raw === "dashboard") return "You, in the dashboard";
  if (raw === "mcp:import_source") return "Import through MCP";
  if (raw === "importer" || raw === "cli:import") return "Import from the CLI";
  if (raw === "cli") return "You, in the CLI";
  if (raw.startsWith("mcp:")) return `${raw.slice(4)}, through MCP`;
  return raw;
}

function recLink(r, ctx) {
  return h("button", { class: "rec-link", onclick: () => ctx.openRecord(r.id), title: r.title },
    r.kind ? h("i", { style: { width: "7px", height: "7px", borderRadius: "2px", background: KIND[r.kind]?.color, flex: "none" } }) : null,
    h("span", { class: "t" }, r.title || r.id));
}

function relRow(label, r, ctx, reload) {
  return h("div", { class: "rel" },
    h("span", { class: "rt" }, label),
    recLink({ id: r.record_id, title: r.title, kind: r.kind }, ctx),
    r.status ? h("span", { style: { flex: "none" } }, statusPill(r.status)) : null,
    r.type !== "supersedes" ? h("button", {
      class: "icon-btn", title: "Remove this link", "aria-label": "Remove link",
      onclick: async () => {
        try { await api.deleteRelation(r.relation_id); reload("Link removed"); } catch (e) { toast(e.message, { error: true }); }
      },
    }, icon("close")) : null);
}

function deleteButton(rec, ctx) {
  let armed = false;
  const btn = h("button", { class: "btn danger sm" }, icon("trash"), "Delete record");
  btn.addEventListener("click", async () => {
    if (!armed) {
      armed = true;
      btn.classList.add("confirm");
      btn.lastChild.textContent = "Click again to delete permanently";
      setTimeout(() => { armed = false; btn.classList.remove("confirm"); btn.lastChild.textContent = "Delete record"; }, 4000);
      return;
    }
    try {
      await api.deleteRecord(rec.id);
      toast("Record deleted");
      ctx.closePanel();
      ctx.refresh();
    } catch (e) { toast(e.message, { error: true }); }
  });
  return h("div", {}, btn, h("p", { class: "muted", style: { fontSize: "12px", margin: "6px 0 0" } },
    "Deleting removes the record, its evidence, links and history. To keep history, supersede or retract it instead."));
}

// ---------------------------------------------------------------- forms

function formShell(title, fields, onSubmit, close, submitLabel = "Save") {
  const err = h("p", { class: "form-error", hidden: true });
  const submit = h("button", { class: "btn primary sm", type: "submit" }, submitLabel);
  const form = h("form", { class: "inline-form" },
    h("div", { style: { fontWeight: "600", fontSize: "13px", marginBottom: "12px" } }, title),
    fields, err,
    h("div", { class: "btns" }, h("button", { class: "btn ghost sm", type: "button", onclick: close }, "Cancel"), submit));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.hidden = true;
    submit.disabled = true;
    try { await onSubmit(new FormData(form)); }
    catch (ex) { err.textContent = ex.message; err.hidden = false; }
    finally { submit.disabled = false; }
  });
  form.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } });
  return form;
}

const reasonField = (placeholder) => h("label", { class: "field" }, h("span", {}, "Reason (kept in history)"),
  h("input", { class: "input", name: "reason", required: true, placeholder }));

function editForm(rec, close, done) {
  return formShell("Correct this record", [
    h("label", { class: "field" }, h("span", {}, "Title"), h("input", { class: "input", name: "title", value: rec.title, required: true })),
    h("label", { class: "field" }, h("span", {}, "Content"), h("textarea", { class: "textarea", name: "content" }, rec.content)),
    h("div", { class: "row" },
      h("label", { class: "field" }, h("span", {}, "Importance"), select("importance", ["low", "normal", "high"], rec.importance_label)),
      h("label", { class: "field" }, h("span", {}, "Claim key"), h("input", { class: "input mono", name: "subject", value: rec.subject || "", placeholder: "e.g. sentinel2.revisit_days" })),
      h("label", { class: "field" }, h("span", {}, "Value"), h("input", { class: "input", name: "value", value: rec.value || "" }))),
    reasonField("e.g. Fixed the VRAM estimate"),
  ], async (fd) => {
    const body = { reason: fd.get("reason"), title: fd.get("title"), content: fd.get("content"), importance: fd.get("importance") };
    if (fd.get("subject") !== (rec.subject || "")) body.subject = fd.get("subject");
    if (fd.get("value") !== (rec.value || "")) body.value = fd.get("value");
    await api.updateRecord(rec.id, body);
    done("Record updated; the previous version is in its history");
  }, close);
}

function statusForm(rec, close, done) {
  const choices = [
    ["evidence_supported", "Evidence-supported: a source backs it"],
    ["unverified", "Unverified: not checked yet"],
    ["challenged", "Challenged: there is reason to doubt it"],
    ["contradicted", "Contradicted: evidence says it is wrong"],
    ["resolved", "Resolved: question answered / task done"],
    ["retracted", "Retracted: withdrawn, keep only as history"],
    ["active", "Reactivate (undo resolve/retract/supersede)"],
  ];
  const current = rec.lifecycle === "active" ? rec.verification : rec.lifecycle;
  return formShell("Change status", [
    h("label", { class: "field" }, h("span", {}, "New status"),
      h("select", { class: "select", name: "status" }, choices.map(([v, l]) => h("option", { value: v, selected: v === current }, l)))),
    !rec.evidence.length ? h("p", { class: "warn-note" }, "Evidence-supported needs at least one evidence item. Add evidence first.") : null,
    reasonField("e.g. Checked the ESA mission page"),
  ], async (fd) => {
    const s = fd.get("status");
    const body = { reason: fd.get("reason") };
    if (["active", "resolved", "retracted"].includes(s)) body.lifecycle = s; else body.verification = s;
    if (body.verification && rec.lifecycle !== "active") body.lifecycle = "active";
    await api.updateRecord(rec.id, body);
    done(`Status set to ${STATUS[s]?.label || "active"}`);
  }, close);
}

function evidenceForm(rec, close, done) {
  const today = localDate();
  return formShell("Add evidence", [
    h("label", { class: "field" }, h("span", {}, "URL"), h("input", { class: "input", name: "url", type: "url", placeholder: "https://…" })),
    h("label", { class: "field" }, h("span", {}, "Citation"), h("input", { class: "input", name: "citation", placeholder: "Author, title, year" })),
    h("label", { class: "field" }, h("span", {}, "Supporting excerpt"), h("textarea", { class: "textarea", name: "excerpt", style: { minHeight: "60px" } })),
    h("div", { class: "row" },
      h("label", { class: "field" }, h("span", {}, "Checked on"), h("input", { class: "input", name: "checked_at", type: "date", value: today })),
      h("label", { class: "field", style: { justifyContent: "flex-end" } }, h("span", {}, " "),
        h("label", { style: { display: "flex", gap: "6px", alignItems: "center", fontSize: "13px" } },
          h("input", { type: "checkbox", name: "verify", checked: rec.verification === "unverified" }), "Mark evidence-supported"))),
    reasonField("e.g. Found the source for this claim"),
  ], async (fd) => {
    const ev = { url: fd.get("url") || null, citation: fd.get("citation") || null, excerpt: fd.get("excerpt") || null, checked_at: fd.get("checked_at") || null };
    if (!ev.url && !ev.citation && !ev.excerpt) throw new Error("Give a URL, a citation or an excerpt.");
    const body = { reason: fd.get("reason"), add_evidence: [ev] };
    if (fd.get("verify")) body.verification = "evidence_supported";
    await api.updateRecord(rec.id, body);
    done("Evidence added");
  }, close);
}

function linkForm(rec, ctx, close, done) {
  let target = null;
  const picker = recordPicker(ctx.pid, rec.id, (r) => { target = r; });
  return formShell("Link to another record", [
    h("label", { class: "field" }, h("span", {}, "This record…"),
      h("select", { class: "select", name: "type" }, Object.entries(RELATION).filter(([k]) => k !== "supersedes")
        .map(([k, v]) => h("option", { value: k }, v.label)))),
    h("div", { class: "field" }, h("span", {}, "…which record?"), picker),
    h("label", { class: "field" }, h("span", {}, "Note"), h("input", { class: "input", name: "note", placeholder: "Why this link holds" })),
    reasonField("e.g. Spotted while reviewing"),
  ], async (fd) => {
    if (!target) throw new Error("Pick the record to link to.");
    await api.updateRecord(rec.id, { reason: fd.get("reason"), add_relations: [{ type: fd.get("type"), target_id: target.id, note: fd.get("note") || null }] });
    done("Link added");
  }, close, "Add link");
}

function supersedeForm(rec, ctx, close, done) {
  let target = null;
  const picker = recordPicker(ctx.pid, rec.id, (r) => { target = r; });
  return formShell("Supersede with a newer record", [
    h("p", { class: "warn-note" }, "The newer record becomes the active one. This record is kept unchanged as history."),
    h("div", { class: "field" }, h("span", {}, "Newer record"), picker),
    reasonField("e.g. 10 days is the single-satellite repeat"),
  ], async (fd) => {
    if (!target) throw new Error("Pick the record that replaces this one.");
    await api.updateRecord(rec.id, { reason: fd.get("reason"), superseded_by: target.id });
    done("Superseded; the history is kept");
  }, close, "Supersede");
}

function select(name, options, current) {
  return h("select", { class: "select", name }, options.map((o) => h("option", { value: o, selected: o === current }, o)));
}

export function recordPicker(pid, excludeId, onPick) {
  const input = h("input", { class: "input", placeholder: "Search records in this project…", autocomplete: "off" });
  const list = h("div", { style: { maxHeight: "180px", overflow: "auto", marginTop: "4px" } });
  let chosen = null;
  let timer = null;
  const load = async () => {
    const q = input.value.trim();
    const { records } = await api.records(pid, { q, inactive: "0" });
    clear(list, records.filter((r) => r.id !== excludeId).slice(0, 8).map((r) => h("button", {
      type: "button", class: "list-row", style: { padding: "6px 8px", margin: 0, width: "100%" },
      onclick: () => {
        chosen = r; onPick(r);
        input.value = r.title;
        clear(list, h("div", { class: "muted", style: { fontSize: "12px", padding: "4px 2px" } }, "Selected: ", kindTag(r.kind), " ", r.id));
      },
    }, h("i", { style: { width: "7px", height: "7px", borderRadius: "2px", background: KIND[r.kind]?.color, flex: "none" } }),
    h("span", { class: "t" }, r.title), statusPill(r.status))));
  };
  input.addEventListener("input", () => { chosen = null; onPick(null); clearTimeout(timer); timer = setTimeout(load, 160); });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") e.preventDefault(); });
  load();
  return h("div", {}, input, list);
}

// ------------------------------------------------------------ new record

function renderNew(panel, ctx) {
  openForm = { rid: "new", name: "new" };
  const err = h("p", { class: "form-error", hidden: true });
  const verifyNote = h("p", { class: "muted", style: { fontSize: "12px", margin: "-6px 0 14px" } },
    "With a URL or citation, the record is saved as evidence-supported. Without one it starts as unverified.");
  const form = h("form", {},
    h("label", { class: "field" }, h("span", {}, "Type"),
      h("select", { class: "select", name: "kind" }, Object.entries(KIND).map(([k, v]) => h("option", { value: k, selected: k === "finding" }, v.label)))),
    h("label", { class: "field" }, h("span", {}, "Title"), h("input", { class: "input", name: "title", required: true, placeholder: "One-line statement of the finding, decision or question" })),
    h("label", { class: "field" }, h("span", {}, "Details"), h("textarea", { class: "textarea", name: "content", placeholder: "Reasoning, context, numbers" })),
    h("div", { class: "row" },
      h("label", { class: "field" }, h("span", {}, "Origin"), h("input", { class: "input", name: "origin_model", value: "human", list: "origins" }),
        h("datalist", { id: "origins" }, ["human", "chatgpt", "claude", "gemini"].map((o) => h("option", { value: o })))),
      h("label", { class: "field" }, h("span", {}, "Importance"), select("importance", ["low", "normal", "high"], "normal"))),
    h("div", { class: "row" },
      h("label", { class: "field" }, h("span", {}, "Evidence URL"), h("input", { class: "input", name: "url", type: "url", placeholder: "https://…" })),
      h("label", { class: "field" }, h("span", {}, "Citation"), h("input", { class: "input", name: "citation" }))),
    verifyNote,
    h("details", { style: { marginBottom: "14px" } }, h("summary", { class: "muted", style: { cursor: "pointer", fontSize: "13px" } }, "Claim key (lets the analyzer compare claims)"),
      h("div", { class: "row", style: { marginTop: "10px" } },
        h("label", { class: "field" }, h("span", {}, "Key"), h("input", { class: "input mono", name: "subject", placeholder: "sentinel2.revisit_days" })),
        h("label", { class: "field" }, h("span", {}, "Value"), h("input", { class: "input", name: "value", placeholder: "5" })))),
    err,
    h("div", { style: { display: "flex", gap: "8px", justifyContent: "flex-end" } },
      h("button", { type: "button", class: "btn ghost", onclick: ctx.closePanel }, "Cancel"),
      h("button", { type: "submit", class: "btn primary" }, "Save record")));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const ev = fd.get("url") || fd.get("citation") ? [{ url: fd.get("url") || null, citation: fd.get("citation") || null, checked_at: localDate() }] : [];
    try {
      const rec = await api.createRecord(ctx.pid, {
        kind: fd.get("kind"), title: fd.get("title"), content: fd.get("content"), origin_model: fd.get("origin_model"),
        importance: fd.get("importance"), subject: fd.get("subject"), value: fd.get("value"),
        evidence: ev, verification: ev.length ? "evidence_supported" : "unverified",
      });
      openForm = null;
      toast(rec.possible_duplicates?.length ? `Saved. A record with the same title already exists (${rec.possible_duplicates[0].id}).` : "Record saved");
      await ctx.refresh();
      ctx.openRecord(rec.id);
    } catch (ex) { err.textContent = ex.message; err.hidden = false; }
  });
  clear(panel,
    h("div", { class: "panel-head" }, h("strong", {}, "New record"),
      h("div", { class: "actions" }, h("button", { class: "icon-btn", "aria-label": "Close", onclick: ctx.closePanel }, icon("close")))),
    h("div", { class: "panel-body" }, form));
  form.querySelector("[name=title]").focus();
}
