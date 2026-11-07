/* Sidebar: dataset switcher, filters, batch evaluation controls and JSON upload
   Plain script sharing the page scope with app.js (no modules, so the page also works opened from disk). */
"use strict";

function initSidebar() {
  $("group-id").value = state.group; renderGroups();
  $("btn-load-group").addEventListener("click", switchGroup);
  $("group-id").addEventListener("keydown", e => { if (e.key === "Enter") switchGroup(); });
  ["filter-lang", "filter-type", "filter-status"].forEach(id => $(id).addEventListener("change", renderList));
  $("filter-search").addEventListener("input", renderList);
  $("btn-reset").addEventListener("click", () => {
    ["filter-lang", "filter-type", "filter-status"].forEach(id => $(id).value = "ALL"); $("filter-search").value = ""; renderList();
  });
  $("btn-batch").addEventListener("click", () => runBatch(state.shown.filter(d => !isDone(d)).map(d => d.id)));
  $("btn-batch-stop").addEventListener("click", () => { if (batch) { batch.stop = true; showBatch(); } });
  $("upload").addEventListener("change", upload);
}

function renderGroups() {
  $("group-ids").innerHTML = state.groups.map(g => `<option value="${esc(g)}"></option>`).join("");
}
async function switchGroup() {
  const g = $("group-id").value.trim();
  if (!g) { $("group-id").value = state.group; return; }
  if (batch) return alert("Wait for the running evaluation to finish or stop it first.");
  state.group = g;   // remembered for the next visit only once it loads (see fetchDialogs)
  await fetchDialogs(); await loadAppeals();
}


function showBatch(finished) {
  const b = batch || finished, box = $("batch-status");
  $("btn-batch-stop").classList.toggle("hidden", !batch);
  if (!b) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  $("batch-bar").style.width = `${b.total ? (b.done / b.total) * 100 : 100}%`;
  const fails = b.failed.length ? `${b.failed.length} failed (${b.failed.slice(0, 4).join(", ")}${b.failed.length > 4 ? "…" : ""})` : "";
  $("batch-text").textContent = batch
    ? `Evaluating ${b.done} of ${b.total}${fails ? " · " + fails : ""}${b.stop ? " · stopping…" : ""}`
    : `${b.stop ? "Stopped" : "Done"}: ${b.done - b.failed.length} evaluated${fails ? ", " + fails + ". Press the button again to retry them." : "."}`;
}

function upload(e) {
  const f = e.target.files[0]; if (!f) return;
  const rd = new FileReader();
  rd.onload = () => {
    try {
      const recs = extractRecords(JSON.parse(rd.result)), list = (recs.length ? recs : [JSON.parse(rd.result)]).map(normalize);
      if (!list.every(d => Array.isArray(d.messages) && d.messages.length)) throw new Error("no transcript");
      state.dialogs.unshift(...list); state.loadError = ""; renderList(); selectDialog(list[0].id);
    } catch { alert("Could not read the file. Expected a record with conversation_id, language, channel and a transcript array."); }
  };
  rd.readAsText(f); e.target.value = "";
}
