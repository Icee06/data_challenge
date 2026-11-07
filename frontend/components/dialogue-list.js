/* Conversation list: one row per conversation with language, channel and score
   Plain script sharing the page scope with app.js (no modules, so the page also works opened from disk). */
"use strict";

const isDone = d => !!state.evals[d.id] || d.score != null;
const band = s => s == null ? "" : s >= 80 ? "band-good" : s >= 50 ? "band-mid" : "band-low";
const TYPE = { chat: "Chat", call: "Call" };
// Selecting with the keyboard: Enter or Space on a focused row.
function initDialogueList() {
  $("dialog-list").addEventListener("keydown", e => {
    const t = e.target.closest("[data-dialog]");
    if (t && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); selectDialog(t.dataset.dialog); }
  });
}

function renderList() {
  const lang = $("filter-lang").value, type = $("filter-type").value, status = $("filter-status").value, q = $("filter-search").value.trim().toLowerCase();
  const list = state.dialogs.filter(d => (lang === "ALL" || d.language === lang) && (type === "ALL" || d.type === type)
    && (status === "ALL" || (status === "done") === isDone(d)) && (!q || d.id.toLowerCase().includes(q)));
  state.shown = list;
  $("dialog-count").textContent = list.length === state.dialogs.length ? list.length : `${list.length} of ${state.dialogs.length}`;
  $("dialog-list").innerHTML = state.loadError ? `<p class="text-center text-base px-3 py-8" style="color:var(--fail)">${esc(state.loadError)}</p>`
    : list.length ? list.map(d => {
    const sc = state.evals[d.id] ? scoreOf(d.id, true).final : d.score;
    const title = d.title.startsWith(d.id + ": ") ? d.title.slice(d.id.length + 2) : d.title;
    const pill = d.failed ? `<span class="pill pill-fail" title="${esc(d.failed)}">Failed</span>`
      : sc == null ? `<span class="pill pill-none">Not evaluated</span>` : `<span class="pill ${band(sc)}">${sc}</span>`;
    return `<div data-dialog="${esc(d.id)}" role="button" tabindex="0" aria-current="${d.id === state.selId}" class="dlg ${d.id === state.selId ? "is-sel" : ""}">
      <div class="dlg-top"><span class="dlg-id">${esc(d.id)}</span>${pill}</div>
      <div class="dlg-title">${esc(title)}</div>
      <div class="dlg-tags"><span class="tag">${esc(d.language)}</span><span class="tag">${icon(d.type === "call" ? "call" : "chat")}${esc(TYPE[d.type] || d.type)}</span></div></div>`;
  }).join("") : `<p class="text-center text-base py-8" style="color:var(--muted)">No conversations match these filters.</p>`;
  const pending = list.filter(d => !isDone(d)).length, btn = $("btn-batch");
  btn.disabled = !!batch || !pending;
  setIconLabel(btn, "bolt", batch ? "Evaluating…" : pending ? `Evaluate ${pending} pending` : list.length ? "All shown conversations evaluated" : "Nothing to evaluate");
  $("appeal-count").textContent = state.appeals.filter(a => a.status === "open").length;
}

