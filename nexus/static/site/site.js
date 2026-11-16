// Public site behaviour: the Fig. 1 demo, citations that light records, and the invite form.
// All demo content is static and synthetic; everything is inserted as text, never as HTML.
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const SVG = "http://www.w3.org/2000/svg";

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  // ------------------------------------------------------------- Fig. 1 script
  const MODELS = {
    // Numbered as in the byline; no assistant gets a brand colour.
    chatgpt: { name: "ChatGPT", n: "1" },
    claude: { name: "Claude", n: "2" },
    gemini: { name: "Gemini", n: "3" },
  };
  const STATUS = {
    evidence_supported: "Supported", unverified: "Unverified", challenged: "Challenged", contradicted: "Contradicted",
  };
  const ORDER = ["evidence_supported", "unverified", "challenged", "contradicted"];

  // Each chapter starts from the state the previous one left, so any chapter can be shown on its own.
  const CHAPTERS = [
    {
      model: "chatgpt", when: "Monday, 31 Oct",
      steps: [
        { t: "user", text: "Save what we found about free imagery for the wetland project." },
        { t: "call", tool: "save_memory", arg: "finding · with source", res: "filed rec_2c77",
          rec: { id: "rec_2c77", title: "Sentinel-2 provides free multispectral imagery at 10 m", s: "evidence_supported", by: "chatgpt" } },
        { t: "call", tool: "save_memory", arg: "finding · subject revisit_days = 5", res: "filed rec_81d0",
          rec: { id: "rec_81d0", title: "Sentinel-2 revisits the same location every 5 days", s: "unverified", by: "chatgpt" } },
        { t: "call", tool: "save_memory", arg: "decision", res: "filed rec_4f1a",
          rec: { id: "rec_4f1a", title: "Build one annual median composite per year", s: "unverified", by: "chatgpt" } },
        { t: "say", parts: ["Saved three records. Only the imagery claim has a cited source, so it is the only one marked supported", { c: ["rec_2c77"] }, "."] },
      ],
    },
    {
      model: "claude", when: "Wednesday, 2 Nov",
      steps: [
        { t: "user", text: "Pick up the wetland project. How often will we actually get clear images?" },
        { t: "call", tool: "get_project_context", arg: "Wetland loss, Louisiana coast", res: "3 records · 1 decision" },
        { t: "call", tool: "save_memory", arg: "finding · subject revisit_days = 10", res: "filed rec_9b20",
          rec: { id: "rec_9b20", title: "After cloud masking, usable revisit is about 10 days", s: "unverified", by: "claude" } },
        { t: "call", tool: "analyze_gaps", arg: "Wetland loss, Louisiana coast", res: "1 possible contradiction", flagged: true,
          flag: ["rec_81d0", "rec_9b20"] },
        { t: "say", parts: ["The project says imagery arrives every 5 days", { c: ["rec_81d0"] }, ", but with cloud cover the usable rate is closer to 10", { c: ["rec_9b20"] }, ". I've kept both and flagged the conflict for you."] },
      ],
    },
    {
      model: "gemini", when: "Friday, 4 Nov",
      steps: [
        { t: "user", text: "Can we map yearly wetland loss from 2017 with what we know?" },
        { t: "call", tool: "search_memory", arg: "\"imagery revisit composite\"", res: "4 records" },
        { t: "say", parts: ["Yes. Free 10 m imagery covers the coast", { c: ["rec_2c77"] }, ", and one composite per year", { c: ["rec_4f1a"] }, " works around clouds. Plan on about 10 days between usable images", { c: ["rec_9b20"] }, "; the 5-day figure", { c: ["rec_81d0"] }, " is disputed, so check it before relying on it."] },
      ],
    },
  ];

  function stateBefore(ch) {
    const recs = []; let flag = null;
    for (let i = 0; i < ch; i++) for (const s of CHAPTERS[i].steps) {
      if (s.rec) recs.push({ ...s.rec });
      if (s.flag) { flag = s.flag; applyFlag(recs, flag); }
    }
    return { recs, flag };
  }
  function applyFlag(recs, flag) {
    for (const r of recs) {
      if (r.id === flag[0]) r.s = "contradicted";
      if (r.id === flag[1]) r.s = "challenged";
    }
  }

  function initDemo() {
    const fig = $("#fig1");
    if (!fig) return;
    const tabs = $$(".chapters button", fig);
    const playBtn = $(".play", fig);
    const transcript = $(".transcript", fig);
    const rowsEl = $(".ledger-rows", fig);
    const ledger = $(".ledger", fig);
    const trust = $(".trust", ledger);
    const count = $(".ledger-count", ledger);
    const whoDot = $(".who-dot", fig), whoName = $(".who", fig), when = $(".session-head .when", fig);

    let ch = 0, step = 0, timer = null, playing = !reduced, visible = false, recs = [], flag = null, started = false;

    function renderLedger(newId) {
      rowsEl.replaceChildren();
      for (const r of recs) {
        const li = el("li", "lrow");
        li.dataset.id = r.id;
        if (r.id === newId && !reduced) li.classList.add("new");
        const dot = el("span", `dot s-${r.s}`);
        const n = el("span", "n");
        const ttl = el("span", "ttl", r.title);
        const st = el("span", "st-label", STATUS[r.s]);
        const meta = el("span", "meta");
        meta.append(el("span", "id", r.id), el("span", "", MODELS[r.by].name));
        li.append(n, dot, ttl, st, meta);
        rowsEl.append(li);
      }
      count.textContent = `${recs.length} record${recs.length === 1 ? "" : "s"}`;
      trust.replaceChildren();
      const counts = {};
      for (const r of recs) counts[r.s] = (counts[r.s] || 0) + 1;
      for (const s of ORDER) if (counts[s]) {
        const seg = el("span", `s-${s}`);
        seg.dataset.s = s;
        seg.style.flexGrow = counts[s];
        trust.append(seg);
      }
      trust.setAttribute("aria-label", ORDER.filter((s) => counts[s]).map((s) => `${counts[s]} ${STATUS[s].toLowerCase()}`).join(", ") || "No records yet");
      drawFlag();
    }

    function drawFlag() {
      $(".flag-svg", rowsEl)?.remove();
      if (!flag) return;
      const a = rowsEl.querySelector(`[data-id="${flag[0]}"]`), b = rowsEl.querySelector(`[data-id="${flag[1]}"]`);
      if (!a || !b) return;
      const y1 = a.offsetTop + 17, y2 = b.offsetTop + 17;
      const svg = document.createElementNS(SVG, "svg");
      svg.setAttribute("class", "flag-svg");
      svg.setAttribute("height", String(y2 + 4));
      const p = document.createElementNS(SVG, "path");
      p.setAttribute("d", `M14 ${y1} H6 V${y2} H14`);
      svg.append(p);
      rowsEl.append(svg);
    }

    function light(ids, on) {
      ledger.classList.toggle("citing", on);
      $$(".lrow", rowsEl).forEach((r) => {
        const k = ids.indexOf(r.dataset.id);
        r.classList.toggle("lit", on && k >= 0);
      });
    }

    function stepNode(s) {
      if (s.t === "user") return el("li", "t-user", s.text);
      if (s.t === "call") {
        const li = el("li", "t-call");
        const st = document.createElementNS(SVG, "svg");
        st.setAttribute("class", "st"); st.setAttribute("viewBox", "0 0 14 14");
        const c = document.createElementNS(SVG, "circle"); c.setAttribute("cx", "7"); c.setAttribute("cy", "7"); c.setAttribute("r", "5.5");
        const arc = c.cloneNode(); arc.setAttribute("class", "arc");
        const ok = document.createElementNS(SVG, "path"); ok.setAttribute("class", "ok"); ok.setAttribute("d", "M3 7.5l2.5 2.5L11 4.5");
        st.append(c, arc, ok);
        const head = el("span");
        head.append(el("span", "tool", s.tool), el("span", "arg", ` · ${s.arg}`));
        li.append(st, head, el("span", `res${s.flagged ? " flag" : ""}`, `→ ${s.res}`));
        $(".res", li).hidden = true;
        return li;
      }
      const li = el("li", "t-say");
      let n = 0;
      for (const p of s.parts) {
        if (typeof p === "string") { li.append(document.createTextNode(p)); continue; }
        // Keep a citation on the same line as the word it cites.
        const last = li.lastChild;
        const hold = el("span", "nowrap");
        if (last && last.nodeType === Node.TEXT_NODE) {
          const m = last.data.match(/(\S+)$/);
          if (m) { last.data = last.data.slice(0, -m[1].length); hold.append(m[1]); }
        }
        const btn = el("button", "c", `[${p.c.map(() => ++n).join(",")}]`);
        btn.type = "button";
        btn.setAttribute("aria-label", `Show cited record ${p.c.join(", ")}`);
        const ids = p.c;
        btn.addEventListener("mouseenter", () => { pause(true); light(ids, true); });
        btn.addEventListener("focus", () => { pause(true); light(ids, true); });
        btn.addEventListener("mouseleave", () => light(citedAll(li), true));
        btn.addEventListener("blur", () => light(citedAll(li), true));
        btn.dataset.ids = ids.join(" ");
        hold.append(btn);
        li.append(hold);
      }
      return li;
    }
    const citedAll = (li) => $$("button.c", li).flatMap((b) => b.dataset.ids.split(" "));

    function numberCited(li) {
      const ids = citedAll(li);
      $$(".lrow", rowsEl).forEach((r) => {
        const k = ids.indexOf(r.dataset.id);
        r.classList.toggle("cited", k >= 0);
        $(".n", r).textContent = k >= 0 ? String(k + 1) : "";
      });
    }

    function applyStep(s, animate) {
      const node = stepNode(s);
      if (!animate) node.style.animation = "none", node.style.opacity = 1, node.style.transform = "none";
      transcript.append(node);
      transcript.scrollTop = transcript.scrollHeight;
      if (s.t === "call") {
        const finish = () => {
          node.classList.add("done");
          $(".res", node).hidden = false;
          let newId = null;
          if (s.rec) { recs.push({ ...s.rec }); newId = s.rec.id; }
          if (s.flag) { flag = s.flag; applyFlag(recs, flag); }
          if (s.rec || s.flag) renderLedger(newId);
          transcript.scrollTop = transcript.scrollHeight;
        };
        return animate ? finish : (finish(), null);
      }
      if (s.t === "say") {
        const ids = citedAll(node);
        numberCited(node);
        light(ids, true);
      }
      return null;
    }

    function showSpeaker(i) {
      const m = MODELS[CHAPTERS[i].model];
      whoName.textContent = m.name; whoDot.dataset.l = m.n; when.textContent = CHAPTERS[i].when;
    }

    function setChapter(i, { autoplay = playing } = {}) {
      clearTimeout(timer);
      ch = i; step = 0;
      const st = stateBefore(i);
      recs = st.recs; flag = st.flag;
      transcript.replaceChildren();
      light([], false);
      const m = MODELS[CHAPTERS[i].model];
      showSpeaker(i);
      tabs.forEach((t, k) => { t.setAttribute("aria-selected", String(k === i)); t.style.setProperty("--p", k < i ? 1 : 0); });
      renderLedger(null);
      if (reduced || !autoplay) { while (step < CHAPTERS[ch].steps.length) applyStep(CHAPTERS[ch].steps[step++], false); progress(); return; }
      next();
    }

    function progress() {
      tabs[ch].style.setProperty("--p", step / CHAPTERS[ch].steps.length);
    }

    function next() {
      clearTimeout(timer);
      if (!playing || !visible) return;
      const steps = CHAPTERS[ch].steps;
      if (step >= steps.length) {
        timer = setTimeout(() => setChapter((ch + 1) % CHAPTERS.length), 4200);
        return;
      }
      const s = steps[step++];
      progress();
      const finish = applyStep(s, true);
      if (finish) timer = setTimeout(() => { finish(); timer = setTimeout(next, 650); }, s.flagged ? 1500 : 1000);
      else timer = setTimeout(next, s.t === "user" ? 1100 : 2600);
    }

    function pause(p) {
      playing = !p;
      playBtn.classList.toggle("paused", p);
      playBtn.setAttribute("aria-label", p ? "Play demo" : "Pause demo");
      $("span", playBtn).textContent = p ? "Play" : "Pause";
      if (p) clearTimeout(timer); else next();
    }

    tabs.forEach((t, i) => t.addEventListener("click", () => setChapter(i)));
    playBtn.addEventListener("click", () => pause(playing));
    if (reduced) pause(true);

    new IntersectionObserver(([e]) => {
      visible = e.isIntersecting;
      if (visible && !started) { started = true; setChapter(0); }
      else if (visible) next(); else clearTimeout(timer);
    }, { threshold: 0.35 }).observe(fig);
    if (reduced) { started = true; setChapter(0); }
    else { showSpeaker(0); tabs[0].setAttribute("aria-selected", "true"); renderLedger(null); }
  }

  // ------------------------------------------------------------- Fig. 2 & 3
  function initSpecimen() {
    const fig = $(".specimen");
    if (!fig) return;
    const set = (part, on) => $$(`[data-part="${part}"]`, fig).forEach((n) => n.classList.toggle("on", on));
    $$("[data-part]", fig).forEach((n) => {
      n.addEventListener("mouseenter", () => set(n.dataset.part, true));
      n.addEventListener("mouseleave", () => set(n.dataset.part, false));
    });
    // A slow walk through the labels the first time the figure is seen.
    if (reduced) return;
    const parts = ["a", "b", "c", "d", "e", "f"];
    new IntersectionObserver(([e], obs) => {
      if (!e.isIntersecting) return;
      obs.disconnect();
      parts.forEach((p, i) => {
        setTimeout(() => set(p, true), 300 + i * 650);
        setTimeout(() => set(p, false), 300 + i * 650 + 900);
      });
    }, { threshold: 0.5 }).observe(fig);
  }

  function initGaps() {
    const fig = $(".gaps-fig");
    if (!fig) return;
    const list = $(".gap-list", fig);
    let current = null;
    const pick = (s) => {
      current = current === s ? null : s;
      list.classList.toggle("filtered", !!current);
      $$("li", list).forEach((li) => li.classList.toggle("match", li.dataset.s === current));
      $$(".trust-lg button", fig).forEach((b) => b.classList.toggle("on", b.dataset.s === current));
    };
    $$("[data-s]", fig).forEach((n) => { if (!n.closest(".gap-list")) n.addEventListener("click", () => pick(n.dataset.s)); });
  }

  // ------------------------------------------------------- byline & copy
  function initByline() {
    $$(".byline a[data-row]").forEach((a) => a.addEventListener("click", () => {
      const row = document.getElementById(a.dataset.row);
      if (!row) return;
      row.classList.add("flash");
      setTimeout(() => row.classList.remove("flash"), 60);
    }));
  }
  function initCopy() {
    $$(".copy").forEach((b) => b.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(b.dataset.copy); } catch { return; }
      const label = b.textContent;
      b.classList.add("copied"); b.textContent = "Copied";
      setTimeout(() => { b.classList.remove("copied"); b.textContent = label; }, 1600);
    }));
  }

  // ---------------------------------------------------------------- invites
  function initForms() {
    $$(".beta-form").forEach((form) => {
      const msg = $(".form-msg", form);
      const email = $("input[type=email]", form);
      const btn = $("button[type=submit]", form);
      form.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        msg.classList.remove("err");
        if (!email.value.trim() || !email.checkValidity()) {
          email.setAttribute("aria-invalid", "true");
          msg.classList.add("err");
          msg.textContent = "Enter an email address we can send the invite to.";
          email.focus();
          return;
        }
        email.removeAttribute("aria-invalid");
        btn.disabled = true; btn.textContent = "Sending…";
        try {
          const res = await fetch("/beta", {
            method: "POST", headers: { "content-type": "application/json" },
            body: JSON.stringify(Object.fromEntries(new FormData(form))),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.error || "Something went wrong. Try again in a minute.");
          form.classList.add("done");
          msg.textContent = data.message || "Thank you. You're on the list.";
        } catch (e) {
          msg.classList.add("err");
          msg.textContent = e.message || "Couldn't reach the server. Check your connection and try again.";
        } finally {
          btn.disabled = false; btn.textContent = "Request an invite";
        }
      });
    });
  }

  initDemo(); initSpecimen(); initGaps(); initByline(); initCopy(); initForms();
})();
