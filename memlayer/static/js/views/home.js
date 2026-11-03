import { api } from "../api.js";
import { h, clear, icon, trustBar, ago, modelChip, toast, plural } from "../dom.js";
import { feed } from "./common.js";

export async function render(ctx) {
  const [{ projects }, { activity }] = await Promise.all([api.projects(), api.activity({ limit: 15 })]);
  const el = h("div", { class: "view-inner" });
  const formSlot = h("div");

  const showNew = () => {
    const err = h("p", { class: "form-error", hidden: true });
    const form = h("form", { class: "inline-form", style: { maxWidth: "560px" } },
      h("label", { class: "field" }, h("span", {}, "Project name"), h("input", { class: "input", name: "name", required: true, placeholder: "e.g. Environmental AI Research" })),
      h("label", { class: "field" }, h("span", {}, "Objective"), h("textarea", { class: "textarea", name: "objective", style: { minHeight: "70px" }, placeholder: "What this research is trying to establish" })),
      err,
      h("div", { class: "btns" }, h("button", { class: "btn ghost sm", type: "button", onclick: () => { clear(formSlot); ctx.setParam("new", null); } }, "Cancel"),
        h("button", { class: "btn primary sm", type: "submit" }, "Create project")));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      try {
        const p = await api.createProject({ name: fd.get("name"), objective: fd.get("objective") });
        toast(`Created ${p.name}`);
        await ctx.refreshShell();
        ctx.navigate(`/p/${encodeURIComponent(p.id)}`);
      } catch (ex) { err.textContent = ex.message; err.hidden = false; }
    });
    clear(formSlot, form);
    form.querySelector("input").focus();
  };

  const totals = projects.reduce((acc, p) => {
    for (const [k, v] of Object.entries(p.status_counts)) acc[k] = (acc[k] || 0) + v;
    return acc;
  }, {});
  const recordTotal = Object.values(totals).reduce((a, b) => a + b, 0);

  el.append(
    h("div", { class: "page-head" },
      h("div", {},
        h("h1", {}, "All projects"),
        h("p", { class: "sub" }, recordTotal
          ? `${plural(recordTotal, "record")} across ${plural(projects.length, "project")}, saved by you and every connected AI.`
          : ctx.meta?.brand?.tagline || "")),
      h("div", { class: "actions" }, h("button", { class: "btn primary", onclick: showNew }, icon("plus"), "New project"))),
    formSlot,
    projects.length ? h("div", {},
      h("div", { class: "section-head" }, h("h2", {}, "Projects"), h("span", { class: "meta" }, projects.length)),
      projects.map((p) => {
        const n = Object.values(p.status_counts).reduce((a, b) => a + b, 0);
        const checked = p.status_counts.evidence_supported || 0;
        return h("a", { class: "proj-row", href: `#/p/${encodeURIComponent(p.id)}` },
          h("div", { style: { minWidth: 0 } }, h("div", { class: "name" }, p.name),
            h("div", { class: "desc" }, p.objective || p.description || "No objective written yet")),
          h("div", { class: "hide-sm" }, trustBar(p.status_counts),
            h("div", { class: "meta", style: { marginTop: "6px" } }, `${checked} of ${n} evidence-supported`)),
          h("div", { class: "chip-row hide-sm" }, p.models.slice(0, 3).map(modelChip)),
          h("div", { class: "meta", style: { textAlign: "right" } },
            p.high_gaps ? h("div", { style: { color: "var(--sev-high)" } }, `${p.high_gaps} need attention`) : h("div", {}, "No urgent gaps"),
            h("div", {}, `updated ${ago(p.updated_at)}`)));
      })) : h("div", { class: "empty" }, h("strong", {}, "No projects yet"),
      "Create one here, or let an AI client create it: ask Claude to save something to project memory and it appears in this list."),

    activity.length ? h("div", { class: "section" },
      h("div", { class: "section-head" }, h("h2", {}, "Latest across all projects")),
      feed(activity, ctx, { showProject: true })) : null,

    connectSection(ctx));

  if (ctx.params.get("new")) queueMicrotask(showNew);
  return { el };
}

function connectSection(ctx) {
  const m = ctx.meta?.mcp;
  if (!m) return null;
  const copy = (text) => navigator.clipboard?.writeText(text).then(() => toast("Copied"));
  const add = m.url ? m.url : `claude mcp add --scope user ${m.server_name} -- ${m.stdio_command}`;
  return h("div", { class: "connect" },
    h("div", {},
      h("h2", {}, "Connect an AI client"),
      h("p", {}, m.url
        ? "Paste this address into ChatGPT (Developer mode connectors), Claude (Settings, Connectors), Gemini or Perplexity custom connectors. The app asks you to sign in once, then reads and writes this memory."
        : "Any MCP client that can launch a local server can read and write this memory. Claude Code and Claude Desktop work today. ChatGPT only connects to remote HTTPS servers, so for now bring ChatGPT research in with Import."),
      h("div", { class: "code" }, h("span", {}, add), h("button", { class: "icon-btn", "aria-label": "Copy", onclick: () => copy(add) }, icon("copy")))),
    h("div", {},
      h("h2", {}, "Ask your AI to review a project"),
      h("p", {}, m.url
        ? "The built-in gap analysis follows fixed rules. For contradictions that only show in the wording, ask your assistant to \u201creview my project memory\u201d. It records each conflict back into memory, and the conflicts appear here."
        : "The built-in gap analysis follows fixed rules. For contradictions that only show in the wording, run the review_project prompt from your MCP client. The AI records each conflict it finds back into memory, and the conflicts appear here."),
      h("div", { class: "code" }, h("span", {}, `/mcp__${m.server_name}__review_project "Project name"`),
        h("button", { class: "icon-btn", "aria-label": "Copy", onclick: () => copy(`/mcp__${m.server_name}__review_project`) }, icon("copy")))));
}
