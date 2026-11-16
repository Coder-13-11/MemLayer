// Small pieces the workspace views share. Everything stored is inserted as text, never as HTML.
import { h, icon, toast } from "../dom.js";

export function copyText(text, message = "Copied") {
  return navigator.clipboard?.writeText(text).then(() => toast(message), () => toast("Could not copy", { error: true }));
}

/** A line of code or an address with a copy button. */
export function codeLine(text, label = "Copy") {
  return h("div", { class: "code" }, h("span", {}, text),
    h("button", { class: "icon-btn", type: "button", "aria-label": label, title: label, onclick: () => copyText(text) }, icon("copy")));
}

export function pageHead(title, sub, actions = []) {
  return h("div", { class: "page-head" },
    h("div", {}, h("h1", {}, title), sub ? h("p", { class: "sub" }, sub) : null),
    actions.length ? h("div", { class: "actions" }, actions) : null);
}

export function section(title, meta, ...children) {
  return h("div", { class: "section" },
    h("div", { class: "section-head" }, h("h2", {}, title), meta != null ? h("span", { class: "meta" }, meta) : null),
    ...children);
}

/** A plain table. `columns` is [{label, cell(row), cls?}]. */
export function table(columns, rows, { empty = "Nothing here yet." } = {}) {
  if (!rows.length) return h("p", { class: "empty" }, empty);
  return h("div", { class: "table-wrap" }, h("table", { class: "grid plain" },
    h("thead", {}, h("tr", {}, columns.map((c) => h("th", { class: c.cls || "", scope: "col" }, c.label)))),
    h("tbody", {}, rows.map((r) => h("tr", {}, columns.map((c) => h("td", { class: c.cls || "" }, c.cell(r))))))));
}

/** A button that asks once more in place before doing something that cannot be undone. */
export function confirmButton(label, confirmLabel, action, { cls = "btn sm danger" } = {}) {
  let armed = false, timer = null;
  const btn = h("button", { class: cls, type: "button" }, label);
  btn.addEventListener("click", async () => {
    if (!armed) {
      armed = true;
      btn.textContent = confirmLabel;
      btn.classList.add("confirm");
      timer = setTimeout(() => { armed = false; btn.textContent = label; btn.classList.remove("confirm"); }, 4000);
      return;
    }
    clearTimeout(timer);
    btn.disabled = true;
    try { await action(); } catch (e) { toast(e.message, { error: true }); btn.disabled = false; }
  });
  return btn;
}

/** Wire a form: disables its submit button while saving and shows the server's message on failure. */
export function onSubmit(form, handler) {
  const err = form.querySelector(".form-error") || form.appendChild(h("p", { class: "form-error", hidden: true, role: "alert" }));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const button = form.querySelector("[type=submit]");
    err.hidden = true;
    if (button) button.disabled = true;
    try { await handler(Object.fromEntries(new FormData(form)), form); }
    catch (ex) { err.textContent = ex.message; err.hidden = false; }
    finally { if (button) button.disabled = false; }
  });
  return form;
}

export function field(label, control, hint) {
  return h("label", { class: "field" }, h("span", {}, label), control, hint ? h("span", { class: "hint" }, hint) : null);
}

export function notice(text, kind = "") {
  return h("p", { class: `note ${kind}` }, text);
}

export const num = (n) => Number(n || 0).toLocaleString();
