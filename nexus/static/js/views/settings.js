// Workspace settings: people, connecting AI tools, keys, provider keys, usage, the audit log, and the account.
import { api, setWorkspace } from "../api.js";
import { h, add, clear, ago, stamp, toast, plural } from "../dom.js";
import { codeLine, copyText, section, table, confirmButton, onSubmit, field, notice, num } from "./ui.js";

export const SECTIONS = [
  { id: "connect", label: "Connect AI tools" },
  { id: "members", label: "Members" },
  { id: "keys", label: "API keys", admin: true },
  { id: "providers", label: "Provider keys", admin: true },
  { id: "usage", label: "Usage" },
  { id: "audit", label: "Audit log", admin: true },
  { id: "workspace", label: "Workspace" },
  { id: "account", label: "Your account" },
];
const ROLE_HELP = { owner: "Everything, including deleting the workspace", admin: "Manages people, keys and connected apps",
  editor: "Reads and writes records", viewer: "Reads only" };
const ACTIONS = {
  "account.created": "Account created", "member.joined": "Joined the workspace", "member.left": "Left the workspace",
  "member.removed": "Removed a member", "member.role_changed": "Changed a role", "invitation.created": "Invited",
  "invitation.revoked": "Withdrew an invitation", "key.created": "Created an API key", "key.revoked": "Revoked an API key",
  "provider_key.stored": "Stored a provider key", "provider_key.removed": "Removed a provider key",
  "connection.approved": "Connected an app", "connection.revoked": "Disconnected an app",
  "workspace.created": "Created the workspace", "workspace.renamed": "Renamed the workspace",
  "workspace.exported": "Exported the workspace", "account.password_changed": "Changed their password",
};

export async function render(ctx) {
  const me = ctx.me;
  const admin = ["admin", "owner"].includes(me.workspace.role);
  const tab = SECTIONS.find((s) => s.id === ctx.tab && (!s.admin || admin)) || SECTIONS[0];
  const el = h("div", { class: "view-inner settings" });
  const body = h("div", { class: "settings-body" });
  add(el, 
    h("nav", { class: "settings-nav", "aria-label": "Settings" }, SECTIONS.filter((s) => !s.admin || admin).map((s) =>
      h("a", { href: `#/settings/${s.id}`, class: s.id === tab.id ? "active" : "", "aria-current": s.id === tab.id ? "page" : null }, s.label)),
      h("a", { href: "#/playground", class: "aside" }, "Try the gateway")),
    body);
  const draw = async () => clear(body, await PANES[tab.id](ctx, me, admin, draw));
  await draw();
  return { el, update: draw };
}

