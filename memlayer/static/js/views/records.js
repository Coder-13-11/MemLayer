import { api } from "../api.js";
import { h, clear, icon, statusPill, kindTag, modelChip, ago, stamp, KIND, STATUS, plural } from "../dom.js";

const COLS = [
  { key: "title", label: "Title" },
  { key: "kind", label: "Type", opt: true },
  { key: "status", label: "Status" },
  { key: "origin_model", label: "Origin" },
  { key: "evidence_count", label: "Evidence", num: true },
  { key: "links", label: "Links", num: true, opt: true },
  { key: "updated_at", label: "Updated" },
];
const STATUS_RANK = { contradicted: 0, challenged: 1, unverified: 2, evidence_supported: 3, resolved: 4, superseded: 5, retracted: 6 };

export async function render(ctx) {
  const f = {
    q: ctx.params.get("q") || "", kind: ctx.params.get("kind") || "", status: ctx.params.get("status") || "",
    model: ctx.params.get("model") || "", inactive: ctx.params.get("inactive") === "1",
  };
  let sort = { key: f.q ? "rank" : "updated_at", dir: f.q ? 1 : -1 };
  let rows = [];
  let models = [];

  const el = h("div", { class: "view-inner" });
  const tableSlot = h("div");
  const count = h("span", { class: "count" });

  const select = (name, label, options) => {
    const s = h("select", { class: "select", "aria-label": label },
      h("option", { value: "" }, label), options.map(([v, l]) => h("option", { value: v, selected: f[name] === v }, l)));
    s.addEventListener("change", () => { f[name] = s.value; syncUrl(); load(); });
    return s;
  };
  const search = h("input", { class: "input", type: "search", placeholder: "Filter by keyword…", value: f.q, "aria-label": "Filter records" });
  let t;
  search.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { f.q = search.value.trim(); sort = f.q ? { key: "rank", dir: 1 } : { key: "updated_at", dir: -1 }; syncUrl(); load(); }, 180); });
  const inactive = h("input", { type: "checkbox", checked: f.inactive });
  inactive.addEventListener("change", () => { f.inactive = inactive.checked; syncUrl(); load(); });
  const modelSlot = h("span");

  const syncUrl = () => {
    for (const k of ["q", "kind", "status", "model"]) ctx.setParam(k, f[k] || null);
    ctx.setParam("inactive", f.inactive ? "1" : null);
  };

  const sorted = () => {
    const { key, dir } = sort;
    return [...rows].sort((a, b) => {
      let x = a[key]; let y = b[key];
      if (key === "status") { x = STATUS_RANK[x]; y = STATUS_RANK[y]; }
      if (x === y) return 0;
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      return (x > y ? 1 : -1) * dir;
    });
  };

  const paintTable = () => {
    count.textContent = plural(rows.length, "record");
    if (!rows.length) {
      clear(tableSlot, h("div", { class: "empty" }, h("strong", {}, "No records match"),
        f.q || f.kind || f.status || f.model ? "Clear a filter to see more." : "Save a finding with New record, or import a conversation."));
      return;
    }
    const selected = ctx.params.get("r");
    clear(tableSlot, h("table", { class: "grid" },
      h("thead", {}, h("tr", {}, COLS.map((c) => h("th", { class: `${c.num ? "num" : ""} ${c.opt ? "opt" : ""}`, "aria-sort": sort.key === c.key ? (sort.dir > 0 ? "ascending" : "descending") : null },
        h("button", { onclick: () => { sort = { key: c.key, dir: sort.key === c.key ? -sort.dir : (c.key === "title" || c.key === "kind" ? 1 : -1) }; paintTable(); } },
          c.label, sort.key === c.key ? icon(sort.dir > 0 ? "up" : "down", "sort") : null))))),
      h("tbody", {}, sorted().map((r) => h("tr", { dataset: { id: r.id }, class: selected === r.id ? "selected" : "", onclick: () => ctx.openRecord(r.id) },
        h("td", { class: `title ${["superseded", "retracted"].includes(r.status) ? "inactive" : ""}` },
          h("span", { class: "t", title: r.title }, r.title)),
        h("td", { class: "opt" }, kindTag(r.kind)),
        h("td", {}, statusPill(r.status)),
        h("td", {}, modelChip(r.origin_model)),
        h("td", { class: "num" }, r.evidence_count || h("span", { class: "muted" }, "0")),
        h("td", { class: "num opt" }, r.links || h("span", { class: "muted" }, "0")),
        h("td", { class: "muted", title: stamp(r.updated_at) }, ago(r.updated_at)))))));
  };

  const load = async () => {
    const res = await api.records(ctx.pid, { q: f.q, kind: f.kind, status: f.status, model: f.model, inactive: f.inactive ? "1" : "" });
    rows = res.records;
    if (!models.length || !f.model) {
      const all = await api.records(ctx.pid, { inactive: "1" });
      models = [...new Set(all.records.map((r) => r.origin_model || "unknown"))].sort();
      clear(modelSlot, select("model", "Any origin", models.map((m) => [m, m])));
    }
    paintTable();
  };

  el.append(
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Records"),
      h("p", { class: "sub" }, "Every finding, decision, assumption and question, with where it came from and how well it is supported."))),
    h("div", { class: "filters" }, search,
      select("kind", "Any type", Object.entries(KIND).map(([k, v]) => [k, v.label])),
      select("status", "Any status", Object.entries(STATUS).map(([k, v]) => [k, v.label])),
      modelSlot,
      h("label", { class: "toggle" }, inactive, "Include superseded and retracted"),
      count),
    tableSlot);
  await load();
  return { el, update: load };
}
