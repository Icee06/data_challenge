/* Checklist editor: criteria, weights, scales, N/A rules and saving a new version
   Plain script sharing the page scope with app.js (no modules, so the page also works opened from disk). */
"use strict";

/* ---------- checklist editor ---------- */
function initChecklistEditor() {
  $("btn-edit-checklist").addEventListener("click", () => {
    state.draft = clone(state.checklist); $("checklist-error").textContent = "";
    renderChecklistEditor(); $("modal-checklist").classList.remove("hidden");
  });
  $("btn-add-crit").addEventListener("click", () => {
    state.draft.push({ id: "c" + Date.now(), name: "New criterion", section: "", type: "yesno", weight: 0, allowNA: false, scale: [], scaleDesc: {}, flag: false, naRule: "", description: "" });
    renderChecklistEditor(); $("checklist-rows").lastElementChild?.scrollIntoView({ block: "nearest" });
  });
  $("btn-reset-crit").addEventListener("click", () => { state.draft = clone(DEFAULT_CHECKLIST); renderChecklistEditor(); });
  $("btn-save-checklist").addEventListener("click", saveChecklist);
  $("checklist-rows").addEventListener("input", editField);
  $("checklist-rows").addEventListener("change", editField);
}

function weightNote(list) {
  const t = list.filter(c => !c.flag).reduce((s, c) => s + (Number(c.weight) || 0), 0);
  return `<span class="${t === 100 ? "text-emerald-700" : "text-amber-700"}">Total weight: ${t}${t === 100 ? "" : " (scores are scaled to 100)"}</span>`;
}
function renderChecklistEditor() {
  $("weight-sum").innerHTML = weightNote(state.draft);
  $("checklist-rows").innerHTML = state.draft.map((c, i) => `
    <div class="bg-white border border-slate-300 rounded-lg p-3 grid grid-cols-12 gap-2 text-xs items-end" data-i="${i}">
      <label class="col-span-4">Name<input data-f="name" value="${esc(c.name)}"></label>
      <label class="col-span-3">Section<input data-f="section" value="${esc(c.section)}"></label>
      <label class="col-span-2">Type<select data-f="type"><option value="yesno" ${c.type === "yesno" ? "selected" : ""}>Yes / No</option><option value="scale" ${c.type === "scale" ? "selected" : ""}>Scale</option></select></label>
      <label class="col-span-1">Weight<input data-f="weight" type="number" min="0" value="${c.weight}" ${c.flag ? "disabled" : ""}></label>
      <button data-del="${i}" class="col-span-2 text-red-700 hover:underline text-right">Remove</button>
      <label class="col-span-12">What it checks (the AI scores from this text)<textarea data-f="description" rows="2" placeholder="Да — оператор … / Yes — the operator …">${esc(c.description)}</textarea></label>
      <label class="col-span-3 ${c.type === "scale" ? "" : "opacity-40"}">Scale steps (high → low)<input data-f="scale" value="${c.scale.join(", ")}" ${c.type === "scale" ? "" : "disabled"}></label>
      <label class="col-span-4">N/A condition<input data-f="naRule" value="${esc(c.naRule)}" ${c.allowNA ? "" : "disabled"}></label>
      <label class="col-span-2 flex items-center gap-2"><input data-f="allowNA" type="checkbox" class="!w-auto" ${c.allowNA ? "checked" : ""}>N/A allowed</label>
      <label class="col-span-3 flex items-center gap-2"><input data-f="flag" type="checkbox" class="!w-auto" ${c.flag ? "checked" : ""}>Critical flag (no points)</label>
      ${c.type === "scale" ? `<div class="col-span-12 grid gap-1"><span>What earns each step</span>${[...c.scale].sort((a, b) => b - a).map(s => `
        <label class="flex items-center gap-2"><span class="w-8 shrink-0 text-right font-semibold">${s}</span><input data-f="scaleDesc" data-step="${s}" value="${esc(c.scaleDesc?.[s] ?? "")}" placeholder="When does the operator get ${s} points?"></label>`).join("")}</div>` : ""}
    </div>`).join("");
}
function editField(e) {
  const row = e.target.closest("[data-i]"), f = e.target.dataset.f; if (!row || !f) return;
  const c = state.draft[+row.dataset.i];
  const v = e.target.type === "checkbox" ? e.target.checked : e.target.value;
  if (f === "scale") c.scale = v.split(",").map(s => s.trim()).filter(s => s !== "").map(Number).filter(n => !isNaN(n));
  else if (f === "scaleDesc") c.scaleDesc = { ...c.scaleDesc, [e.target.dataset.step]: v };
  else if (f === "weight") c.weight = Number(v) || 0;
  else c[f] = v;
  if (e.type === "change" && ["type", "allowNA", "flag", "scale"].includes(f)) { if (c.type === "scale" && !c.scale.length) c.scale = [10, 5, 0]; renderChecklistEditor(); }
  else if (f === "weight") $("weight-sum").innerHTML = weightNote(state.draft);
}
async function saveChecklist() {
  const err = $("checklist-error"); err.textContent = "";
  const ids = new Set();
  for (const c of state.draft) {
    const n = c.name.trim() || "Unnamed criterion";
    if (!c.name.trim()) return err.textContent = "Every criterion needs a name.";
    if (ids.has(c.id)) return err.textContent = `“${n}”: duplicate criterion id ${c.id}.`; ids.add(c.id);
    if (!c.description?.trim()) return err.textContent = `“${n}”: describe what it checks. The AI scores from this text.`;
    if (c.type === "scale" && new Set(c.scale).size < 2) return err.textContent = `“${n}”: a scale needs at least two different steps.`;
    if (c.type === "scale" && c.scale.some(s => !c.scaleDesc?.[s]?.trim())) return err.textContent = `“${n}”: describe what earns each scale step.`;
    if (c.allowNA && !c.naRule.trim()) return err.textContent = `“${n}”: write the N/A condition or untick “N/A allowed”.`;
    if (!c.flag && !(Number(c.weight) > 0)) return err.textContent = `“${n}”: give it a weight above 0, or mark it as a gross-error flag.`;
  }
  if (!state.draft.some(c => !c.flag && c.weight > 0)) return err.textContent = "At least one scored criterion with a weight is required.";
  state.draft.forEach(c => {
    if (c.type === "scale") { c.scale = [...new Set(c.scale)].sort((a, b) => b - a); c.scaleDesc = Object.fromEntries(c.scale.map(s => [s, c.scaleDesc[s].trim()])); }
    else { c.scale = []; c.scaleDesc = {}; }
    if (c.flag) c.weight = 0;
    if (!c.allowNA) c.naRule = "";
  });
  if (state.source === "api") {
    try {
      const j = await api(EP.checklist, { method: "PUT", body: toServerChecklist(state.draft) });
      if (!applyServerChecklist(j)) state.checklist = clone(state.draft);   // the backend's saved copy is the source of truth
      if (j?.warnings?.length) console.info("Checklist warnings:", j.warnings);
    } catch (e) { return err.textContent = "Backend did not save the checklist: " + e.message; }
  } else state.checklist = clone(state.draft);
  state.evals = {};
  state.dialogs.forEach(d => { d.score = null; delete d.failed; });
  persist();
  closeModal("modal-checklist"); renderList(); renderEvaluation();
  const ids2 = state.dialogs.map(d => d.id);
  // A new checklist version has no saved results yet, so every dialog is evaluated once (one AI call each).
  if (ids2.length && confirm(`Checklist saved${state.checklistVersion != null ? ` as version ${state.checklistVersion}` : ""}. Re-evaluate all ${ids2.length} dialogs in ${state.source === "api" ? `dataset ${state.group}` : "this list"} with it now? That is ${ids2.length} AI calls.`)) runBatch(ids2);
}

