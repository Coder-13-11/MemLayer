// Thin JSON client. Writes carry X-Nexus, which the server requires. In the hosted app every request also
// names the workspace it is for; the server checks membership on each one.

let workspace = null;
try { workspace = localStorage.getItem("nx-workspace"); } catch { /* storage unavailable */ }

export function currentWorkspace() { return workspace; }
export function setWorkspace(slug) {
  workspace = slug || null;
  try { slug ? localStorage.setItem("nx-workspace", slug) : localStorage.removeItem("nx-workspace"); } catch { /* ignore */ }
}

async function request(method, path, body) {
  const opts = { method, headers: {} };
  if (workspace) opts.headers["X-Nexus-Workspace"] = workspace;
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  if (method !== "GET") opts.headers["X-Nexus"] = "1";
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (res.status === 401) {  // hosted: the sign-in session ended
    location.href = `/login?next=${encodeURIComponent("/app" + location.hash)}`;
    throw new Error("Signed out");
  }
  if (res.status === 403 && workspace && /not a member of that workspace/.test(data?.error || "")) {
    setWorkspace(null);  // removed from it, or it was deleted: fall back to one they are in
    location.hash = "#/";
    location.reload();
    throw new Error(data.error);
  }
  if (!res.ok) {
    const err = new Error(data?.error || `${method} ${path} failed (${res.status})`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

const enc = encodeURIComponent;
const qs = (params) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, v);
  const s = p.toString();
  return s ? `?${s}` : "";
};

export const api = {
  meta: () => request("GET", "/api/meta"),
  revision: (since) => request("GET", `/api/revision${qs({ since })}`),
  projects: () => request("GET", "/api/projects"),
  createProject: (body) => request("POST", "/api/projects", body),
  project: (pid) => request("GET", `/api/projects/${enc(pid)}`),
  updateProject: (pid, body) => request("PATCH", `/api/projects/${enc(pid)}`, body),
  deleteProject: (pid) => request("DELETE", `/api/projects/${enc(pid)}`),
  records: (pid, params = {}) => request("GET", `/api/projects/${enc(pid)}/records${qs(params)}`),
  createRecord: (pid, body) => request("POST", `/api/projects/${enc(pid)}/records`, body),
  record: (rid) => request("GET", `/api/records/${enc(rid)}?source=1`),
  updateRecord: (rid, body) => request("PATCH", `/api/records/${enc(rid)}`, body),
  deleteRecord: (rid) => request("DELETE", `/api/records/${enc(rid)}`),
  deleteRelation: (id) => request("DELETE", `/api/relations/${enc(id)}`),
  graph: (pid) => request("GET", `/api/projects/${enc(pid)}/graph`),
  gaps: (pid) => request("GET", `/api/projects/${enc(pid)}/gaps`),
  sources: (pid) => request("GET", `/api/projects/${enc(pid)}/sources`),
  source: (sid) => request("GET", `/api/sources/${enc(sid)}`),
  importText: (pid, body) => request("POST", `/api/projects/${enc(pid)}/import`, body),
  activity: (params = {}) => request("GET", `/api/activity${qs(params)}`),
  search: (q) => request("GET", `/api/search${qs({ q })}`),
  briefing: (pid, params = {}) => request("GET", `/api/projects/${enc(pid)}/briefing${qs(params)}`),
  check: (pid, statements) => request("POST", `/api/projects/${enc(pid)}/check`, { statements }),

  // Hosted only: account, workspace and everything a team administers.
  me: () => request("GET", "/api/me"),
  updateMe: (body) => request("PATCH", "/api/me", body),
  changePassword: (body) => request("POST", "/api/me/password", body),
  deleteAccount: (body) => request("DELETE", "/api/me", body),
  createWorkspace: (name) => request("POST", "/api/workspaces", { name }),
  workspace: () => request("GET", "/api/workspace"),
  renameWorkspace: (name) => request("PATCH", "/api/workspace", { name }),
  deleteWorkspace: (confirm) => request("DELETE", "/api/workspace", { confirm }),
  members: () => request("GET", "/api/workspace/members"),
  setRole: (uid, role) => request("PATCH", `/api/workspace/members/${enc(uid)}`, { role }),
  removeMember: (uid) => request("DELETE", `/api/workspace/members/${enc(uid)}`),
  invite: (body) => request("POST", "/api/workspace/invitations", body),
  revokeInvite: (id) => request("DELETE", `/api/workspace/invitations/${enc(id)}`),
  keys: () => request("GET", "/api/workspace/keys"),
  createKey: (body) => request("POST", "/api/workspace/keys", body),
  revokeKey: (id) => request("DELETE", `/api/workspace/keys/${enc(id)}`),
  providers: () => request("GET", "/api/workspace/providers"),
  addProvider: (body) => request("POST", "/api/workspace/providers", body),
  removeProvider: (id) => request("DELETE", `/api/workspace/providers/${enc(id)}`),
  connections: () => request("GET", "/api/workspace/connections"),
  revokeConnection: (id) => request("DELETE", `/api/workspace/connections/${enc(id)}`),
  usage: (days = 30) => request("GET", `/api/workspace/usage${qs({ days })}`),
  receipts: (params = {}) => request("GET", `/api/workspace/receipts${qs(params)}`),
  receipt: (id) => request("GET", `/api/workspace/receipts/${enc(id)}`),
  audit: () => request("GET", "/api/workspace/audit"),
  loadDemo: () => request("POST", "/api/workspace/demo", {}),
  // A plain link would not carry the workspace header, so the file is fetched and handed to the browser.
  exportWorkspace: async () => {
    const res = await fetch("/api/workspace/export", { headers: workspace ? { "X-Nexus-Workspace": workspace } : {} });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || "Export failed");
    const url = URL.createObjectURL(await res.blob());
    const a = Object.assign(document.createElement("a"), { href: url, download: `nexus-${workspace || "workspace"}.json` });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  },
  playground: (body) => request("POST", "/api/workspace/playground", body),
};
