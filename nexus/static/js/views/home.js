import { api } from "../api.js";
import { h, add, clear, icon, trustBar, ago, modelChip, toast, plural } from "../dom.js";
import { feed } from "./common.js";
import { codeLine } from "./ui.js";

export async function render(ctx) {
  const [{ projects }, { activity }] = await Promise.all([api.projects(), api.activity({ limit: 15 })]);
  const el = h("div", { class: "view-inner" });
  const formSlot = h("div");

  const showNew = () => {
    const err = h("p", { class: "form-error", hidden: true });
    const form = h("form", { class: "inline-form", style: { maxWidth: "560px" } },
      h("label", { class: "field" }, h("span", {}, "Project name"), h("input", { class: "input", name: "name", required: true, placeholder: "e.g. Checkout rebuild" })),
      h("label", { class: "field" }, h("span", {}, "Objective"), h("textarea", { class: "textarea", name: "objective", style: { minHeight: "70px" }, placeholder: "What this project is trying to achieve" })),
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

  add(el, 
    h("div", { class: "page-head" },
      h("div", {},
        h("h1", {}, "All projects"),
        h("p", { class: "sub" }, recordTotal
          ? `${plural(recordTotal, "record")} across ${plural(projects.length, "project")}, written by your team and every AI tool it connected.`
          : "One record of what each project has decided, for every person and AI tool that works on it.")),
      ctx.canWrite ? h("div", { class: "actions" }, h("button", { class: "btn primary", onclick: showNew }, icon("plus"), "New project")) : null),
    formSlot,
    ctx.me && !projects.length ? firstRun(ctx, showNew) : null,
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
      })) : ctx.me ? null : h("div", { class: "empty" }, h("strong", {}, "No projects yet"),
      "Create one here, or let an AI client create it: ask Claude to save something to project memory and it appears in this list."),

    activity.length ? h("div", { class: "section" },
      h("div", { class: "section-head" }, h("h2", {}, "Latest across all projects")),
      feed(activity, ctx, { showProject: true })) : null,

    ctx.me ? null : connectSection(ctx));

  if (ctx.params.get("new")) queueMicrotask(showNew);
  return { el };
}

// A new workspace: three things to do, each doable from here.
function firstRun(ctx, showNew) {
  const s = ctx.me.service;
  const admin = ["admin", "owner"].includes(ctx.me.workspace.role);
  const step = (n, title, text, ...actions) => h("li", { class: "step" },
    h("span", { class: "step-n", "aria-hidden": "true" }, n),
    h("div", {}, h("h2", {}, title), h("p", {}, text), ...actions));
  return h("ol", { class: "first-run" },
    step("1", "Start with a project",
      "Load the example to see a populated record with replaced decisions, a disputed assumption and two tools that disagree. It is invented data and safe to delete.",
      ctx.canWrite ? h("div", { class: "btns" },
        h("button", { class: "btn primary", type: "button", onclick: async (e) => {
          e.target.disabled = true;
          try {
            const res = await api.loadDemo();
            toast("Example project loaded");
            await ctx.refreshShell();
            ctx.navigate(`/p/${encodeURIComponent(res.project.id)}`);
          } catch (ex) { toast(ex.message, { error: true }); e.target.disabled = false; }
        } }, "Load the example project"),
        h("button", { class: "btn", type: "button", onclick: showNew }, "Start an empty one")) : null),
    step("2", "Connect the AI tools you use",
      "Paste this address into any app with connectors. It sends you back here to sign in and choose this workspace.",
      codeLine(s.mcp_url), h("a", { href: "#/settings/connect" }, "Other ways in: the gateway and the CLI")),
    admin ? step("3", "Bring your team", "Everyone works from the same record. Invite by email; the role decides what they can change.",
      h("a", { class: "btn", href: "#/settings/members" }, "Invite teammates")) : null);
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
