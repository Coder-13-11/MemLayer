// The public site's behaviour: the carbon-copy form, the plan check, the receipt, copy buttons.
// Everything works without this file; it only adds the demonstrations.
(function () {
  "use strict";
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  // ------------------------------------------------ write once, every copy gets it
  var entry = $("[data-entry]");
  var copies = $$("[data-copy]");
  if (entry && copies.length) {
    var timers = [];
    var press = function (copy, text, delay) {
      timers.push(setTimeout(function () {
        var line = $("[data-carbon]", copy);
        line.textContent = text || " ";
        copy.classList.add("pressing");
        setTimeout(function () { copy.classList.remove("pressing"); }, 140);
      }, delay));
    };
    var transfer = function () {
      timers.forEach(clearTimeout);
      timers = [];
      copies.forEach(function (copy, i) { press(copy, entry.value, reduce ? 0 : 110 + i * 120); });
    };
    entry.addEventListener("input", transfer);

    // On arrival the line is typed in once, so the transfer is seen before anything is asked of the visitor.
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
        if (i < full.length) setTimeout(type, 46 + (full[i - 1] === " " ? 60 : 0));
      };
      setTimeout(type, 650);
    }
  }

  // --------------------------------------------------------------- the plan check
  var out = $("[data-check-out]");
  if (out) {
    var choices = $$("[data-check]");
    var results = null;
    var show = function (n) {
      if (!results || !results[n]) return;
      var r = results[n];
      $("[data-check-step]", out).textContent = r.step;
      $("[data-check-no]", out).textContent = "No. " + String(n + 1).padStart(4, "0");
      var stamp = $("[data-check-stamp]", out);
      stamp.textContent = r.stamp;
      stamp.className = "stamp" + (r.tone === "ok" ? " ok" : r.tone === "warn" ? " warn" : "");
      void stamp.offsetWidth;  // restart the stamp animation
      stamp.classList.add("in");
      var dl = $("[data-check-lines]", out);
      dl.textContent = "";
      r.lines.forEach(function (pair) {
        var dt = document.createElement("dt"), dd = document.createElement("dd");
        dt.textContent = pair[0];
        dd.textContent = pair[1];
        dl.appendChild(dt);
        dl.appendChild(dd);
      });
      $("[data-check-fine]", out).textContent = r.fine;
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
      toggle.textContent = on ? "Put it back" : "Change a decision after it was read";
      $("[data-till-foot]", till).textContent = on
        ? "1 record changed after this model read it. What it was told is no longer current."
        : "Every record is still as the model saw it.";
    });
  }

  // ------------------------------------------------------------------ copy buttons
  $$("[data-copy-text]").forEach(function (b) {
    b.addEventListener("click", function () {
      var done = function (label) { b.textContent = label; setTimeout(function () { b.textContent = "Copy"; }, 1600); };
      if (!navigator.clipboard) return done("Select it");
      navigator.clipboard.writeText(b.dataset.copyText).then(function () { done("Copied"); }, function () { done("Select it"); });
    });
  });
})();
