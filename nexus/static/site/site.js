// The public site's behaviour: live sync in the hero, ledger filters, the plan check, the receipt, tabs,
// copy buttons and scroll reveals. Everything on the page reads without this file.
(function () {
  "use strict";
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  document.documentElement.classList.add("js");

  // --------------------------------------------- write once, every agent gets it
  var entry = $("[data-entry]");
  var copies = $$("[data-copy]");
  var consoleEl = $("[data-form]");
  if (entry && copies.length) {
    var timers = [];
    var transfer = function () {
      timers.forEach(clearTimeout);
      timers = [];
      if (consoleEl && !reduce) {
        consoleEl.classList.remove("sending");
        void consoleEl.offsetWidth;
        consoleEl.classList.add("sending");
      }
      copies.forEach(function (copy, i) {
        var sync = $("[data-sync]", copy);
        if (sync) sync.textContent = "syncing";
        copy.classList.add("pressing");
        timers.push(setTimeout(function () {
          $("[data-carbon]", copy).textContent = entry.value;
          if (sync) sync.textContent = "synced";
          copy.classList.remove("pressing");
        }, reduce ? 0 : 160 + i * 110));
      });
    };
    entry.addEventListener("input", transfer);

    // On arrival the line is typed once, so the sync is seen before anything is asked of the visitor.
    var full = entry.value;
    var touched = false;
    ["pointerdown", "keydown", "focus"].forEach(function (ev) {
      entry.addEventListener(ev, function () { touched = true; }, { once: true });
    });
    if (!reduce) {
      entry.value = "";
      transfer();
      var i = 0;
      var type = function () {
        if (touched) {
          if (entry.value === full.slice(0, i)) { entry.value = full; transfer(); }
          return;
        }
        i += 1;
        entry.value = full.slice(0, i);
        transfer();
        if (i < full.length) setTimeout(type, 52 + (full[i - 1] === " " ? 70 : 0));
      };
      setTimeout(type, 700);
    }
  }

  // ---------------------------------------------------------------- ledger filter
  var ledger = $("[data-ledger]");
  if (ledger) {
    var rows = $$("tr", ledger);
    var count = $("[data-ledger-count]");
    $$("[data-filter]").forEach(function (b) {
      b.addEventListener("click", function () {
        var want = b.dataset.filter, shown = 0;
        rows.forEach(function (r) {
          var on = want === "all" || r.dataset.state === want;
          r.hidden = !on;
          shown += on ? 1 : 0;
        });
        $$("[data-filter]").forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); });
        if (count) count.textContent = shown + " of " + rows.length + " entries";
      });
    });
  }

  // --------------------------------------------------------------- the plan check
  var out = $("[data-check-out]");
  if (out) {
    var choices = $$("[data-check]");
    var results = null;
    var BADGE = { bad: ["BLOCKED", "2", "A CI step can fail on it."], warn: ["WARNING", "0", "Not judged. The agent is told to read these first."],
      ok: ["PASS", "0", "Nothing matched. That is not confirmation the step is right."] };
    var show = function (n) {
      if (!results || !results[n]) return;
      var r = results[n], b = BADGE[r.tone] || BADGE.ok;
      $("[data-check-step]", out).textContent = r.step;
      var verdict = $("[data-check-verdict]", out);
      verdict.className = "verdict " + (BADGE[r.tone] ? r.tone : "ok");
      void verdict.offsetWidth;  // restart the entrance
      verdict.classList.add("in");
      $("[data-check-badge]", out).textContent = b[0];
      $("[data-check-stamp]", out).textContent = r.stamp;
      $("[data-check-exit]", out).textContent = b[1];
      $("[data-check-fine]", out).textContent = b[2];
      var dl = $("[data-check-lines]", out);
      dl.textContent = "";
      (r.lines.length ? r.lines : [["Result", "No entry in the record matches this step."]]).forEach(function (pair) {
        var dt = document.createElement("dt"), dd = document.createElement("dd");
        dt.textContent = pair[0];
        dd.textContent = pair[1];
        dl.appendChild(dt);
        dl.appendChild(dd);
      });
      choices.forEach(function (c, i) { c.setAttribute("aria-pressed", String(i === n)); });
    };
    choices.forEach(function (c) { c.addEventListener("click", function () { show(Number(c.dataset.check)); }); });
    // The results come from the product's own check, run on the example project (scripts/build_site_demo.py).
    fetch("/static/site/demo.json").then(function (r) { return r.json(); }).then(function (d) {
      results = d.results;
    }).catch(function () { choices.forEach(function (c, i) { if (i) c.disabled = true; }); });
  }

  // ------------------------------------------------------------------ the receipt
  var till = $("[data-till]");
  var toggle = $("[data-till-toggle]");
  if (till && toggle) {
    toggle.addEventListener("click", function () {
      var on = toggle.getAttribute("aria-pressed") !== "true";
      toggle.setAttribute("aria-pressed", String(on));
      till.classList.toggle("changed", on);
      $("[data-till-label]", toggle).textContent = on ? "Put it back" : "Simulate: change a decision after it was read";
      var foot = $("[data-till-foot]", till);
      foot.lastChild.textContent = on
        ? "1 entry changed after this model read it. What it was told is no longer current."
        : "Every entry is still as the model saw it.";
    });
  }

  // ------------------------------------------------------------------------- tabs
  $$("[data-tabs]").forEach(function (deck) {
    var tabs = $$("[role=tab]", deck);
    var pick = function (tab, focus) {
      tabs.forEach(function (t) {
        var on = t === tab;
        t.setAttribute("aria-selected", String(on));
        t.tabIndex = on ? 0 : -1;
        document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
      });
      if (focus) tab.focus();
    };
    tabs.forEach(function (t, i) {
      t.addEventListener("click", function () { pick(t); });
      t.addEventListener("keydown", function (e) {
        if (e.key === "ArrowRight") pick(tabs[(i + 1) % tabs.length], true);
        if (e.key === "ArrowLeft") pick(tabs[(i + tabs.length - 1) % tabs.length], true);
      });
    });
  });

  // ------------------------------------------------------------------ copy buttons
  $$("[data-copy-text]").forEach(function (b) {
    b.addEventListener("click", function () {
      var done = function (label) { b.textContent = label; setTimeout(function () { b.textContent = "Copy"; }, 1600); };
      if (!navigator.clipboard) return done("Select it");
      navigator.clipboard.writeText(b.dataset.copyText).then(function () { done("Copied"); }, function () { done("Select it"); });
    });
  });

  // ---------------------------------------------------------------- scroll reveals
  var reveals = $$(".reveal");
  if (reduce || !("IntersectionObserver" in window)) {
    reveals.forEach(function (el) { el.classList.add("seen"); });
  } else {
    var io = new IntersectionObserver(function (items) {
      items.forEach(function (it) {
        if (it.isIntersecting) { it.target.classList.add("seen"); io.unobserve(it.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    reveals.forEach(function (el) { io.observe(el); });
  }
})();
