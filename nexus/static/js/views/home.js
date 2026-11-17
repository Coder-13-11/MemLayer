// Home: one thing to do next, then the projects. Nothing else.
import { api } from "../api.js";
import { h, clear, icon, ago, toast, plural } from "../dom.js";
import { codeLine } from "./ui.js";

const hiddenKey = (ctx) => `nx-setup-hidden:${ctx.me?.workspace?.id || "local"}`;

function greeting(ctx) {
  const hour = new Date().getHours();
  const part = hour < 5 ? "Good evening" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  const first = (ctx.me?.user?.name || "").trim().split(/\s+/)[0];
  return first ? `${part}, ${first}` : part;
}

export async function render(ctx) {
  const el = h("div", { class: "view-inner calm" });
  const formSlot = h("div");

  const showNew = () => {
    const err = h("p", { class: "form-error", hidden: true, role: "alert" });
    const form = h("form", { class: "inline-form", style: { maxWidth: "560px" } },
      h("label", { class: "field" }, h("span", {}, "Project name"),
        h("input", { class: "input", name: "name", required: true, placeholder: "e.g. Checkout rebuild" })),
      h("label", { class: "field" }, h("span", {}, "What is it trying to achieve? (optional)"),
        h("textarea", { class: "textarea", name: "objective", style: { minHeight: "64px" } })),
      err,
      h("div", { class: "btns" },
        h("button", { class: "btn ghost sm", type: "button", onclick: () => { clear(formSlot); ctx.setParam("new", null); } }, "Cancel"),
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

  const loadExample = async (button) => {
    button.disabled = true;
    button.textContent = "Loading…";
    try {
      const res = await api.loadDemo();
      await ctx.refreshShell();
      ctx.navigate(`/p/${encodeURIComponent(res.project.id)}`);
    } catch (ex) { toast(ex.message, { error: true }); button.disabled = false; button.textContent = "Load the example project"; }
  };

  const paint = async () => {
    const { projects } = await api.projects();
    const counts = ctx.me ? (await api.workspace()).counts : null;
    const waiting = projects.filter((p) => p.high_gaps).sort((a, b) => b.high_gaps - a.high_gaps);
    const total = waiting.reduce((n, p) => n + p.high_gaps, 0);

    // A brand-new workspace: one action, stated once.
    if (!projects.length) {
      clear(el,
        h("div", { class: "welcome" },
          h("h1", {}, ctx.me ? `Welcome to ${ctx.me.workspace.name}` : "Welcome"),
          h("p", { class: "sub" }, "This is where your team's decisions live, so every AI tool you use reads the same thing. ",
            "The quickest way to see it is with an example."),
          ctx.canWrite ? h("div", { class: "welcome-actions" },
            ctx.me ? h("button", { class: "btn primary lg", type: "button", onclick: (e) => loadExample(e.currentTarget) }, "Load the example project") : null,
            h("button", { class: ctx.me ? "btn ghost lg" : "btn primary lg", type: "button", onclick: showNew }, "Start an empty project"))
            : h("p", { class: "muted" }, "No projects yet. Someone with edit access can create the first one."),
          ctx.me ? h("p", { class: "fineprint" }, "The example is invented data: a checkout rebuild with replaced decisions and two tools that disagree. Delete it whenever you like.") : null),
        formSlot,
        setupStrip(ctx, counts, paint));
      if (ctx.params.get("new")) queueMicrotask(showNew);
      return;
    }

    clear(el,
      h("div", { class: "page-head" },
        h("div", {},
          h("h1", {}, greeting(ctx)),
          h("p", { class: "sub" }, total
            ? `${plural(total, "thing")} ${total === 1 ? "needs" : "need"} a decision from a person.`
            : "Nothing needs you right now. Your agents are reading an up-to-date record.")),
        ctx.canWrite ? h("div", { class: "actions" }, h("button", { class: "btn", onclick: showNew }, icon("plus"), "New project")) : null),
      formSlot,
      waiting.length ? h("a", { class: "focus-card", href: `#/p/${encodeURIComponent(waiting[0].id)}/gaps` },
        h("div", {},
          h("div", { class: "focus-title" }, `Review ${plural(waiting[0].high_gaps, "item")} in ${waiting[0].name}`),
          h("div", { class: "focus-sub" }, "Conflicts, unproven assumptions and decisions resting on open questions. A few minutes clears most of them.")),
        h("span", { class: "btn primary" }, "Start review")) : null,
      setupStrip(ctx, counts, paint),
      h("div", { class: "section" },
        h("div", { class: "section-head" }, h("h2", {}, "Projects"), h("span", { class: "meta" }, projects.length)),
        projects.map((p) => {
          const n = Object.values(p.status_counts).reduce((a, b) => a + b, 0);
          return h("a", { class: "proj-row simple", href: `#/p/${encodeURIComponent(p.id)}` },
            h("div", { style: { minWidth: 0 } }, h("div", { class: "name" }, p.name),
              h("div", { class: "desc" }, p.objective || p.description || `${plural(n, "entry", "entries")}`)),
            h("div", { class: "state" }, p.high_gaps
              ? h("span", { class: "needs" }, `${p.high_gaps} to review`) : h("span", { class: "fine" }, icon("check"), "All clear")),
            h("div", { class: "meta" }, ago(p.updated_at)));
        })),
      ctx.me ? null : connectSection(ctx));
    if (ctx.params.get("new")) queueMicrotask(showNew);
  };

  await paint();
  return { el, update: paint };
}

// Three steps, shown until done or hidden. Progress is read from the workspace, not remembered in the browser.
function setupStrip(ctx, counts, repaint) {
  if (!ctx.me || !counts) return null;
  let hidden = false;
  try { hidden = localStorage.getItem(hiddenKey(ctx)) === "1"; } catch { /* ignore */ }
  const admin = ["admin", "owner"].includes(ctx.me.workspace.role);
  const steps = [
    { done: counts.projects > 0, label: "Create a project", href: "#/?new=1" },
    { done: counts.connections > 0 || counts.keys > 0, label: "Connect an AI tool", href: "#/settings/connect", connect: true },
    ...(admin ? [{ done: counts.members > 1, label: "Invite a teammate", href: "#/settings/members" }] : []),
  ];
  const done = steps.filter((s) => s.done).length;
  if (hidden || done === steps.length) return null;
  const next = steps.find((s) => !s.done);
  return h("section", { class: "setup", "aria-label": "Getting set up" },
    h("div", { class: "setup-head" },
      h("strong", {}, "Getting set up"),
      h("span", { class: "meta" }, `${done} of ${steps.length}`),
      h("div", { class: "setup-bar", role: "img", "aria-label": `${done} of ${steps.length} steps done` },
        h("span", { style: { width: `${Math.max(6, 100 * done / steps.length)}%` } })),
      h("button", { class: "btn ghost sm", type: "button", onclick: () => {
        try { localStorage.setItem(hiddenKey(ctx), "1"); } catch { /* ignore */ }
        repaint();
      } }, "Hide")),
    h("ol", { class: "setup-steps" }, steps.map((s) => h("li", { class: s.done ? "done" : s === next ? "next" : "" },
      h("span", { class: "dot", "aria-hidden": "true" }, s.done ? icon("check") : null),
      h("div", { class: "grow" },
        s.done ? h("span", {}, s.label) : h("a", { href: s.href }, s.label),
        s === next && s.connect ? h("div", { class: "setup-detail" },
          h("p", {}, "Paste this address into any app with connectors. It sends you back here to sign in."),
          codeLine(ctx.me.service.mcp_url)) : null)))));
}

function connectSection(ctx) {
  const m = ctx.meta?.mcp;
  if (!m) return null;
  const line = m.url ? m.url : `claude mcp add --scope user ${m.server_name} -- ${m.stdio_command}`;
  return h("div", { class: "section" },
    h("div", { class: "section-head" }, h("h2", {}, "Connect an AI client")),
    h("p", { class: "section-lede" }, "Any MCP client that can launch a local server can read and write this record."),
    codeLine(line));
}
