(() => {
"use strict";

const API = "http://localhost:8000/api"; // FastAPI backend (proxies the organizers' GET to avoid CORS)

/* ---------- helpers ---------- */
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const r2 = n => Math.round((n + Number.EPSILON) * 100) / 100;
const load = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } };
const save = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} };
const clone = o => JSON.parse(JSON.stringify(o));

/* ---------- default checklist (from the PDF) ---------- */
const DEFAULT_CHECKLIST = [
  { id: "c1", name: "Установление контакта", section: "Начало и понимание запроса", type: "yesno", weight: 10, allowNA: false, scale: [], flag: false, naRule: "" },
  { id: "c2", name: "Выявление потребности", section: "Начало и понимание запроса", type: "yesno", weight: 15, allowNA: true, scale: [], flag: false, naRule: "Данных для ответа хватало с самого начала" },
  { id: "c3", name: "Идентификация", section: "Безопасность и решение", type: "yesno", weight: 20, allowNA: true, scale: [], flag: false, naRule: "Запрос не требовал идентификации" },
  { id: "c4", name: "Корректная и полная консультация", section: "Безопасность и решение", type: "scale", weight: 20, allowNA: true, scale: [20, 15, 10, 5, 0], flag: false, naRule: "Чат закончился до формулировки запроса" },
  { id: "c5", name: "Эмпатия и клиентоориентированность", section: "Ведение и завершение", type: "yesno", weight: 15, allowNA: false, scale: [], flag: false, naRule: "" },
  { id: "c6", name: "Управление диалогом, скорость и грамотность", section: "Ведение и завершение", type: "scale", weight: 15, allowNA: false, scale: [10, 7, 4, 0], flag: false, naRule: "" },
  { id: "c7", name: "Завершение", section: "Ведение и завершение", type: "yesno", weight: 5, allowNA: true, scale: [], flag: false, naRule: "Чат оборвался до возможности завершить" },
  { id: "c8", name: "Грубые ошибки", section: "Грубые нарушения", type: "yesno", weight: 0, allowNA: false, scale: [], flag: true, naRule: "" },
];

/* ---------- state ---------- */
const state = {
  dialogs: [], selId: null, source: "mock",
  checklist: load("cqa.checklist", clone(DEFAULT_CHECKLIST)),
  evals: load("cqa.evals", {}),     // { dialogId: { results: {critId: {answer, evidence, checked, explanation}}, engine } }
  appeals: load("cqa.appeals", []), // { id, dialogId, critId, comment, status, decision: {answer, reason} | null }
  draft: null,
};
const persist = () => { save("cqa.checklist", state.checklist); save("cqa.evals", state.evals); save("cqa.appeals", state.appeals); };
const sel = () => state.dialogs.find(d => d.id === state.selId);
const critOf = id => state.checklist.find(c => c.id === id);

/* ---------- data adapter ---------- */
function normalize(r) {
  return {
    id: String(r.conversation_id ?? r.id),
    language: (r.language || "").toUpperCase(),
    type: (r.channel === "voice call" || r.type === "call") ? "call" : "chat",
    title: r.title || `Dialog ${r.conversation_id ?? r.id}`,
    messages: r.transcript || r.messages || [],
    audio_url: r.audio_url || null,
  };
}
const extractRecords = j => Array.isArray(j) ? j : (j?.data?.records ?? j?.records ?? []);

async function fetchDialogs() {
  try {
    const res = await fetch(`${API}/dialogs`);
    if (!res.ok) throw new Error(res.status);
    const list = extractRecords(await res.json()).map(normalize);
    if (!list.length) throw new Error("empty");
    state.dialogs = list; state.source = "api";
  } catch (e) {
    console.warn("Backend unavailable, using mock dialogs", e);
    state.dialogs = MOCK.map(normalize); state.source = "mock";
  }
  $("source-note").textContent = state.source === "api" ? "Data: backend API" : "Data: offline mock (backend not reachable)";
  renderList();
}

