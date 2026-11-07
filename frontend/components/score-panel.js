/* Score panel: total, criteria grouped by section, explanations and evidence
   Plain script sharing the page scope with app.js (no modules, so the page also works opened from disk). */
"use strict";

const CHIP_ICON = { pass: "check", fail: "cross", na: "na", mid: "partial" };

function initScorePanel() {
  $("btn-run").addEventListener("click", runEvaluation);
  $("btn-appeal").addEventListener("click", () => openAppeal());
}

function renderEvaluation() {
  const d = sel(), box = $("criteria-container"), sv = $("score-value");
  const ev = d && state.evals[d.id];
  $("score-trail").innerHTML = ""; sv.className = "";
  if (!ev) {
    sv.textContent = "–"; $("critical-badge").classList.add("hidden");
    box.innerHTML = `<p class="text-center px-4 py-10" style="color:var(--muted)">${d ? (d.failed ? `The last evaluation failed: ${esc(d.failed)}. Press “Run evaluation” to try again.` : "Not evaluated yet. Press “Run evaluation” to score this conversation.") : "Pick a conversation, then run the evaluation."}</p>`;
    return;
  }
  const orig = scoreOf(d.id, false), fin = scoreOf(d.id, true);
  sv.textContent = fin.final ?? "n/a"; sv.className = band(fin.final);
  $("critical-badge").classList.toggle("hidden", !fin.critical);
  const changed = state.appeals.some(a => a.dialogId === d.id && a.status === "resolved");
  $("score-trail").innerHTML = [
    fin.final === null ? "<span>No criterion applies, so there is no total.</span>" : "",
    changed ? `<span>AI score <b>${orig.final ?? "n/a"}</b>, after review <b>${fin.final ?? "n/a"}</b></span>` : "",
    `<span>Engine: ${esc(ev.engine)}</span>`,
    ev.version != null ? `<span>Checklist v${esc(ev.version)}</span>` : "",
  ].join("");

  // Group criteria under their checklist sections, in checklist order; the gross-error flag goes last.
  const groups = [];
  [...state.checklist.filter(c => !c.flag), ...state.checklist.filter(c => c.flag)].forEach(c => {
    const name = c.section || (c.flag ? "Gross violations" : "Other");
    let g = groups.find(x => x.name === name && x.flag === !!c.flag);
    if (!g) groups.push(g = { name, flag: !!c.flag, items: [] });
    g.items.push(c);
  });
  box.innerHTML = groups.map(g => {
    const s = !g.flag && fin.sections.find(x => x.name === g.name);
    return `<section class="sec"><div class="sec-head"><h3 class="sec-name">${esc(g.name)}</h3>${s ? `<span class="sec-score">${s.earned} / ${s.max}</span>` : ""}</div>
      ${g.items.map(c => criterionCard(d, ev, fin, c)).join("")}</section>`;
  }).join("");
}
function criterionCard(d, ev, fin, c) {
  const r = ev.results[c.id];
  if (!r) return `<article class="crit"><div class="crit-head"><h4 class="crit-name">${esc(c.name)}</h4></div><p class="crit-why">Not scored with the current checklist. Run the evaluation again.</p></article>`;
  const appeal = state.appeals.filter(p => p.dialogId === d.id && p.critId === c.id).at(-1);
  const eff = appeal?.status === "resolved" ? appeal.decision.answer : r.answer;
  const row = fin.rows[c.id], na = !c.flag && c.allowNA && eff === "NA";
  let kind, label;
  if (c.flag) { kind = eff === "YES" ? "fail" : "pass"; label = eff === "YES" ? "Да, нарушение" : "Нет"; }
  else if (na) { kind = "na"; label = "N/A"; }
  else if (c.type === "yesno") { kind = eff === "YES" ? "pass" : "fail"; label = ansLabel(c, eff); }
  else { const top = Math.max(...c.scale); kind = Number(eff) >= top ? "pass" : Number(eff) <= 0 ? "fail" : "mid"; label = ansLabel(c, eff); }
  const meta = c.flag ? "No points. Marks the whole conversation."
    : na ? `Not applicable. Its weight of ${c.weight} goes to the other criteria.`
    : row ? `<b>${row.earned}</b> of ${row.max} points, weight ${c.weight}` : `Weight ${c.weight}`;
  let evidence = "";
  if (r.evidence != null) {
    const txt = d.messages[r.evidence]?.text || "", who = ROLE[d.messages[r.evidence]?.speaker] || "Message";
    evidence = `<button data-goto="${r.evidence}" class="evidence"><span class="ev-meta">${icon("target")}${esc(who)}, message #${r.evidence} at ${esc(tsOf(d, r.evidence))}</span><span class="ev-quote">“${esc(txt.length > 140 ? txt.slice(0, 140) + "…" : txt)}”</span></button>`;
  } else if (r.checked) {
    const what = r.checkedText ? `Checked: ${esc(r.checkedText)}` : `Checked messages #${r.checked[0]} to #${r.checked[1]}`;
    evidence = `<button data-goto="${r.checked[0]}" data-to="${r.checked[1]}" class="evidence evidence-range"><span class="ev-meta">${icon("lines")}${what}</span><span class="ev-quote">Show this part of the conversation</span></button>`;
  }
  const aiSaid = appeal?.status === "resolved" && appeal.decision.answer !== r.answer ? ` <span style="color:var(--muted)">(AI said ${esc(ansLabel(c, r.answer))})</span>` : "";
  const appealLine = !appeal ? "" : appeal.status === "open" ? `<span class="appeal-open">Appeal waiting for review</span>` : `<span class="appeal-done">Reviewer: ${esc(appeal.decision.reason)}</span>`;
  return `<article class="crit ${c.flag && eff === "YES" ? "crit-flag-hit" : ""}">
    <div class="crit-head"><h4 class="crit-name">${esc(c.name)}</h4><span class="chip chip-${kind}">${icon(c.flag && eff === "YES" ? "shield" : CHIP_ICON[kind])}${esc(label)}</span></div>
    <div class="crit-meta"><span>${meta}</span><button data-dispute="${esc(c.id)}" class="btn-link-dispute">${icon("flag")}Dispute</button></div>
    <p class="crit-why">${na ? "<b>Why N/A:</b> " : ""}${esc(r.explanation)}${aiSaid}</p>
    ${evidence}
    ${appealLine ? `<div class="crit-foot">${appealLine}</div>` : ""}
  </article>`;
}
