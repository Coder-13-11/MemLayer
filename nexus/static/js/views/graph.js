// Interactive knowledge graph: force-directed SVG with pan, zoom, drag, hover focus.
import { api } from "../api.js";
import { h, clear, KIND, STATUS, RELATION, statusPill } from "../dom.js";

const NS = "http://www.w3.org/2000/svg";
const RING = {
  evidence_supported: { stroke: "var(--st-supported)", dash: null },
  unverified: { stroke: "var(--st-unverified)", dash: "2.5 2.5" },
  challenged: { stroke: "var(--st-challenged)", dash: null },
  contradicted: { stroke: "var(--st-contradicted)", dash: null },
  resolved: { stroke: "var(--st-resolved)", dash: null },
  superseded: { stroke: "var(--st-superseded)", dash: "1.5 2" },
  retracted: { stroke: "var(--st-retracted)", dash: "1.5 2" },
};

export async function render(ctx) {
  const opts = { inactive: true, labels: true };
  const pos = new Map(); // id -> {x, y, vx, vy, fixed}
  let data = { nodes: [], edges: [] };
  let view = { x: 0, y: 0, k: 1 };
  let alpha = 1;
  let raf = null;
  let fitted = false;
  let hoverId = null;

  const svg = h("svg", { class: "canvas", role: "img", "aria-label": "Knowledge graph of project records" });
  const defs = h("defs");
  const stage = h("g");
  const edgeLayer = h("g");
  const nodeLayer = h("g");
  stage.append(edgeLayer, nodeLayer);
  svg.append(defs, stage);
  for (const [type, r] of Object.entries(RELATION)) {
    const m = h("marker", { id: `arrow-${type}`, viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" },
      h("path", { d: "M0 1.5 9 5 0 8.5z", style: { fill: r.color } }));
    defs.append(m);
  }
  const tip = h("div", { class: "graph-tip", hidden: true });
  const toggle = (key, label) => {
    const cb = h("input", { type: "checkbox", checked: opts[key] });
    cb.addEventListener("change", () => { opts[key] = cb.checked; build(); reheat(.4); });
    return h("label", {}, cb, label);
  };
  const stats = h("span", { class: "muted" });
  const wrap = h("div", { class: "graph-wrap" },
    svg,
    h("div", { class: "graph-tools" },
      h("div", { class: "seg" }, toggle("inactive", "Show superseded"), toggle("labels", "Labels"), stats),
      h("button", { class: "btn sm", onclick: () => fit(true) }, "Fit to screen")),
    legend(), tip);
  const el = h("div", { class: "view-inner wide" }, wrap);

  // ------------------------------------------------------------ build DOM
  let nodeEls = new Map();
  let edgeEls = [];
  let visible = { nodes: [], edges: [] };

  function build() {
    const nodes = data.nodes.filter((n) => opts.inactive || n.lifecycle === "active");
    const ids = new Set(nodes.map((n) => n.id));
    const edges = data.edges.filter((e) => ids.has(e.source) && ids.has(e.target));
    visible = { nodes, edges };
    stats.textContent = `${nodes.length} records · ${edges.length} links`;

    // seed positions for new nodes near a linked neighbour, else on a ring by kind
    const kinds = Object.keys(KIND);
    nodes.forEach((n, i) => {
      if (pos.has(n.id)) return;
      const nb = edges.find((e) => (e.source === n.id && pos.has(e.target)) || (e.target === n.id && pos.has(e.source)));
      const anchor = nb ? pos.get(nb.source === n.id ? nb.target : nb.source) : null;
      const a = (kinds.indexOf(n.kind) / kinds.length) * Math.PI * 2 + i * 0.37;
      const r = anchor ? 40 : 160 + (i % 5) * 30;
      pos.set(n.id, { x: (anchor?.x || 0) + Math.cos(a) * r, y: (anchor?.y || 0) + Math.sin(a) * r, vx: 0, vy: 0, fixed: false });
    });

    edgeEls = edges.map((e) => {
      const r = RELATION[e.type] || RELATION.relates_to;
      const line = document.createElementNS(NS, "line");
      line.setAttribute("class", "edge");
      line.setAttribute("marker-end", `url(#arrow-${e.type})`);
      line.style.stroke = r.color;
      if (r.dash) line.setAttribute("stroke-dasharray", r.dash);
      const title = document.createElementNS(NS, "title");
      title.textContent = `${e.type.replace("_", " ")}${e.note ? `: ${e.note}` : ""}`;
      line.append(title);
      return { e, line };
    });
    clear(edgeLayer, edgeEls.map((x) => x.line));

    nodeEls = new Map();
    clear(nodeLayer, nodes.map((n) => {
      const g = document.createElementNS(NS, "g");
      g.setAttribute("class", "node");
      g.setAttribute("tabindex", "0");
      g.setAttribute("role", "button");
      g.setAttribute("aria-label", `${KIND[n.kind]?.label || n.kind}: ${n.title} (${STATUS[n.status]?.label || n.status})`);
      const rad = radius(n);
      const ring = RING[n.status] || RING.unverified;
      const inactive = n.lifecycle !== "active";
      const halo = document.createElementNS(NS, "circle");
      halo.setAttribute("r", rad + 3.5);
      halo.style.fill = "none";
      halo.style.stroke = ring.stroke;
      halo.style.strokeWidth = n.status === "evidence_supported" || n.status === "contradicted" ? "2" : "1.5";
      if (ring.dash) halo.setAttribute("stroke-dasharray", ring.dash);
      const core = document.createElementNS(NS, "circle");
      core.setAttribute("r", rad);
      core.style.fill = inactive ? "var(--st-superseded)" : (KIND[n.kind]?.color || "var(--kind-note)");
      g.append(halo, core);
      if (opts.labels) {
        const t = document.createElementNS(NS, "text");
        t.setAttribute("x", rad + 7);
        t.setAttribute("y", 4);
        t.textContent = n.title.length > 34 ? `${n.title.slice(0, 32)}…` : n.title;
        const full = document.createElementNS(NS, "title");
        full.textContent = n.title;
        g.append(full);
        if (inactive) t.style.textDecoration = "line-through";
        g.append(t);
      }
      if (inactive) g.style.opacity = ".55";
      g.addEventListener("pointerenter", (ev) => { hoverId = n.id; focus(); showTip(n, ev); });
      g.addEventListener("pointermove", (ev) => showTip(n, ev));
      g.addEventListener("pointerleave", () => { hoverId = null; focus(); tip.hidden = true; });
      g.addEventListener("keydown", (ev) => { if (ev.key === "Enter") ctx.openRecord(n.id); });
      dragNode(g, n);
      nodeEls.set(n.id, g);
      return g;
    }));
    draw();
  }

  function radius(n) { return n.kind === "objective" ? 13 : 5 + (n.importance || 2) * 2; }

  function showTip(n, ev) {
    const box = wrap.getBoundingClientRect();
    clear(tip, h("div", { class: "k" }, `${KIND[n.kind]?.label || n.kind} · ${n.origin_model || "unknown"} · ${n.evidence_count} evidence`),
      h("div", { style: { margin: "3px 0 6px", fontWeight: 500 } }, n.title), statusPill(n.status));
    tip.hidden = false;
    const x = ev.clientX - box.left + 14;
    const y = ev.clientY - box.top + 14;
    tip.style.left = `${Math.min(x, box.width - 330)}px`;
    tip.style.top = `${Math.min(y, box.height - 90)}px`;
  }

  function focus() {
    if (!hoverId) {
      nodeEls.forEach((g) => g.classList.remove("dim"));
      edgeEls.forEach(({ line }) => line.classList.remove("dim"));
      return;
    }
    const near = new Set([hoverId]);
    visible.edges.forEach((e) => { if (e.source === hoverId) near.add(e.target); if (e.target === hoverId) near.add(e.source); });
    nodeEls.forEach((g, id) => g.classList.toggle("dim", !near.has(id)));
    edgeEls.forEach(({ e, line }) => line.classList.toggle("dim", e.source !== hoverId && e.target !== hoverId));
  }

  // ----------------------------------------------------------- simulation
  function tick() {
    const nodes = visible.nodes;
    const P = nodes.map((n) => pos.get(n.id));
    for (let i = 0; i < P.length; i++) {
      for (let j = i + 1; j < P.length; j++) {
        const a = P[i]; const b = P[j];
        let dx = b.x - a.x; let dy = b.y - a.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = Math.random() - .5; dy = Math.random() - .5; d2 = 1; }
        const d = Math.sqrt(d2);
        const f = (5200 / d2) * alpha;
        const fx = (dx / d) * f; const fy = (dy / d) * f;
        a.vx -= fx; a.vy -= fy; b.vx += fx; b.vy += fy;
      }
    }
    for (const e of visible.edges) {
      const a = pos.get(e.source); const b = pos.get(e.target);
      const dx = b.x - a.x; const dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = (d - 150) * 0.04 * alpha;
      const fx = (dx / d) * f; const fy = (dy / d) * f;
      a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
    }
    for (const p of P) {
      p.vx -= p.x * 0.008 * alpha;
      p.vy -= p.y * 0.011 * alpha;
      if (p.fixed) { p.vx = 0; p.vy = 0; continue; }
      p.vx *= 0.6; p.vy *= 0.6;
      p.x += Math.max(-30, Math.min(30, p.vx));
      p.y += Math.max(-30, Math.min(30, p.vy));
    }
    alpha *= 0.985;
  }

  function draw() {
    for (const { e, line } of edgeEls) {
      const a = pos.get(e.source); const b = pos.get(e.target);
      const target = visible.nodes.find((n) => n.id === e.target);
      const dx = b.x - a.x; const dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const off = radius(target) + 5;
      line.setAttribute("x1", a.x); line.setAttribute("y1", a.y);
      line.setAttribute("x2", b.x - (dx / d) * off); line.setAttribute("y2", b.y - (dy / d) * off);
    }
    nodeEls.forEach((g, id) => { const p = pos.get(id); g.setAttribute("transform", `translate(${p.x},${p.y})`); });
    stage.setAttribute("transform", `translate(${view.x},${view.y}) scale(${view.k})`);
  }

  function loop() {
    tick();
    draw();
    if (alpha > 0.01) raf = requestAnimationFrame(loop);
    else { raf = null; if (!fitted) { fitted = true; fit(true); } }
  }
  function reheat(a = 0.5) {
    alpha = Math.max(alpha, a);
    if (!raf) raf = requestAnimationFrame(loop);
  }

  function fit(animate) {
    if (!visible.nodes.length) return;
    const box = svg.getBoundingClientRect();
    if (!box.width) return;
    let x0 = Infinity; let y0 = Infinity; let x1 = -Infinity; let y1 = -Infinity;
    visible.nodes.forEach((n) => { const p = pos.get(n.id); x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y); x1 = Math.max(x1, p.x); y1 = Math.max(y1, p.y); });
    // Keep clear of the toolbar (top) and the legend (bottom-left, ~300px wide).
    const left = 330; const right = 40; const top = 70; const bottom = 40;
    const w = box.width - left - right; const hgt = box.height - top - bottom;
    const k = Math.max(0.35, Math.min(1, Math.min(w / Math.max(1, x1 - x0 + 220), hgt / Math.max(1, y1 - y0 + 30))));
    const target = { k, x: left + w / 2 - ((x0 + x1) / 2 + 90) * k, y: top + hgt / 2 - ((y0 + y1) / 2) * k };
    if (!animate || matchMedia("(prefers-reduced-motion: reduce)").matches) { view = target; draw(); return; }
    const from = { ...view }; const t0 = performance.now();
    const step = (t) => {
      const u = Math.min(1, (t - t0) / 420); const e = 1 - Math.pow(1 - u, 4);
      view = { k: from.k + (target.k - from.k) * e, x: from.x + (target.x - from.x) * e, y: from.y + (target.y - from.y) * e };
      draw();
      if (u < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  // --------------------------------------------------------- interaction
  function toWorld(cx, cy) {
    const box = svg.getBoundingClientRect();
    return { x: (cx - box.left - view.x) / view.k, y: (cy - box.top - view.y) / view.k };
  }

  function dragNode(g, n) {
    let moved = false;
    g.addEventListener("pointerdown", (ev) => {
      ev.stopPropagation();
      g.setPointerCapture(ev.pointerId);
      moved = false;
      const p = pos.get(n.id);
      p.fixed = true;
      const move = (e) => { const w = toWorld(e.clientX, e.clientY); p.x = w.x; p.y = w.y; moved = true; reheat(0.25); draw(); };
      const up = () => {
        g.removeEventListener("pointermove", move);
        g.removeEventListener("pointerup", up);
        p.fixed = false;
        if (!moved) ctx.openRecord(n.id);
      };
      g.addEventListener("pointermove", move);
      g.addEventListener("pointerup", up);
    });
  }

  svg.addEventListener("pointerdown", (ev) => {
    const start = { x: ev.clientX, y: ev.clientY, vx: view.x, vy: view.y };
    svg.setPointerCapture(ev.pointerId);
    svg.classList.add("panning");
    const move = (e) => { view.x = start.vx + e.clientX - start.x; view.y = start.vy + e.clientY - start.y; draw(); };
    const up = () => { svg.removeEventListener("pointermove", move); svg.removeEventListener("pointerup", up); svg.classList.remove("panning"); };
    svg.addEventListener("pointermove", move);
    svg.addEventListener("pointerup", up);
  });
  svg.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const box = svg.getBoundingClientRect();
    const mx = ev.clientX - box.left; const my = ev.clientY - box.top;
    const k = Math.max(0.2, Math.min(3, view.k * Math.exp(-ev.deltaY * 0.0015)));
    view.x = mx - ((mx - view.x) / view.k) * k;
    view.y = my - ((my - view.y) / view.k) * k;
    view.k = k;
    draw();
  }, { passive: false });

  // ---------------------------------------------------------------- data
  data = await api.graph(ctx.pid);
  if (!data.nodes.length) {
    return { el: h("div", { class: "view-inner" }, h("div", { class: "empty" }, h("strong", {}, "Nothing to map yet"),
      "Records and the links between them appear here once something is saved.")) };
  }
  build();
  // Settle the layout before first paint so the graph opens calm and centred, not in a heap.
  for (let i = 0; i < 300 && alpha > 0.01; i++) tick();
  alpha = 0.02;
  requestAnimationFrame(() => { fit(false); fitted = true; draw(); });

  return {
    el,
    cleanup: () => { if (raf) cancelAnimationFrame(raf); },
    update: async () => {
      data = await api.graph(ctx.pid);
      build();
      reheat(0.35);
    },
  };
}