/* ---------- timestamps / audio ---------- */
const toSec = ts => {
  if (!ts) return 0;
  if (/^\d{1,2}:\d{2}$/.test(ts)) { const [m, s] = ts.split(":").map(Number); return m * 60 + s; }
  const t = Date.parse(ts); return isNaN(t) ? 0 : t / 1000;
};
const relSec = (d, i) => Math.max(0, toSec(d.messages[i]?.timestamp) - toSec(d.messages[0]?.timestamp));
const fmt = s => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

/* ---------- scoring (rules from the PDF) ---------- */
function calc(cl, answers) {
  const scored = cl.filter(c => !c.flag);
  const totalW = scored.reduce((s, c) => s + c.weight, 0);
  const app = scored.filter(c => !(c.allowNA && answers[c.id] === "NA"));
  const W = app.reduce((s, c) => s + c.weight, 0);
  const k = W ? totalW / W : 0;                       // redistribute N/A weight, keep max = totalW
  const rows = {}, sections = {};
  let sum = 0;
  scored.forEach(c => {
    const a = answers[c.id];
    if (c.allowNA && a === "NA") { rows[c.id] = { na: true, earned: 0, max: 0 }; return; }
    const top = Math.max(...(c.scale.length ? c.scale : [1]));
    const ratio = c.type === "yesno" ? (a === "YES" ? 1 : 0) : (top ? Number(a) / top : 0);
    const max = c.weight * k, earned = max * (isNaN(ratio) ? 0 : ratio);
    rows[c.id] = { na: false, earned: r2(earned), max: r2(max) };
    sum += earned;
    const s = sections[c.section || "—"] ||= { earned: 0, max: 0 };
    s.earned += earned; s.max += max;
  });
  const flag = cl.find(c => c.flag);
  return {
    final: W ? r2(sum) : null, rows,
    sections: Object.entries(sections).map(([n, v]) => ({ name: n, earned: r2(v.earned), max: r2(v.max) })),
    critical: !!flag && answers[flag.id] === "YES",
  };
}
function answersFor(id, useReview) {
  const ev = state.evals[id]; if (!ev) return null;
  const a = {}; Object.entries(ev.results).forEach(([k, v]) => a[k] = v.answer);
  if (useReview) state.appeals.filter(p => p.dialogId === id && p.status === "resolved").forEach(p => a[p.critId] = p.decision.answer);
  return a;
}
const ansLabel = (c, a) => a === "NA" ? "N/A" : c.type === "yesno" ? (a === "YES" ? "Да" : "Нет") : `${a} / ${Math.max(...c.scale)}`;

