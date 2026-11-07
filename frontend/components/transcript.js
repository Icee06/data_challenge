/* Transcript: the conversation as a chat and jumping to evidence
   Plain script sharing the page scope with app.js (no modules, so the page also works opened from disk). */
"use strict";

const ROLE = { client: "Client", operator: "Operator", bot: "Bot", system: "System" };
function renderTranscript() {
  const d = sel(), box = $("transcript-container");
  $("dialog-title").textContent = d ? d.title : "No conversation selected";
  $("btn-run").disabled = !d;
  renderAudio(d);
  const empty = t => `<p class="text-center py-10" style="color:var(--muted)">${t}</p>`;
  if (!d) { box.innerHTML = `<div class="empty"><img src="assets/logo.svg" alt="" class="empty-mark"><p>Pick a conversation from the list to read its transcript.</p></div>`; return; }
  if (!d.messages.length) { box.innerHTML = empty("This conversation has no transcript."); return; }
  box.innerHTML = d.messages.map((m, i) => {
    const role = ROLE[m.speaker] ? m.speaker : "client";
    if (m.speaker === "system") return `<div id="msg-${i}" class="msg msg-system"><p class="msg-text">${icon("system")} ${esc(m.text)} <span class="msg-meta">#${i} ${esc(tsOf(d, i))}</span></p></div>`;
    return `<div id="msg-${i}" class="msg msg-${role}">
      <div class="msg-head"><span class="msg-who">${icon(role)}${esc(ROLE[m.speaker] || m.speaker)}</span><span class="msg-meta">#${i}&nbsp;&nbsp;${esc(tsOf(d, i))}</span></div>
      <p class="msg-text">${esc(m.text)}</p></div>`;
  }).join("");
}

/* ---------- navigation to evidence ---------- */
function goTo(from, to = from) {
  document.querySelectorAll("#transcript-container > div").forEach(e => e.classList.remove("highlight-quote", "msg-hit"));
  for (let i = from; i <= to; i++) $(`msg-${i}`)?.classList.add(i === from ? "highlight-quote" : "msg-hit");
  $(`msg-${from}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  seekAudio(sel(), from);
}
