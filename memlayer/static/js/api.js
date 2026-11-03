// Thin JSON client. Writes carry X-MemLayer, which the server requires.

async function request(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  if (method !== "GET") opts.headers["X-MemLayer"] = "1";
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new Error(data?.error || `${method} ${path} failed (${res.status})`);
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
};
