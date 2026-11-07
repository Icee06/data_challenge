/* Appeals: disputing a criterion and the reviewer queue
   Plain script sharing the page scope with app.js (no modules, so the page also works opened from disk). */
"use strict";

/* ---------- appeals + reviewer ---------- */
function initAppealModal() {
  $("btn-submit-appeal").addEventListener("click", submitAppeal);
  $("btn-reviewer").addEventListener("click", openReview);
}

function openAppeal(critId) {
  const d = sel(); if (!d) return alert("Select a dialog first.");
  if (!state.evals[d.id]) return alert("Run the evaluation first.");
  $("appeal-crit").innerHTML = state.checklist.map(c => `<option value="${esc(c.id)}" ${c.id === critId ? "selected" : ""}>${esc(c.name)}</option>`).join("");
  $("appeal-comment").value = ""; $("appeal-error").textContent = "";
  $("modal-appeal").classList.remove("hidden");
}
async function submitAppeal() {
  const comment = $("appeal-comment").value.trim();
  if (!comment) return $("appeal-error").textContent = "Write a comment so the reviewer knows what to check.";
  const critId = $("appeal-crit").value; let id = "a" + Date.now();
  if (state.source === "api") {
    const evaluationId = state.evals[state.selId]?.evaluationId;
    if (evaluationId == null) return $("appeal-error").textContent = "This evaluation has no backend id. Run the evaluation again.";
    try { id = String((await api(EP.appeal, { method: "POST", body: { evaluation_id: evaluationId, criterion_id: critId, comment } }))?.id ?? id); }
    catch (e) { return $("appeal-error").textContent = "Could not save the appeal: " + e.message; }
  }
  state.appeals.push({ id, dialogId: state.selId, critId, comment, status: "open", decision: null });
  persist(); closeModal("modal-appeal"); renderList(); renderEvaluation();
}
async function openReview() {
  $("modal-review").classList.remove("hidden");
  if (state.source === "api") {
    $("review-list").innerHTML = `<p class="text-slate-600 text-center py-8">Loading appeals…</p>`;
    await loadAppeals();
    // The queue needs each disputed dialog's transcript and evaluation, even if it was never opened here.
    const missing = [...new Set(state.appeals.map(p => p.dialogId))].filter(id => { const d = state.dialogs.find(x => x.id === id); return d && (!state.evals[id] || !d.messages.length); });
    await Promise.all(missing.map(id => loadDialogDetails(id).catch(e => console.warn("Could not load", id, e))));
  }
  renderReview();
}
function renderReview() {
  const items = [...state.appeals].reverse();
  const note = state.appealsNote ? `<p class="text-amber-700 text-sm">${esc(state.appealsNote)}</p>` : "";
  $("review-list").innerHTML = note + (items.length ? items.map(p => {
    const d = state.dialogs.find(x => x.id === p.dialogId), c = critOf(p.critId), r = state.evals[p.dialogId]?.results[p.critId];
    if (!d || !c || !r) return `<div class="bg-white border border-slate-300 rounded-lg p-4 text-sm text-slate-600">Appeal ${esc(p.id)} · dialog ${esc(p.dialogId)} · criterion ${esc(p.critId)}: ${!d ? `the dialog is not in the loaded dataset (${esc(state.group)})` : !c ? "the criterion is not in the current checklist" : "the evaluation could not be loaded"}. Comment: <i>${esc(p.comment)}</i></div>`;
    const idx = r.evidence ?? r.checked?.[0] ?? 0, end = r.evidence ?? r.checked?.[1] ?? idx;
    const frag = d.messages.slice(Math.max(0, idx - 1), Math.min(d.messages.length, end + 2));
    const opts = c.type === "yesno" ? ["YES", "NO"] : c.scale.map(String); if (c.allowNA) opts.push("NA");
    const before = scoreOf(d.id, false)?.final;
    return `<div class="bg-white border border-slate-300 rounded-lg p-4 space-y-2 text-sm" data-appeal="${esc(p.id)}">
      <div class="flex justify-between"><b>${esc(d.title)} · ${esc(c.name)}</b>
        <span class="${p.status === "open" ? "text-amber-700" : "text-emerald-700"}">${p.status === "open" ? "Open" : "Resolved"}</span></div>
      <div>AI answer: <b>${esc(ansLabel(c, r.answer))}</b> — ${esc(r.explanation)}</div>
      <div class="bg-slate-50 rounded p-2 space-y-1">${frag.map(m => `<div><b>${esc(ROLE[m.speaker] || m.speaker)}:</b> ${esc(m.text)}</div>`).join("")}</div>
      <div>Manager comment: <i>${esc(p.comment)}</i></div>
      ${p.status === "open" ? `<div class="grid grid-cols-3 gap-2 items-end">
        <label>Decision<select data-f="ans">${opts.map(o => `<option value="${o}" ${String(r.answer) === o ? "selected" : ""}>${esc(ansLabel(c, normAnswer(o)))}${String(r.answer) === o ? " (keep the AI answer)" : ""}</option>`).join("")}</select></label>
        <label class="col-span-2">Reason<input data-f="reason" placeholder="Why confirm or change?"></label></div>
        <p data-f="err" class="text-red-700 h-4"></p>
        <button data-decide="${esc(p.id)}" class="btn-primary px-3 py-1.5">Save decision</button>`
      : `<div>Decision: <b>${esc(ansLabel(c, p.decision.answer))}</b> — ${esc(p.decision.reason)}</div>
         <div>Original score <b>${before ?? "n/a"}</b> → final score <b>${scoreOf(d.id, true)?.final ?? "n/a"}</b></div>`}
    </div>`;
  }).join("") : `<p class="text-slate-600 text-center py-8">No appeals yet.</p>`);
}
async function decide(id) {
  const card = document.querySelector(`[data-appeal="${CSS.escape(id)}"]`), p = state.appeals.find(a => a.id === id);
  const err = card.querySelector('[data-f="err"]');
  const reason = card.querySelector('[data-f="reason"]').value.trim();
  if (!reason) return err.textContent = "Add a reason for the decision.";
  const decision = { answer: normAnswer(card.querySelector('[data-f="ans"]').value), reason };
  if (state.source === "api") {
    // The backend wants "confirm" when the answer stays as it is now, otherwise "change" with the new answer.
    const prev = state.appeals.filter(a => a.dialogId === p.dialogId && a.critId === p.critId && a.status === "resolved").at(-1);
    const current = prev ? prev.decision.answer : state.evals[p.dialogId]?.results[p.critId]?.answer;
    const body = String(decision.answer) === String(current)
      ? { decision: "confirm", reason }
      : { decision: "change", reason, answer: toServerAnswer(decision.answer) };
    try {
      const out = await api(EP.decision(id), { method: "POST", body });
      const d = state.dialogs.find(x => x.id === p.dialogId);
      state.evals[p.dialogId] = fromServer(out?.evaluation ?? await api(EP.evaluation(p.dialogId)), d);   // backend recalculates the total
    } catch (e) { return err.textContent = "Could not save the decision: " + e.message; }
  }
  p.decision = decision; p.status = "resolved";
  persist(); renderReview(); renderList(); renderEvaluation();
}


function closeModal(id) { $(id).classList.add("hidden"); }
