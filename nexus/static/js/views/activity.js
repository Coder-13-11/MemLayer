import { api } from "../api.js";
import { h, clear } from "../dom.js";
import { feed } from "./common.js";

export async function render(ctx) {
  const el = h("div", { class: "view-inner" });
  const slot = h("div");
  el.append(
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Activity"),
      h("p", { class: "sub" }, "Everything saved, changed, linked or imported in this project, by any AI client or by you. New entries appear live."))),
    slot);
  const load = async () => clear(slot, feed((await api.activity({ project: ctx.pid, limit: 200 })).activity, ctx));
  await load();
  return { el, update: load };
}