/* ---------- evaluation ---------- */
async function evaluate(d) {
  try {
    const res = await fetch(`${API}/evaluate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ dialog: d, checklist: state.checklist }) });
    if (!res.ok) throw new Error(res.status);
    const j = await res.json();
    if (!j.results) throw new Error("bad shape");
    return { results: j.results, engine: "AI backend" };
  } catch (e) {
    console.warn("Evaluate endpoint unavailable, using local demo heuristics", e);
    return { results: heuristic(d), engine: "local demo heuristics" };
  }
}

// Offline demo evaluator for the 8 default criteria. Real scoring belongs in the backend LLM.
function heuristic(d) {
  const m = d.messages, ops = m.map((x, i) => [x, i]).filter(([x]) => x.speaker === "operator");
  const find = (re, who) => { const h = m.findIndex(x => (!who || x.speaker === who) && re.test(x.text)); return h < 0 ? null : h; };
  const out = {}, first = ops[0]?.[1] ?? null, last = ops.at(-1)?.[1] ?? null;
  const hello = /здравств|добр(ый|ое)|сәлем|hello|good (morning|day|afternoon)|hi\b/i, intro = /меня зовут|менің атым|my name|this is/i;
  const bye = /до свидания|всего доброго|хорошего дня|сау болыңыз|қош болыңыз|good ?bye|have a (great|nice)|остались ли|anything else/i;
  for (const c of state.checklist) {
    let r;
    switch (c.id) {
      case "c1": r = first === null ? { answer: "NO", checked: [0, m.length - 1], explanation: "Оператор не ответил." }
        : hello.test(m[first].text) && intro.test(m[first].text) ? { answer: "YES", evidence: first, explanation: "Оператор поздоровался и представился в первом ответе." }
        : { answer: "NO", evidence: first, explanation: "В первом ответе нет приветствия или представления." }; break;
      case "c2": { const q = ops.find(([x]) => x.text.includes("?"));
        r = q ? { answer: "YES", evidence: q[1], explanation: "Оператор уточнил недостающее до ответа." } : { answer: "NA", checked: [0, m.length - 1], explanation: "Данных для ответа хватало с самого начала." }; break; }
      case "c3": { const s = find(/баланс|вывод|withdraw|balance|пароль|password|иин|iin/i, "client");
        if (s === null) r = { answer: "NA", checked: [0, m.length - 1], explanation: "Защищённые данные или действия не запрашивались: идентификация не требовалась." };
        else { const v = find(/кодов|идентиф|verify|иин|iin|подтверд|растау/i, "operator");
          r = v !== null && v > s - 1 ? { answer: "YES", evidence: v, explanation: "Личность проверена до защищённого действия." } : { answer: "NO", checked: [s, Math.min(s + 2, m.length - 1)], explanation: "Проверка личности не подтверждена в чате." }; } break; }
      case "c4": r = ops.length >= 2 ? { answer: c.scale[1] ?? c.scale[0], evidence: ops[1][1], explanation: "Основной вопрос решён (демо-оценка)." } : { answer: c.scale.at(-2) ?? 0, checked: [0, m.length - 1], explanation: "Недостаточно ответов оператора (демо-оценка)." }; break;
      case "c5": r = { answer: "YES", evidence: first, explanation: "Оператор вежлив; жалоб клиента нет." }; break;
      case "c6": r = { answer: c.scale[0], checked: [0, m.length - 1], explanation: "Ответы ясные и связные (демо-оценка)." }; break;
      case "c7": { const b = last !== null && bye.test(m[last].text);
        r = b ? { answer: "YES", evidence: last, explanation: "Оператор предложил помощь/попрощался." } : { answer: "NO", evidence: last, explanation: "В последней реплике оператора нет итога или прощания." }; break; }
      case "c8": { const h = find(/переведите мне|личный номер|personal number|send (the )?money to me|дурак|идиот/i, "operator");
        r = h !== null ? { answer: "YES", evidence: h, explanation: "Найдено грубое нарушение." } : { answer: "NO", checked: [0, m.length - 1], explanation: "Оскорблений, услуг в обход компании и раскрытия данных не выявлено." }; break; }
      default: r = { answer: c.allowNA ? "NA" : (c.type === "yesno" ? "NO" : 0), checked: [0, m.length - 1], explanation: "Новый критерий: требуется бэкенд-оценка (/api/evaluate)." };
    }
    out[c.id] = r;
  }
  return out;
}

async function runEvaluation() {
  const d = sel(); if (!d) return;
  const btn = $("btn-run"); btn.disabled = true; btn.textContent = "Evaluating...";
  $("criteria-container").innerHTML = `<p class="text-slate-400 text-center py-10">Evaluating dialog...</p>`;
  state.evals[d.id] = await evaluate(d);
  persist(); btn.disabled = false; btn.textContent = "Run evaluation";
  renderEvaluation();
}

/* ---------- rendering ---------- */
function renderList() {
  const lang = $("filter-lang").value, type = $("filter-type").value;
  const list = state.dialogs.filter(d => (lang === "ALL" || d.language === lang) && (type === "ALL" || d.type === type));
  $("dialog-count").textContent = list.length;
  $("dialog-list").innerHTML = list.length ? list.map(d => {
    const ev = state.evals[d.id], sc = ev ? calc(state.checklist, answersFor(d.id, true)).final : null;
    return `<div data-dialog="${esc(d.id)}" class="p-3 cursor-pointer hover:bg-slate-700 border-b border-slate-700/50 ${d.id === state.selId ? "bg-slate-700" : ""}">
      <div class="flex justify-between gap-2"><div class="text-sm font-medium text-slate-200 truncate">${esc(d.title)}</div>
      <span class="text-xs ${sc === null ? "text-slate-600" : "text-emerald-400"}">${sc === null ? "" : sc}</span></div>
      <div class="text-xs mt-1 flex gap-2"><span class="px-1.5 py-0.5 bg-slate-600 rounded text-[10px]">${esc(d.language)}</span>
      <span class="px-1.5 py-0.5 bg-slate-600 rounded text-[10px]">${esc(d.type)}</span></div></div>`;
  }).join("") : `<p class="text-slate-500 text-center text-sm py-8">No dialogs match the filters.</p>`;
  $("appeal-count").textContent = state.appeals.filter(a => a.status === "open").length;
}

function renderTranscript() {
  const d = sel(), box = $("transcript-container");
  $("dialog-title").textContent = d ? d.title : "No dialog selected";
  $("btn-run").disabled = !d;
  const ac = $("audio-container");
  if (d?.audio_url) { ac.classList.remove("hidden"); $("audio-player").src = d.audio_url; } else { ac.classList.add("hidden"); $("audio-player").removeAttribute("src"); }
  if (!d) return;
  box.innerHTML = d.messages.map((m, i) => {
    const cls = { operator: "bg-slate-700/60 ml-4", client: "bg-slate-900 border border-slate-700 mr-4", bot: "bg-indigo-900/30 border border-indigo-800/40 mr-4", system: "bg-amber-900/20 text-center italic" }[m.speaker] || "bg-slate-900 mr-4";
    return `<div id="msg-${i}" class="p-3 rounded-lg text-xs ${cls}">
      <div class="flex justify-between text-[10px] text-slate-400 mb-1"><span class="font-bold capitalize text-slate-300">${esc(m.speaker)} <span class="font-normal text-slate-500">#${i}</span></span>
      <span>${d.type === "call" ? fmt(relSec(d, i)) : esc(m.timestamp || "")}</span></div>
      <p class="text-slate-200 whitespace-pre-wrap">${esc(m.text)}</p></div>`;
  }).join("");
}

function renderEvaluation() {
  const d = sel(), box = $("criteria-container");
  const ev = d && state.evals[d.id];
  $("section-scores").innerHTML = ""; $("score-trail").textContent = "";
  if (!ev) { $("score-value").textContent = "--"; $("critical-badge").classList.add("hidden"); box.innerHTML = `<p class="text-slate-500 text-center py-10">${d ? "Not evaluated yet. Press “Run evaluation”." : "Select a dialog first."}</p>`; return; }
  const orig = calc(state.checklist, answersFor(d.id, false)), fin = calc(state.checklist, answersFor(d.id, true));
  $("score-value").textContent = fin.final ?? "n/a";
  $("critical-badge").classList.toggle("hidden", !fin.critical);
  const changed = state.appeals.some(a => a.dialogId === d.id && a.status === "resolved");
  $("score-trail").textContent = (fin.final === null ? "No applicable criteria: score not calculated. " : "") + (changed ? `Original ${orig.final ?? "n/a"} → final ${fin.final ?? "n/a"} · ` : "") + `Engine: ${ev.engine}`;
  $("section-scores").innerHTML = fin.sections.map(s => `<span class="text-[10px] px-2 py-1 bg-slate-700 rounded">${esc(s.name)}: ${s.earned}/${s.max}</span>`).join("");

  box.innerHTML = state.checklist.map(c => {
    const r = ev.results[c.id];
    if (!r) return `<div class="p-3 bg-slate-900 border border-slate-700 rounded-lg text-xs text-slate-400">${esc(c.name)}: not evaluated with the current checklist. Run the evaluation again.</div>`;
    const appeal = state.appeals.filter(p => p.dialogId === d.id && p.critId === c.id).at(-1);
    const eff = appeal?.status === "resolved" ? appeal.decision.answer : r.answer;
    const row = fin.rows[c.id];
    const na = !c.flag && c.allowNA && eff === "NA";
    const badge = c.flag ? (eff === "YES" ? "bg-red-500/20 text-red-400" : "bg-emerald-500/20 text-emerald-400")
      : na ? "bg-slate-600 text-slate-200" : row && row.earned > 0 ? "bg-emerald-500/20 text-emerald-400" : "bg-red-500/20 text-red-400";
    const pts = c.flag ? (eff === "YES" ? "Flagged" : "Clear") : na ? "N/A" : `${row.earned} / ${row.max} pts`;
    let link = "";
    if (r.evidence != null) link = `<button data-goto="${r.evidence}" class="text-emerald-400 hover:underline text-[10px] mt-1 block text-left">📍 Evidence: message #${r.evidence} “${esc((d.messages[r.evidence]?.text || "").slice(0, 60))}”</button>`;
    else if (r.checked) link = `<button data-goto="${r.checked[0]}" data-to="${r.checked[1]}" class="text-sky-400 hover:underline text-[10px] mt-1 block">🔎 Checked section: messages #${r.checked[0]}–#${r.checked[1]}</button>`;
    return `<div class="p-3 bg-slate-900 border border-slate-700 rounded-lg text-xs space-y-1.5">
      <div class="flex justify-between items-start gap-2"><span class="font-semibold text-slate-200">${esc(c.name)}</span>
        <span class="px-2 py-0.5 rounded text-[10px] font-bold whitespace-nowrap ${badge}">${pts}</span></div>
      <div class="text-[10px] text-slate-500">Answer: <b class="text-slate-300">${esc(ansLabel(c, eff))}</b>${c.flag ? "" : ` · Weight ${c.weight}`}
        ${appeal?.status === "resolved" && appeal.decision.answer !== r.answer ? ` · AI said ${esc(ansLabel(c, r.answer))}` : ""}</div>
      <p class="text-slate-400 text-[11px]">${na ? "<b>N/A reason:</b> " : ""}${esc(r.explanation)}</p>
      ${link}
      ${appeal ? `<div class="text-[10px] ${appeal.status === "open" ? "text-amber-400" : "text-indigo-300"}">${appeal.status === "open" ? "Appeal pending review" : `Reviewer: ${esc(appeal.decision.reason)}`}</div>` : ""}
      <button data-dispute="${esc(c.id)}" class="text-amber-400 hover:underline text-[10px]">Dispute this criterion</button></div>`;
  }).join("");
}