const PANES = {
  // ------------------------------------------------------------- connect
  async connect(ctx, me, admin, redraw) {
    const s = me.service;
    const { connections, all_members } = await api.connections();
    return [
      h("h1", {}, "Connect AI tools"),
      h("p", { class: "sub" }, "Three ways in, for three kinds of tool. All of them read and write this workspace's record."),
      section("Apps that support connectors", "MCP",
        h("p", { class: "section-lede" }, "ChatGPT, Claude, Cursor, VS Code and other apps with custom connectors. Paste the address; the app ",
          "sends you here to sign in and choose this workspace. You can make a connection read-only."),
        codeLine(s.mcp_url),
        h("p", { class: "section-lede" }, "Claude Code, from a terminal:"),
        codeLine(`claude mcp add --transport http ${s.brand.mcp_server_name} ${s.mcp_url}`)),
      section("Tools that only let you change a base URL", "Gateway",
        h("p", { class: "section-lede" }, "Point the tool at NEXUS instead of the provider. Each request gets the project's briefing added and leaves a receipt. ",
          "Create an API key first, then either store your provider key under Provider keys, or keep sending your own and add the NEXUS key as a header."),
        h("dl", { class: "kv" },
          h("dt", {}, "OpenAI-compatible tools"), h("dd", {}, codeLine(`${s.public_url}/v1`)),
          h("dt", {}, "Claude Code and Anthropic SDKs"), h("dd", {}, codeLine(`ANTHROPIC_BASE_URL=${s.public_url}`)),
          h("dt", {}, "Choose the project"), h("dd", {}, codeLine("x-nexus-project: <project name>")),
          h("dt", {}, "Keep your own provider key"), h("dd", {}, codeLine("x-nexus-key: nxk_…")))),
      section("Terminals, hooks and CI", "CLI",
        h("p", { class: "section-lede" }, "Print the briefing at the start of an agent session, and fail a CI step when a plan repeats something the team replaced."),
        codeLine(`NEXUS_URL=${s.public_url} NEXUS_API_KEY=nxk_… nexus brief "<project>"`),
        codeLine(`nexus check "<project>" "Add Jest tests for the cart"`)),
      section(all_members ? "Connected apps, all members" : "Your connected apps", connections.length,
        table([
          { label: "App", cell: (c) => h("strong", {}, c.client_name || "An app") },
          { label: "Connected by", cell: (c) => c.email },
          { label: "Access", cell: (c) => c.access === "read" ? "Read only" : "Read and write" },
          { label: "Last used", cell: (c) => c.last_used_at ? ago(c.last_used_at) : "never" },
          { label: "", cls: "right", cell: (c) => confirmButton("Disconnect", "Disconnect now", async () => {
            await api.revokeConnection(c.id); toast("Disconnected"); redraw();
          }) },
        ], connections, { empty: "No app is connected to this workspace yet." })),
    ];
  },

  // ------------------------------------------------------------- members
  async members(ctx, me, admin, redraw) {
    const { members, invitations, you } = await api.members();
    const myRole = me.workspace.role;
    const linkSlot = h("div");
    const invite = admin ? onSubmit(h("form", { class: "inline-form wide" },
      h("div", { class: "row" },
        field("Email", h("input", { class: "input", name: "email", type: "email", required: true, placeholder: "teammate@company.com" })),
        field("Role", h("select", { class: "select", name: "role" },
          me.service.invitable_roles.map((r) => h("option", { value: r, selected: r === "editor" }, `${r[0].toUpperCase()}${r.slice(1)}: ${ROLE_HELP[r].toLowerCase()}`))))),
      h("div", { class: "btns" }, h("button", { class: "btn primary sm", type: "submit" }, "Invite"))),
    async (data, form) => {
      const res = await api.invite(data);
      form.reset();
      clear(linkSlot, notice(res.emailed ? `An invitation was emailed to ${res.invitation.email}. You can also share this link:`
        : `This server has no outgoing mail, so send ${res.invitation.email} this link yourself. It works once, for that address only, for 7 days.`, "ok"),
        codeLine(res.link, "Copy invitation link"));
      await redrawLists();
    }) : null;
    const lists = h("div");
    async function redrawLists() {
      const fresh = await api.members();
      clear(lists, render(fresh.members, fresh.invitations));
    }
    function render(members, invitations) {
      return [
        table([
          { label: "Person", cell: (m) => [h("strong", {}, m.name || m.email.split("@")[0]), m.id === you ? h("span", { class: "meta" }, " (you)") : null, h("br"), h("span", { class: "meta" }, m.email)] },
          { label: "Role", cell: (m) => {
            const canEdit = admin && m.id !== you && (myRole === "owner" || m.role !== "owner");
            if (!canEdit) return h("span", { title: ROLE_HELP[m.role] }, m.role);
            return h("select", { class: "select compact", "aria-label": `Role for ${m.email}`, onchange: async (e) => {
              try { await api.setRole(m.id, e.target.value); toast(`${m.email} is now ${e.target.value}`); }
              catch (ex) { toast(ex.message, { error: true }); }
              redrawLists();
            } }, me.service.roles.filter((r) => r !== "owner" || myRole === "owner").map((r) => h("option", { value: r, selected: r === m.role }, r)));
          } },
          { label: "Joined", cell: (m) => ago(m.joined_at) },
          { label: "", cls: "right", cell: (m) => (m.id === you || (admin && (myRole === "owner" || m.role !== "owner")))
            ? confirmButton(m.id === you ? "Leave" : "Remove", m.id === you ? "Leave workspace" : "Remove now", async () => {
              await api.removeMember(m.id);
              if (m.id === you) { setWorkspace(null); location.hash = "#/"; location.reload(); return; }
              toast(`${m.email} removed. Their connected apps lost access.`);
              redrawLists();
            }) : null },
        ], members),
        admin && invitations.length ? section("Pending invitations", invitations.length, table([
          { label: "Email", cell: (i) => i.email }, { label: "Role", cell: (i) => i.role },
          { label: "Invited by", cell: (i) => i.invited_by || "" }, { label: "Sent", cell: (i) => ago(i.created_at) },
          { label: "", cls: "right", cell: (i) => confirmButton("Withdraw", "Withdraw now", async () => { await api.revokeInvite(i.id); redrawLists(); }) },
        ], invitations)) : null,
      ];
    }
    clear(lists, render(members, invitations));
    return [
      h("h1", {}, "Members"),
      h("p", { class: "sub" }, `${plural(members.length, "person", "people")} of ${me.workspace.limits.members} on this plan. `,
        "Everyone sees the same projects; the role decides what they can change."),
      invite, linkSlot, lists,
      section("What each role can do", null, h("dl", { class: "kv" },
        Object.entries(ROLE_HELP).reverse().flatMap(([r, text]) => [h("dt", {}, r), h("dd", {}, text)]))),
    ];
  },

  // ---------------------------------------------------------------- keys
  async keys(ctx, me, admin, redraw) {
    const { keys } = await api.keys();
    const { projects } = await api.projects();
    const reveal = h("div");
    const form = onSubmit(h("form", { class: "inline-form wide" },
      h("div", { class: "row" },
        field("Name", h("input", { class: "input", name: "name", required: true, maxlength: 80, placeholder: "Where it will be used, e.g. CI or Cursor" })),
        field("Can", h("select", { class: "select", name: "role" },
          h("option", { value: "editor" }, "Read and write records"), h("option", { value: "viewer" }, "Read only")))),
      h("div", { class: "row" },
        field("Used for", h("select", { class: "select", name: "scope" },
          h("option", { value: "both" }, "The API and the gateway"), h("option", { value: "memory" }, "The API only"),
          h("option", { value: "gateway" }, "The gateway only"))),
        field("Default project", h("select", { class: "select", name: "default_project" },
          h("option", { value: "" }, "None"), projects.map((p) => h("option", { value: p.name }, p.name))),
          "The gateway adds this project's briefing when a request does not name one.")),
      h("div", { class: "btns" }, h("button", { class: "btn primary sm", type: "submit" }, "Create key"))),
    async (data, f) => {
      const res = await api.createKey({ name: data.name, role: data.role, default_project: data.default_project,
        scopes: data.scope === "both" ? ["memory", "gateway"] : [data.scope] });
      f.reset();
      clear(reveal, notice("Copy this key now. It is shown once and cannot be recovered, only replaced.", "ok"),
        codeLine(res.secret, "Copy key"));
      clear(listSlot, list((await api.keys()).keys));
    });
    const listSlot = h("div");
    const list = (rows) => table([
      { label: "Name", cell: (k) => h("strong", {}, k.name) },
      { label: "Key", cell: (k) => h("span", { class: "mono" }, `${k.prefix}…`) },
      { label: "Can", cell: (k) => `${k.role === "viewer" ? "Read" : "Read and write"} · ${k.scopes.join(" + ")}` },
      { label: "Last used", cell: (k) => k.last_used_at ? ago(k.last_used_at) : "never" },
      { label: "", cls: "right", cell: (k) => confirmButton("Revoke", "Revoke now", async () => {
        await api.revokeKey(k.id); toast(`${k.name} revoked`); clear(listSlot, list((await api.keys()).keys));
      }) },
    ], rows, { empty: "No keys yet. Create one for each place that needs access, so each can be revoked on its own." });
    clear(listSlot, list(keys));
    return [h("h1", {}, "API keys"),
      h("p", { class: "sub" }, "For programs: the REST API, the gateway and the CLI. A key acts for this workspace only and can never manage members or other keys."),
      form, reveal, listSlot];
  },

  // ----------------------------------------------------------- providers
  async providers(ctx, me, admin, redraw) {
    const { credentials, available } = await api.providers();
    const listSlot = h("div");
    const NAMES = { openai: "OpenAI", anthropic: "Anthropic", google: "Google Gemini", openai_compatible: "OpenAI-compatible (OpenRouter, Azure, a proxy…)" };
    const baseUrl = field("Base URL", h("input", { class: "input", name: "base_url", type: "url", placeholder: "https://openrouter.ai/api/v1" }),
      "Needed for OpenAI-compatible providers. Leave empty for the others.");
    const form = onSubmit(h("form", { class: "inline-form wide" },
      h("div", { class: "row" },
        field("Provider", h("select", { class: "select", name: "provider" }, me.service.providers.map((p) => h("option", { value: p }, NAMES[p] || p)))),
        field("API key", h("input", { class: "input", name: "secret", type: "password", required: true, autocomplete: "off", spellcheck: "false" }))),
      baseUrl,
      h("div", { class: "btns" }, h("button", { class: "btn primary sm", type: "submit", disabled: !available }, "Store key"))),
    async (data, f) => {
      await api.addProvider(data);
      f.reset();
      toast("Stored. It is encrypted and cannot be read back, only replaced.");
      clear(listSlot, list((await api.providers()).credentials));
    });
    const list = (rows) => table([
      { label: "Provider", cell: (c) => h("strong", {}, NAMES[c.provider] || c.provider) },
      { label: "Key", cell: (c) => h("span", { class: "mono" }, `…${c.hint}`) },
      { label: "Base URL", cell: (c) => c.base_url || "default" },
      { label: "Last used", cell: (c) => c.last_used_at ? ago(c.last_used_at) : "never" },
      { label: "", cls: "right", cell: (c) => confirmButton("Remove", "Remove now", async () => {
        await api.removeProvider(c.id); clear(listSlot, list((await api.providers()).credentials));
      }) },
    ], rows, { empty: "No provider keys stored. The gateway can still forward requests that carry their own provider key." });
    clear(listSlot, list(credentials));
    return [h("h1", {}, "Provider keys"),
      h("p", { class: "sub" }, "Your own keys for the AI providers the gateway forwards to. NEXUS adds no charge and does not resell model access. ",
        "A stored key is encrypted with a key of its own, is only opened in memory at the moment of a request, and is never shown again."),
      available ? null : notice("This server has no NEXUS_SECRET_KEY configured, so keys cannot be stored. Requests can still pass their own provider key through.", "warn"),
      form, listSlot];
  },

  // --------------------------------------------------------------- usage
  async usage(ctx, me) {
    const u = await api.usage(30);
    const days = [...new Set(u.by_day.map((d) => d.day))];
    const perDay = days.map((day) => ({ day, calls: u.by_day.filter((d) => d.day === day).reduce((a, d) => a + d.calls, 0) }));
    const max = Math.max(1, ...perDay.map((d) => d.calls));
    const byChannel = {};
    for (const d of u.by_day) byChannel[d.channel] = (byChannel[d.channel] || 0) + d.calls;
    const pct = Math.min(100, Math.round(100 * u.month.gateway_requests / u.month.gateway_limit));
    return [h("h1", {}, "Usage"),
      h("p", { class: "sub" }, `The last 30 days, and this month against the ${u.plan} plan's limits. Only counts are kept: no request or response text.`),
      section("Gateway requests this month", `${num(u.month.gateway_requests)} of ${num(u.month.gateway_limit)}`,
        h("div", { class: "meter", role: "img", "aria-label": `${pct}% of the monthly gateway limit used` }, h("span", { style: { width: `${pct}%` } }))),
      section("Calls per day", null, perDay.length ? h("div", { class: "bars", role: "img", "aria-label": "Calls per day" },
        perDay.map((d) => h("div", { class: "bar", title: `${d.day}: ${num(d.calls)} calls` },
          h("span", { style: { height: `${Math.max(3, 100 * d.calls / max)}%` } }), h("i", {}, d.day.slice(8)))))
        : h("p", { class: "empty" }, "No calls yet.")),
      section("By way in", null, h("dl", { class: "kv" }, Object.entries({ mcp: "Connected apps", gateway: "Gateway", api: "REST API" })
        .flatMap(([k, label]) => [h("dt", {}, label), h("dd", {}, num(byChannel[k] || 0))]))),
      section("Models through the gateway", u.by_model.length, table([
        { label: "Model", cell: (m) => h("strong", {}, m.model || "unknown") }, { label: "Provider", cell: (m) => m.provider || "" },
        { label: "Requests", cls: "num", cell: (m) => num(m.calls) }, { label: "Input tokens", cls: "num", cell: (m) => num(m.input_tokens) },
        { label: "Output tokens", cls: "num", cell: (m) => num(m.output_tokens) },
        { label: "Briefing tokens", cls: "num", cell: (m) => num(m.context_tokens) },
        { label: "Errors", cls: "num", cell: (m) => num(m.errors) }, { label: "Avg time", cls: "num", cell: (m) => `${num(m.avg_latency_ms)} ms` },
      ], u.by_model, { empty: "Nothing has gone through the gateway in this period." })),
      section("Most active", null, table([
        { label: "Who", cell: (a) => a.actor || "unknown" }, { label: "Through", cell: (a) => a.channel },
        { label: "Calls", cls: "num", cell: (a) => num(a.calls) }, { label: "Last", cell: (a) => ago(a.last_at) },
      ], u.by_actor, { empty: "No calls yet." })),
      section("Plan limits", u.plan, h("dl", { class: "kv" },
        h("dt", {}, "Members"), h("dd", {}, num(u.limits.members)), h("dt", {}, "Projects"), h("dd", {}, num(u.limits.projects)),
        h("dt", {}, "Records"), h("dd", {}, num(u.limits.records)),
        h("dt", {}, "API and gateway requests per key"), h("dd", {}, `${num(u.limits.api_requests_minute)} a minute`)))];
  },

  // --------------------------------------------------------------- audit
  async audit() {
    const { audit } = await api.audit();
    return [h("h1", {}, "Audit log"),
      h("p", { class: "sub" }, "Who changed access to this workspace, and when. Entries are never edited or removed."),
      table([
        { label: "When", cell: (a) => h("span", { title: a.at }, stamp(a.at)) },
        { label: "Who", cell: (a) => a.actor },
        { label: "What", cell: (a) => [h("strong", {}, ACTIONS[a.action] || a.action), a.target ? ` ${a.target}` : "",
          a.detail?.role ? h("span", { class: "meta" }, ` as ${a.detail.role}`) : null,
          a.detail?.access ? h("span", { class: "meta" }, ` (${a.detail.access === "read" ? "read only" : "read and write"})`) : null] },
        { label: "From", cell: (a) => h("span", { class: "mono" }, a.ip || "") },
      ], audit, { empty: "Nothing yet." })];
  },

  // ----------------------------------------------------------- workspace
  async workspace(ctx, me, admin) {
    const { workspace: ws, counts } = await api.workspace();
    const owner = me.workspace.role === "owner";
    const rename = admin ? onSubmit(h("form", { class: "inline-form" },
      field("Name", h("input", { class: "input", name: "name", required: true, maxlength: 80, value: ws.name })),
      h("div", { class: "btns" }, h("button", { class: "btn sm", type: "submit" }, "Rename"))),
    async (data) => { await api.renameWorkspace(data.name); toast("Renamed"); location.reload(); }) : null;
    const remove = owner ? onSubmit(h("form", { class: "inline-form danger-zone" },
      h("p", {}, `Deleting removes every project, record, source and receipt in ${ws.name}, and disconnects every app. It cannot be undone. Export first if you may want it later.`),
      field(`Type the workspace's name to confirm`, h("input", { class: "input", name: "confirm", required: true, autocomplete: "off" })),
      h("div", { class: "btns" }, h("button", { class: "btn danger sm", type: "submit" }, "Delete this workspace"))),
    async (data) => { await api.deleteWorkspace(data.confirm); setWorkspace(null); location.hash = "#/"; location.reload(); }) : null;
    return [h("h1", {}, "Workspace"),
      h("p", { class: "sub" }, `${ws.name} holds ${plural(counts.projects, "project")} and ${plural(counts.records, "record")}, with ${plural(counts.members, "member")}.`),
      rename,
      admin ? section("Export", null,
        h("p", { class: "section-lede" }, "Everything in this workspace as one JSON file: projects, records with their full history, evidence, relations, original sources and the member list. It is yours to keep or move."),
        h("a", { class: "btn sm", href: "/api/workspace/export", download: "" }, "Download export")) : null,
      remove ? section("Delete", null, remove) : null];
  },

  // ------------------------------------------------------------- account
  async account(ctx, me) {
    const profile = onSubmit(h("form", { class: "inline-form" },
      field("Name", h("input", { class: "input", name: "name", maxlength: 80, value: me.user.name || "" })),
      field("Email", h("input", { class: "input", value: me.user.email, disabled: true }),
        me.user.email_verified_at ? "Confirmed" : "Not confirmed yet"),
      h("div", { class: "btns" }, h("button", { class: "btn sm", type: "submit" }, "Save"))),
    async (data) => { await api.updateMe({ name: data.name }); toast("Saved"); });
    const password = onSubmit(h("form", { class: "inline-form" },
      field("Current password", h("input", { class: "input", name: "current", type: "password", required: true, autocomplete: "current-password" })),
      field("New password", h("input", { class: "input", name: "new", type: "password", required: true, minlength: 10, autocomplete: "new-password" }), "At least 10 characters"),
      h("div", { class: "btns" }, h("button", { class: "btn sm", type: "submit" }, "Change password"))),
    async (data, f) => { const r = await api.changePassword(data); f.reset(); toast(r.note); });
    const remove = onSubmit(h("form", { class: "inline-form danger-zone" },
      h("p", {}, "Deleting your account removes it and any workspace where you are the only member. A workspace you share stays with its other members."),
      field("Your password", h("input", { class: "input", name: "password", type: "password", required: true, autocomplete: "current-password" })),
      h("div", { class: "btns" }, h("button", { class: "btn danger sm", type: "submit" }, "Delete my account"))),
    async (data) => { await api.deleteAccount(data); setWorkspace(null); location.href = "/"; });
    return [h("h1", {}, "Your account"), profile, section("Password", null,
      h("p", { class: "section-lede" }, "Changing it signs you out of other browsers and disconnects your AI apps, which will ask you to sign in again."), password),
      section("Delete account", null, remove)];
  },
};

export { copyText };