function legend() {
  const dot = (color, dash, ring = color) => h("svg", { viewBox: "0 0 22 8" },
    h("circle", { cx: "11", cy: "4", r: "3", style: { fill: color } }));
  const line = (r) => h("svg", { viewBox: "0 0 22 8" },
    h("line", { x1: "1", y1: "4", x2: "21", y2: "4", style: { stroke: r.color, strokeWidth: "1.6" }, "stroke-dasharray": r.dash || null }));
  const ring = (k) => h("svg", { viewBox: "0 0 22 8" },
    h("circle", { cx: "11", cy: "4", r: "3", style: { fill: "none", stroke: RING[k].stroke, strokeWidth: "1.4" }, "stroke-dasharray": RING[k].dash ? "1.5 1.2" : null }));
  return h("div", { class: "graph-legend" },
    h("div", { class: "ttl" }, "Type (fill)"),
    ["finding", "decision", "assumption", "question", "objective", "task"].map((k) => h("span", {}, dot(KIND[k].color), KIND[k].label)),
    h("div", { class: "ttl" }, "Status (ring)"),
    ["evidence_supported", "unverified", "challenged", "contradicted"].map((k) => h("span", {}, ring(k), STATUS[k].label)),
    h("div", { class: "ttl" }, "Links"),
    ["supports", "challenges", "contradicts", "depends_on", "supersedes", "answers"].map((k) => h("span", {}, line(RELATION[k]), RELATION[k].label)));
}