/* ---------- navigation to evidence ---------- */
function goTo(from, to = from) {
  document.querySelectorAll("#transcript-container > div").forEach(e => e.classList.remove("highlight-quote", "msg-hit"));
  for (let i = from; i <= to; i++) $(`msg-${i}`)?.classList.add(i === from ? "highlight-quote" : "msg-hit");
  $(`msg-${from}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  const d = sel(), p = $("audio-player");
  if (d?.audio_url && p.src) { try { p.currentTime = relSec(d, from); } catch {} }
}

/* ---------- selection ---------- */
function selectDialog(id) {
  state.selId = id; renderList(); renderTranscript(); renderEvaluation();
}

/* ---------- appeals + reviewer ---------- */
function openAppeal(critId) {
  const d = sel(); if (!d) return alert("Select a dialog first.");
  if (!state.evals[d.id]) return alert("Run the evaluation first.");
  $("appeal-crit").innerHTML = state.checklist.map(c => `<option value="${esc(c.id)}" ${c.id === critId ? "selected" : ""}>${esc(c.name)}</option>`).join("");
  $("appeal-comment").value = ""; $("appeal-error").textContent = "";
  $("modal-appeal").classList.remove("hidden");
}
function submitAppeal() {
  const comment = $("appeal-comment").value.trim();
  if (!comment) return $("appeal-error").textContent = "Write a comment so the reviewer knows what to check.";
  const critId = $("appeal-crit").value;
  state.appeals.push({ id: "a" + Date.now(), dialogId: state.selId, critId, comment, status: "open", decision: null });
  persist(); closeModal("modal-appeal"); renderList(); renderEvaluation();
}
function renderReview() {
  const items = [...state.appeals].reverse();
  $("review-list").innerHTML = items.length ? items.map(p => {
    const d = state.dialogs.find(x => x.id === p.dialogId), c = critOf(p.critId), r = state.evals[p.dialogId]?.results[p.critId];
    if (!d || !c || !r) return "";
    const idx = r.evidence ?? r.checked?.[0] ?? 0, end = r.evidence ?? r.checked?.[1] ?? idx;
    const frag = d.messages.slice(Math.max(0, idx - 1), Math.min(d.messages.length, end + 2));
    const opts = c.type === "yesno" ? ["YES", "NO"] : c.scale.map(String); if (c.allowNA) opts.push("NA");
    const before = calc(state.checklist, answersFor(d.id, false)).final;
    return `<div class="bg-slate-900 border border-slate-700 rounded-lg p-4 space-y-2 text-xs" data-appeal="${p.id}">
      <div class="flex justify-between"><b>${esc(d.title)} · ${esc(c.name)}</b>
        <span class="${p.status === "open" ? "text-amber-400" : "text-emerald-400"}">${p.status === "open" ? "Open" : "Resolved"}</span></div>
      <div>AI answer: <b>${esc(ansLabel(c, r.answer))}</b> — ${esc(r.explanation)}</div>
      <div class="bg-slate-800 rounded p-2 space-y-1">${frag.map(m => `<div><span class="text-slate-500">${esc(m.speaker)}:</span> ${esc(m.text)}</div>`).join("")}</div>
      <div>Manager comment: <i>${esc(p.comment)}</i></div>
      ${p.status === "open" ? `<div class="grid grid-cols-3 gap-2 items-end">
        <label>Decision<select data-f="ans">${opts.map(o => `<option value="${o}" ${String(r.answer) === o ? "selected" : ""}>${o === "NA" ? "N/A" : o}${String(r.answer) === o ? " (confirm AI)" : ""}</option>`).join("")}</select></label>
        <label class="col-span-2">Reason<input data-f="reason" placeholder="Why confirm or change?"></label></div>
        <p data-f="err" class="text-red-400 h-4"></p>
        <button data-decide="${p.id}" class="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded font-semibold">Save decision</button>`
      : `<div>Decision: <b>${esc(ansLabel(c, p.decision.answer))}</b> — ${esc(p.decision.reason)}</div>
         <div>Original score <b>${before ?? "n/a"}</b> → final score <b>${calc(state.checklist, answersFor(d.id, true)).final ?? "n/a"}</b></div>`}
    </div>`;
  }).join("") : `<p class="text-slate-500 text-center py-8">No appeals yet.</p>`;
}
function decide(id) {
  const card = document.querySelector(`[data-appeal="${id}"]`), p = state.appeals.find(a => a.id === id);
  const reason = card.querySelector('[data-f="reason"]').value.trim();
  if (!reason) return card.querySelector('[data-f="err"]').textContent = "Add a reason for the decision.";
  const raw = card.querySelector('[data-f="ans"]').value, c = critOf(p.critId);
  p.decision = { answer: raw === "NA" || raw === "YES" || raw === "NO" ? raw : Number(raw), reason };
  p.status = "resolved"; persist(); renderReview(); renderList(); renderEvaluation();
}

/* ---------- checklist editor ---------- */
function renderChecklistEditor() {
  const total = state.draft.filter(c => !c.flag).reduce((s, c) => s + (Number(c.weight) || 0), 0);
  $("weight-sum").innerHTML = `<span class="${total === 100 ? "text-emerald-400" : "text-amber-400"}">Total weight: ${total}${total === 100 ? "" : " (should be 100)"}</span>`;
  $("checklist-rows").innerHTML = state.draft.map((c, i) => `
    <div class="bg-slate-900 border border-slate-700 rounded-lg p-3 grid grid-cols-12 gap-2 text-[11px] items-end" data-i="${i}">
      <label class="col-span-4">Name<input data-f="name" value="${esc(c.name)}"></label>
      <label class="col-span-3">Section<input data-f="section" value="${esc(c.section)}"></label>
      <label class="col-span-2">Type<select data-f="type"><option value="yesno" ${c.type === "yesno" ? "selected" : ""}>Yes / No</option><option value="scale" ${c.type === "scale" ? "selected" : ""}>Scale</option></select></label>
      <label class="col-span-1">Weight<input data-f="weight" type="number" min="0" value="${c.weight}" ${c.flag ? "disabled" : ""}></label>
      <button data-del="${i}" class="col-span-2 text-red-400 hover:underline text-right">Remove</button>
      <label class="col-span-3 ${c.type === "scale" ? "" : "opacity-40"}">Scale steps (high → low)<input data-f="scale" value="${c.scale.join(", ")}" ${c.type === "scale" ? "" : "disabled"}></label>
      <label class="col-span-4">N/A condition<input data-f="naRule" value="${esc(c.naRule)}" ${c.allowNA ? "" : "disabled"}></label>
      <label class="col-span-2 flex items-center gap-2"><input data-f="allowNA" type="checkbox" class="!w-auto" ${c.allowNA ? "checked" : ""}>N/A allowed</label>
      <label class="col-span-3 flex items-center gap-2"><input data-f="flag" type="checkbox" class="!w-auto" ${c.flag ? "checked" : ""}>Critical flag (no points)</label>
    </div>`).join("");
}
function editField(e) {
  const row = e.target.closest("[data-i]"), f = e.target.dataset.f; if (!row || !f) return;
  const c = state.draft[+row.dataset.i];
  const v = e.target.type === "checkbox" ? e.target.checked : e.target.value;
  if (f === "scale") c.scale = v.split(",").map(s => Number(s.trim())).filter(n => !isNaN(n) && String(n) !== "");
  else if (f === "weight") c.weight = Number(v) || 0;
  else c[f] = v;
  if (e.type === "change" && ["type", "allowNA", "flag"].includes(f)) { if (c.type === "scale" && !c.scale.length) c.scale = [10, 5, 0]; renderChecklistEditor(); }
  else if (f === "weight") { const t = state.draft.filter(x => !x.flag).reduce((s, x) => s + x.weight, 0); $("weight-sum").innerHTML = `<span class="${t === 100 ? "text-emerald-400" : "text-amber-400"}">Total weight: ${t}${t === 100 ? "" : " (should be 100)"}</span>`; }
}
async function saveChecklist() {
  const err = $("checklist-error"); err.textContent = "";
  for (const c of state.draft) {
    if (!c.name.trim()) return err.textContent = "Every criterion needs a name.";
    if (c.type === "scale" && c.scale.length < 2) return err.textContent = `“${c.name}”: a scale needs at least two steps.`;
  }
  if (!state.draft.some(c => !c.flag && c.weight > 0)) return err.textContent = "At least one scored criterion with a weight is required.";
  state.draft.forEach(c => { if (c.type === "scale") c.scale = [...c.scale].sort((a, b) => b - a); if (c.flag) c.weight = 0; });
  state.checklist = clone(state.draft); state.evals = {};
  persist();
  fetch(`${API}/checklist`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(state.checklist) }).catch(() => {});
  closeModal("modal-checklist"); renderList(); renderEvaluation();
  if (sel()) runEvaluation();
}

/* ---------- misc ---------- */
function closeModal(id) { $(id).classList.add("hidden"); }

function upload(e) {
  const f = e.target.files[0]; if (!f) return;
  const rd = new FileReader();
  rd.onload = () => {
    try {
      const recs = extractRecords(JSON.parse(rd.result)), list = (recs.length ? recs : [JSON.parse(rd.result)]).map(normalize);
      if (!list.every(d => Array.isArray(d.messages) && d.messages.length)) throw new Error("no transcript");
      state.dialogs.unshift(...list); renderList(); selectDialog(list[0].id);
    } catch { alert("Could not read the file. Expected a record with conversation_id, language, channel and a transcript array."); }
  };
  rd.readAsText(f); e.target.value = "";
}

/* ---------- events ---------- */
document.addEventListener("click", e => {
  const t = e.target.closest("[data-dialog],[data-goto],[data-dispute],[data-decide],[data-close],[data-del]"); if (!t) return;
  if (t.dataset.dialog) selectDialog(t.dataset.dialog);
  else if (t.dataset.goto) goTo(+t.dataset.goto, t.dataset.to ? +t.dataset.to : +t.dataset.goto);
  else if (t.dataset.dispute) openAppeal(t.dataset.dispute);
  else if (t.dataset.decide) decide(t.dataset.decide);
  else if (t.dataset.close) closeModal(t.dataset.close);
  else if (t.dataset.del) { state.draft.splice(+t.dataset.del, 1); renderChecklistEditor(); }
});
document.addEventListener("DOMContentLoaded", () => {
  fetchDialogs();
  $("filter-lang").addEventListener("change", renderList);
  $("filter-type").addEventListener("change", renderList);
  $("btn-run").addEventListener("click", runEvaluation);
  $("btn-appeal").addEventListener("click", () => openAppeal());
  $("btn-submit-appeal").addEventListener("click", submitAppeal);
  $("btn-reviewer").addEventListener("click", () => { renderReview(); $("modal-review").classList.remove("hidden"); });
  $("btn-edit-checklist").addEventListener("click", () => { state.draft = clone(state.checklist); $("checklist-error").textContent = ""; renderChecklistEditor(); $("modal-checklist").classList.remove("hidden"); });
  $("btn-add-crit").addEventListener("click", () => { state.draft.push({ id: "c" + Date.now(), name: "New criterion", section: "", type: "yesno", weight: 0, allowNA: false, scale: [], flag: false, naRule: "" }); renderChecklistEditor(); });
  $("btn-reset-crit").addEventListener("click", () => { state.draft = clone(DEFAULT_CHECKLIST); renderChecklistEditor(); });
  $("btn-save-checklist").addEventListener("click", saveChecklist);
  $("checklist-rows").addEventListener("input", editField);
  $("checklist-rows").addEventListener("change", editField);
  $("upload").addEventListener("change", upload);
});

/* ---------- offline mock data (same shape as the organizers' API) ---------- */
const MOCK = [
  { conversation_id: "p1-ru-001", channel: "agent chat", language: "RU", audio_url: null, transcript: [
    { timestamp: "00:02", speaker: "system", text: "Чат начат" },
    { timestamp: "00:05", speaker: "client", text: "Добрый день, хочу вывести деньги со счета, как это сделать?" },
    { timestamp: "00:30", speaker: "operator", text: "Здравствуйте! Меня зовут Алексей. Назовите, пожалуйста, ваш ИИН для проверки." },
    { timestamp: "00:45", speaker: "client", text: "Мой ИИН 900101300123" },
    { timestamp: "01:10", speaker: "operator", text: "Спасибо, личность подтверждена. Вывод доступен в приложении, раздел «Вывод средств»." },
    { timestamp: "01:20", speaker: "client", text: "Понял, спасибо." },
    { timestamp: "01:30", speaker: "operator", text: "Остались ли у вас еще вопросы? Хорошего дня!" } ] },
  { conversation_id: "p1-kz-001", channel: "agent chat", language: "KZ", audio_url: null, transcript: [
    { timestamp: "00:03", speaker: "client", text: "Сәлеметсіз бе, тарифтер туралы білгім келеді." },
    { timestamp: "00:40", speaker: "operator", text: "Сәлеметсіз бе! Менің атым Айгерім. Тарифтер сайтта көрсетілген." },
    { timestamp: "00:55", speaker: "client", text: "Рахмет." } ] },
  { conversation_id: "p1-en-001", channel: "voice call", language: "EN", audio_url: "https://www.w3schools.com/html/horse.mp3", transcript: [
    { timestamp: "2026-05-01T10:00:02+05:00", speaker: "operator", text: "Hello! My name is Dan, Freedom Broker. How can I help?" },
    { timestamp: "2026-05-01T10:00:12+05:00", speaker: "client", text: "My trading app will not connect." },
    { timestamp: "2026-05-01T10:00:30+05:00", speaker: "operator", text: "Please reinstall the app and log in again." },
    { timestamp: "2026-05-01T10:00:50+05:00", speaker: "client", text: "That worked, thanks." } ] },
];
})();