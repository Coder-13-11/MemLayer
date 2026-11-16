// Applied before first paint so a saved light/dark choice does not flash. Loaded as a file, not inline:
// the hosted service's content security policy allows scripts from this origin only.
try {
  const t = localStorage.getItem("nx-theme");
  if (t) document.documentElement.dataset.theme = t;
} catch (e) { /* storage unavailable */ }
