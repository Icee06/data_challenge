/* Freedom CQA Analytics: API calls, state, evaluation and start-up.
   Components live in components/ and are loaded before this file (see index.html). */
"use strict";

/* ---------- API contract: every backend path lives here (see API_CONTRACT.md) ---------- */
// Relative when the backend serves this page (http://localhost:8000/, or any real host such as Dynamics 365).
// Opened from disk or from another local dev server (e.g. Live Server on :5500), it calls the backend on :8000.
// Override with ?api=http://host:port/api in the page URL.
const API = new URLSearchParams(location.search).get("api") || (
  !location.protocol.startsWith("http") || (/^(localhost|127\.0\.0\.1)$/.test(location.hostname) && location.port !== "8000")
    ? "http://localhost:8000/api" : "/api");
// Every dialog call carries the current dataset (DEV or the control group_id): the backend defaults to DEV.
const G = () => `group_id=${encodeURIComponent(state.group)}`;
const EP = {
  dialogues: group => `${API}/dialogues?group_id=${encodeURIComponent(group)}`,
  dialogue: id => `${API}/dialogues/${encodeURIComponent(id)}?${G()}`,
  evaluate: (id, force) => `${API}/evaluate/${encodeURIComponent(id)}?${G()}${force ? "&force_refresh=true" : ""}`,
  evaluation: id => `${API}/evaluation/${encodeURIComponent(id)}?${G()}`,
  checklist: `${API}/checklist`,                                   // GET {version, checklist, warnings}; PUT saves a new version
  appeals: () => `${API}/appeals?status=all&${G()}`,               // GET: every appeal (open and decided) for the reviewer queue
  appeal: `${API}/appeal`,                                         // POST {evaluation_id, criterion_id, comment}
  decision: id => `${API}/appeal/${encodeURIComponent(id)}/decision`, // POST {decision: "confirm"|"change", reason, answer?}
};
const BATCH_CONCURRENCY = 2;     // parallel LLM evaluations during "Evaluate pending" (free API tiers allow few per minute)
const EVAL_TIMEOUT_MS = 240000;  // one LLM evaluation, retries and rate-limit waits included

/* ---------- helpers ---------- */
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const r2 = n => Math.round((n + Number.EPSILON) * 100) / 100;
const load = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } };
const save = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} };
const clone = o => JSON.parse(JSON.stringify(o));

// fetch wrapper: timeout, JSON body, readable errors. HTTP errors carry .status (backend reachable); network errors do not.
async function api(url, { method = "GET", body, timeout = 15000 } = {}) {
  const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), timeout);
  try {
    const r = await fetch(url, { method, signal: ctl.signal, headers: body === undefined ? undefined : { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
    if (!r.ok) {
      let msg = "HTTP " + r.status;
      try {
        const j = await r.json();
        if (j?.detail) msg += ": " + (typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail));
        if (Array.isArray(j?.problems) && j.problems.length) msg += " (" + j.problems.join("; ") + ")";   // e.g. why an AI answer was rejected
      } catch {}
      const e = new Error(msg); e.status = r.status; throw e;
    }
    return r.status === 204 ? null : await r.json();
  } catch (e) {
    if (e.name === "AbortError") throw new Error(`no answer after ${timeout / 1000} s`);
    throw e;
  } finally { clearTimeout(timer); }
}

/* ---------- default checklist (from the PDF) ---------- */
// description and scaleDesc are what the AI evaluates against; a criterion name alone is not enough.
const DEFAULT_CHECKLIST = [
  { id: "contact", name: "Установление контакта", section: "Начало и понимание запроса", type: "yesno", weight: 10, allowNA: false, scale: [], scaleDesc: {}, flag: false, naRule: "",
    description: "Да — оператор поздоровался на языке клиента и представился в первом ответе." },
  { id: "needs", name: "Выявление потребности", section: "Начало и понимание запроса", type: "yesno", weight: 15, allowNA: true, scale: [], scaleDesc: {}, flag: false, naRule: "Данных для ответа хватало с самого начала",
    description: "Да — оператор учёл известные детали и уточнил недостающее до ответа." },
  { id: "identification", name: "Идентификация", section: "Безопасность и решение", type: "yesno", weight: 20, allowNA: true, scale: [], scaleDesc: {}, flag: false, naRule: "Запрос не требовал идентификации",
    description: "Да — проверка личности подтверждена до раскрытия данных или защищённого действия. Если проверка требовалась перед защищённым действием и не подтверждена в чате — Нет. Общий вопрос о тарифах идентификации не требует." },
  { id: "consultation", name: "Корректная и полная консультация", section: "Безопасность и решение", type: "scale", weight: 20, allowNA: true, scale: [20, 15, 10, 5, 0], flag: false, naRule: "Чат закончился до того, как клиент сформулировал запрос",
    description: "Оцените ответ или следующий шаг: правильно и полно ли решён запрос клиента.",
    scaleDesc: { 20: "Все существенные вопросы закрыты или дан корректный следующий шаг.", 15: "Основной вопрос решён, пропущена второстепенная деталь.", 10: "Часть запроса закрыта, существенный вопрос остался открытым.", 5: "Есть общая подсказка, но клиенту всё ещё непонятно, как действовать.", 0: "Ответа по существу нет или ключевая рекомендация ошибочна." } },
  { id: "empathy", name: "Эмпатия и клиентоориентированность", section: "Ведение и завершение диалога", type: "yesno", weight: 15, allowNA: false, scale: [], scaleDesc: {}, flag: false, naRule: "",
    description: "Да — оператор вежлив и, если клиент выразил недовольство или описал неудобство, отреагировал на конкретную проблему." },
  { id: "dialogue", name: "Управление диалогом, скорость и грамотность", section: "Ведение и завершение диалога", type: "scale", weight: 15, allowNA: false, scale: [10, 7, 4, 0], flag: false, naRule: "",
    description: "Оцените ясность, порядок ответов и предупреждение об ожидании.",
    scaleDesc: { 10: "Ответы ясные и связные. Если было явное ожидание, оператор предупредил.", 7: "Один заметный сбой, но клиент может понять ответ и продолжить.", 4: "Клиенту приходится переспрашивать из-за формулировок или порядка ответов.", 0: "Из-за бессвязных или противоречивых реплик ход диалога непонятен." } },
  { id: "closing", name: "Завершение", section: "Ведение и завершение диалога", type: "yesno", weight: 5, allowNA: true, scale: [], scaleDesc: {}, flag: false, naRule: "Чат оборвался до возможности его завершить",
    description: "Да — оператор подвёл итог или предложил дальнейшую помощь и попрощался." },
  { id: "gross_errors", name: "Грубые ошибки", section: "Грубые нарушения", type: "yesno", weight: 0, allowNA: false, scale: [], scaleDesc: {}, flag: true, naRule: "",
    description: "Да — выявлено оскорбление клиента, предложение платной услуги в обход компании (перевести деньги оператору лично для инвестирования или дать личный номер для частной платной консультации; достаточно самого предложения) или раскрытие конфиденциальной информации. Нет — ни одно из трёх не выявлено." },
];

// Fills fields that older saved checklists may lack, and reads the backend format:
// type "yes_no" | "scale" | "flag", allow_na, na_condition, scale [{score, description}], section = a section id.
function upgradeChecklist(list, sections = []) {
  const secName = id => sections.find(s => s.id === id)?.name ?? id ?? "";
  return (Array.isArray(list) ? list : []).map(raw => {
    const steps = raw.scale ?? raw.scale_steps ?? [];
    const t = raw.type ?? raw.answer_type ?? "yesno";
    const c = {
      id: String(raw.id ?? raw.criterion_id ?? ""), name: raw.name ?? raw.text ?? "", section: secName(raw.section),
      type: t === "scale" ? "scale" : "yesno", weight: Number(raw.weight) || 0,
      allowNA: !!(raw.allowNA ?? raw.allow_na ?? raw.na_allowed), naRule: raw.naRule ?? raw.na_rule ?? raw.na_condition ?? "",
      scale: steps.map(s => Number(typeof s === "object" ? s?.score : s)).filter(n => !isNaN(n)),
      flag: !!(raw.flag ?? raw.is_critical ?? raw.critical) || t === "flag",
      description: raw.description ?? "",
      scaleDesc: raw.scaleDesc ?? raw.scale_descriptions ?? Object.fromEntries(steps.filter(s => typeof s === "object").map(s => [Number(s.score), s.description ?? ""])),
    };
    const def = DEFAULT_CHECKLIST.find(x => x.id === c.id && x.name === c.name);
    if (def) { c.description ||= def.description; c.scaleDesc = { ...def.scaleDesc, ...c.scaleDesc }; }
    return c;
  });
}

/* ---------- state ---------- */
const state = {
  dialogs: [], shown: [], selId: null, source: "mock",
  group: load("cqa.group", "DEV"), groups: load("cqa.groups", ["DEV"]),
  // ".v2" keys: browsers that ran the old build kept c1..c8 checklists and evaluations; start clean once.
  checklist: upgradeChecklist(load("cqa.checklist.v2", clone(DEFAULT_CHECKLIST))), checklistVersion: load("cqa.checklistVersion.v2", null),
  sections: load("cqa.sections.v2", []),     // backend sections [{id, name}]; criteria refer to them by id
  checklistMeta: load("cqa.checklistMeta.v2", {}), // backend checklist id, name and notes, sent back on save
  evals: load("cqa.evals.v2", {}),     // { dialogId: { results: {critId: {answer, evidence, checked, checkedText, explanation}}, engine, evaluationId?, server?, version? } }
  appeals: load("cqa.appeals.v2", []), // { id, dialogId, critId, comment, status, decision: {answer, reason} | null }
  appealsNote: "", loadError: "", draft: null,
};
const persist = () => {
  save("cqa.checklist.v2", state.checklist); save("cqa.checklistVersion.v2", state.checklistVersion); save("cqa.sections.v2", state.sections);
  save("cqa.checklistMeta.v2", state.checklistMeta); save("cqa.evals.v2", state.evals); save("cqa.appeals.v2", state.appeals);
};
const sel = () => state.dialogs.find(d => d.id === state.selId);
const critOf = id => state.checklist.find(c => c.id === id);

/* ---------- data adapter ---------- */
function titleOf(r) {
  const id = r.conversation_id ?? r.id;
  const c = (r.transcript || r.messages || []).find(m => m.speaker === "client" && m.text && !/^[\d#*\s.]+$/.test(m.text));
  return c ? `${id}: ${c.text.slice(0, 45)}${c.text.length > 45 ? "…" : ""}` : `Dialog ${id}`;
}
function normalize(r) {
  return {
    id: String(r.conversation_id ?? r.id),
    language: (r.language || "").toUpperCase(),
    type: (r.channel === "voice call" || r.type === "call") ? "call" : "chat",
    title: r.title || titleOf(r),
    messages: r.transcript || r.messages || [],
    audio_url: r.audio_url || null,
    audio_offset: Number(r.audio_offset ?? 0) || 0,   // seconds from the start of the recording to the first utterance
    score: r.final_score ?? r.score ?? r.evaluation?.total ?? null,   // the registry row carries {evaluation: {total, ...}} once evaluated
  };
}
const extractRecords = j => Array.isArray(j) ? j : (j?.data?.records ?? j?.records ?? j?.dialogues ?? []);

async function fetchDialogs() {
  const g = state.group;
  state.loadError = ""; state.selId = null;
  $("source-note").textContent = `Loading ${g}…`;
  $("source-note").dataset.state = "loading";
  $("dialog-list").innerHTML = `<p class="text-slate-500 text-center text-base py-8">Loading…</p>`;
  try {
    state.dialogs = extractRecords(await api(EP.dialogues(g), { timeout: 30000 })).map(normalize);
    state.source = "api";
    if (!state.dialogs.length) state.loadError = `The backend returned no dialogs for “${g}”.`;
  } catch (e) {
    if (e.status) {   // backend answered with an error: show it, never swap in other data silently
      state.dialogs = []; state.source = "api"; state.loadError = `Could not load “${g}”: ${e.message}`;
    } else {
      console.warn("Backend unreachable, trying dev.json", e);
      try {
        const list = extractRecords(await api("dev.json")).map(normalize);
        if (!list.length) throw new Error("empty");
        state.dialogs = list; state.source = "file";
      } catch (e2) {
        console.warn("No dev.json, using mock dialogs", e2);
        state.dialogs = MOCK.map(normalize); state.source = "mock";
      }
    }
  }
  if (state.source === "api" && state.dialogs.length) {
    state.groups = [g, ...state.groups.filter(x => x !== g)].slice(0, 6); save("cqa.groups", state.groups); save("cqa.group", g); renderGroups();
  }
  $("source-note").textContent = { api: `Dataset ${g}, loaded from the backend`, file: "Local dev.json: the backend is not reachable, so scores come from simple rules, not the AI.", mock: "Offline demo data: the backend is not reachable, so scores come from simple rules, not the AI." }[state.source];
  $("source-note").dataset.state = state.loadError ? "error" : state.source === "api" ? "live" : "offline";
  const key = state.source === "api" ? `api:${g}` : state.source;
  if (load("cqa.source.v2") !== key) { state.evals = {}; if (state.source !== "api") state.appeals = []; save("cqa.source.v2", key); persist(); }
  renderList(); renderTranscript(); renderEvaluation();
}

/* ---------- timestamps / audio ---------- */
const toSec = ts => {
  if (!ts) return 0;
  if (/^\d{1,2}:\d{2}$/.test(ts)) { const [m, s] = ts.split(":").map(Number); return m * 60 + s; }
  const t = Date.parse(String(ts).replace(/(\.\d{3})\d+/, "$1")); return isNaN(t) ? 0 : t / 1000;
};
const relSec = (d, i) => Math.max(0, toSec(d.messages[i]?.timestamp) - toSec(d.messages[0]?.timestamp));
const fmt = s => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const tsOf = (d, i) => d.type === "call" ? fmt(relSec(d, i) + d.audio_offset) : (d.messages[i]?.timestamp || "");

/* ---------- scoring (rules from the PDF; offline fallback only, the backend owns real totals) ---------- */
function calc(cl, answers) {
  const scored = cl.filter(c => !c.flag);
  const app = scored.filter(c => !(c.allowNA && answers[c.id] === "NA"));
  const W = app.reduce((s, c) => s + c.weight, 0);
  const k = W ? 100 / W : 0;                          // redistribute N/A weight; the maximum stays 100 (PDF step 4)
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

/* ---------- server JSON -> internal model ---------- */
const STATUS = { PASS: "YES", FAIL: "NO", YES: "YES", NO: "NO", NA: "NA", "N/A": "NA" };
const normAnswer = a => (typeof a === "string" && STATUS[a.toUpperCase()]) ? STATUS[a.toUpperCase()] : (a !== null && a !== "" && !isNaN(Number(a)) ? Number(a) : a);
// Reads the backend evaluation card (GET /api/evaluation/{id}, see API.md).
// results[].answer is the AI's own answer (reviewer changes come from state.appeals); totals, points and sections are the backend's.
function fromServer(j, d) {
  const last = Math.max(0, (d?.messages?.length || 1) - 1);
  const results = {}, rows = {};
  (j.criteria || []).forEach(c => {
    const id = String(c.id ?? c.criterion_id);
    const ev = Array.isArray(c.evidence) ? c.evidence.find(e => e?.message_index != null) : c.evidence;
    const rng = c.checked_range;   // backend: free text such as "весь диалог (00:02–00:20)"
    results[id] = {
      answer: normAnswer(c.original_answer ?? c.answer ?? c.status),
      evidence: c.evidence_index ?? ev?.message_index ?? null,
      checked: Array.isArray(rng) ? rng : rng ? [0, last] : null,
      checkedText: typeof rng === "string" ? rng : "",
      explanation: c.explanation || c.reason || c.na_reason || "",
    };
    const na = c.applicable === false || !!c.is_na || normAnswer(c.answer) === "NA";
    rows[id] = { na, earned: na ? 0 : (c.earned_points ?? c.points ?? 0), max: na ? 0 : (c.effective_weight ?? c.max_score ?? c.weight ?? 0) };
  });
  const sections = (j.sections || []).filter(s => (s.max_points ?? s.max) != null)
    .map(s => ({ id: s.id, name: s.name, earned: s.earned ?? s.points ?? 0, max: s.max ?? s.max_points ?? 0 }));
  return { results, engine: j.model ? `AI · ${j.model}` : (j.engine || "backend"), version: j.checklist_version ?? null,
    evaluationId: j.evaluation_id ?? null,
    server: { final: j.total ?? j.final_score ?? null, rows, sections, critical: !!j.critical_error,
      original: j.original_total ?? j.original_score ?? j.total ?? j.final_score ?? null } };
}
// Frontend answers ("YES" / "NO" / "NA" / number) -> backend answers ("yes" / "no" / "na" / number).
const toServerAnswer = a => ({ YES: "yes", NO: "no", NA: "na" })[a] ?? a;
// Frontend checklist rows -> PUT /api/checklist body (scoring_engine.Checklist + change_note).
function toServerChecklist(list, note = "Edited in the UI") {
  const sections = [];
  const secId = name => {
    name = (name || "").trim(); if (!name) return null;
    const id = (state.sections.find(s => s.name === name) || sections.find(s => s.name === name))?.id ?? name;
    if (!sections.some(s => s.id === id)) sections.push({ id, name });
    return id;
  };
  const criteria = list.map(c => ({
    id: c.id, name: c.name.trim(), section: secId(c.section),
    type: c.flag ? "flag" : c.type === "scale" ? "scale" : "yes_no",
    weight: c.flag ? 0 : Number(c.weight) || 0,
    allow_na: !c.flag && !!c.allowNA,
    scale: !c.flag && c.type === "scale" ? c.scale.map(s => ({ score: s, description: c.scaleDesc?.[s] ?? "" })) : [],
    description: c.description || "", na_condition: c.allowNA ? (c.naRule || "") : "",
  }));
  const m = state.checklistMeta || {};
  return { id: m.id || "default", name: m.name || "Checklist", notes: m.notes || [], sections, criteria, change_note: note };
}
// Applies a backend checklist response ({version, checklist}) to the state.
function applyServerChecklist(j) {
  const cl = j?.checklist ?? j, list = Array.isArray(cl) ? cl : cl?.criteria;
  if (!Array.isArray(list) || !list.length) return false;
  state.sections = Array.isArray(cl?.sections) ? cl.sections : [];
  state.checklistMeta = { id: cl?.id, name: cl?.name, notes: cl?.notes || [] };
  state.checklist = upgradeChecklist(list, state.sections);
  state.checklistVersion = j?.version ?? state.checklistVersion;
  return true;
}
function fromServerAppeal(a) {
  const dec = a.decision ?? (a.decision_answer != null ? { answer: a.decision_answer, reason: a.decision_reason || "" } : null);
  const resolved = !!dec || ["resolved", "decided", "closed", "confirmed", "changed"].includes(String(a.status).toLowerCase());
  return { id: String(a.id ?? a.appeal_id), dialogId: String(a.conversation_id ?? a.dialog_id), critId: String(a.criterion_id ?? a.critId), comment: a.comment || "",
    status: resolved && dec ? "resolved" : "open", decision: dec ? { answer: normAnswer(dec.answer), reason: dec.reason || "" } : null };
}
// Backend mode: totals come from the server. Local calc() is only the offline-demo fallback.
function scoreOf(id, review) {
  const ev = state.evals[id]; if (!ev) return null;
  if (ev.server) return review ? ev.server : { ...ev.server, final: ev.server.original };
  return calc(state.checklist, answersFor(id, review));
}
async function loadChecklist() {
  if (state.source !== "api") return;
  try {
    if (applyServerChecklist(await api(EP.checklist))) { persist(); renderEvaluation(); }
  } catch (e) { console.warn("Checklist not loaded from the backend; using the saved one", e); }
}
async function loadAppeals() {
  if (state.source !== "api") return;
  try {
    const j = await api(EP.appeals()), list = Array.isArray(j) ? j : (j?.appeals ?? []);
    state.appeals = list.map(fromServerAppeal); state.appealsNote = ""; persist();
  } catch (e) {
    state.appealsNote = `Appeals could not be loaded from the backend (${e.message}). Showing the copy saved in this browser.`;
  }
  renderList();
}

/* ---------- evaluation ---------- */
// The backend uses its active checklist and returns the saved result if this dialog was already
// evaluated under it (no AI call). force = call the AI again; it spends free-tier quota.
async function evaluate(d, force = false) {
  try {
    const j = await api(EP.evaluate(d.id, force), { method: "POST", timeout: EVAL_TIMEOUT_MS });
    if (!j?.criteria) throw new Error("unexpected response format");
    return fromServer(j, d);
  } catch (e) {
    if (state.source === "api") throw e;   // never hide backend errors behind fake results
    return { results: heuristic(d), engine: "local demo heuristics (offline)" };
  }
}

// Offline demo evaluator for the 8 default criteria (same ids as the backend checklist). Real scoring belongs in the backend LLM.
function heuristic(d) {
  const m = d.messages, ops = m.map((x, i) => [x, i]).filter(([x]) => x.speaker === "operator");
  const find = (re, who) => { const h = m.findIndex(x => (!who || x.speaker === who) && re.test(x.text)); return h < 0 ? null : h; };
  const out = {}, first = ops[0]?.[1] ?? null, last = ops.at(-1)?.[1] ?? null;
  const hello = /здравств|добр(ый|ое|ого)|привет|сәлем|hello|good (morning|day|afternoon|evening)|\bhi\b/i, intro = /(?:[Мм]еня зовут|[Яя]|[Мм]ен|[Мм]енің атым|[Mm]y name is|I['’]m|This is)\s+\p{Lu}/u;
  const bye = /до свидания|всего доброго|хорошего (дня|вечера)|сау болыңыз|қош болыңыз|күніңіз сәтті|good ?bye|\bbye\b|take care|have a (great|nice|good)|остались ли|anything else/i;
  for (const c of state.checklist) {
    let r;
    switch (c.id) {
      case "contact": r = first === null ? { answer: "NO", checked: [0, m.length - 1], explanation: "Оператор не ответил." }
        : hello.test(m[first].text) && intro.test(m[first].text) ? { answer: "YES", evidence: first, explanation: "Оператор поздоровался и представился в первом ответе." }
        : { answer: "NO", evidence: first, explanation: "В первом ответе нет приветствия или представления." }; break;
      case "needs": { const q = ops.find(([x]) => x.text.includes("?"));
        r = q ? { answer: "YES", evidence: q[1], explanation: "Оператор уточнил недостающее до ответа." } : { answer: "NA", checked: [0, m.length - 1], explanation: "Данных для ответа хватало с самого начала." }; break; }
      case "identification": { const s = find(/баланс|вывод|withdraw|balance|пароль|password|иин|iin/i, "client");
        if (s === null) r = { answer: "NA", checked: [0, m.length - 1], explanation: "Защищённые данные или действия не запрашивались: идентификация не требовалась." };
        else { const v = find(/кодов|идентиф|verify|иин|iin|подтверд|растау/i, "operator");
          r = v !== null && v > s - 1 ? { answer: "YES", evidence: v, explanation: "Личность проверена до защищённого действия." } : { answer: "NO", checked: [s, Math.min(s + 2, m.length - 1)], explanation: "Проверка личности не подтверждена в чате." }; } break; }
      case "consultation": r = ops.length >= 2 ? { answer: c.scale[1] ?? c.scale[0], evidence: ops[1][1], explanation: "Основной вопрос решён (демо-оценка)." } : { answer: c.scale.at(-2) ?? 0, checked: [0, m.length - 1], explanation: "Недостаточно ответов оператора (демо-оценка)." }; break;
      case "empathy": r = { answer: "YES", evidence: first, explanation: "Оператор вежлив; жалоб клиента нет." }; break;
      case "dialogue": r = { answer: c.scale[0], checked: [0, m.length - 1], explanation: "Ответы ясные и связные (демо-оценка)." }; break;
      case "closing": { const b = last !== null && bye.test(m[last].text);
        r = b ? { answer: "YES", evidence: last, explanation: "Оператор предложил помощь/попрощался." } : { answer: "NO", evidence: last, explanation: "В последней реплике оператора нет итога или прощания." }; break; }
      case "gross_errors": { const h = find(/переведите мне|личный номер|personal number|send (the )?money to me|дурак|идиот/i, "operator");
        r = h !== null ? { answer: "YES", evidence: h, explanation: "Найдено грубое нарушение." } : { answer: "NO", checked: [0, m.length - 1], explanation: "Оскорблений, услуг в обход компании и раскрытия данных не выявлено." }; break; }
      default: r = { answer: c.allowNA ? "NA" : (c.type === "yesno" ? "NO" : 0), checked: [0, m.length - 1], explanation: "Новый критерий: требуется бэкенд-оценка (/api/evaluate)." };
    }
    out[c.id] = r;
  }
  return out;
}

async function runEvaluation() {
  const d = sel(); if (!d) return;
  const btn = $("btn-run"); btn.disabled = true; setIconLabel(btn, "bolt", "Evaluating…");
  $("criteria-container").innerHTML = `<p class="text-center py-10" style="color:var(--muted)">Evaluating this conversation…</p>`;
  try { state.evals[d.id] = await evaluate(d); persist(); }
  catch (e) {
    $("criteria-container").innerHTML = `<p class="text-red-700 text-center py-10">Evaluation failed: ${esc(e.message)}. Check the backend and try again.</p>`;
    btn.disabled = false; setIconLabel(btn, "bolt", "Run evaluation"); return;
  }
  btn.disabled = false; setIconLabel(btn, "bolt", "Run evaluation");
  renderEvaluation(); renderList();
}

/* ---------- batch evaluation ---------- */
let batch = null;   // { total, done, failed: [ids], stop }
async function runBatch(ids, force = false) {
  if (batch || !ids.length) return;
  batch = { total: ids.length, done: 0, failed: [], stop: false };
  showBatch();
  const queue = [...ids];
  const worker = async () => {
    while (queue.length && !batch.stop) {
      const id = queue.shift(), d = state.dialogs.find(x => x.id === id);
      if (!d) continue;
      try { state.evals[id] = await evaluate(d, force); delete d.failed; }
      catch (e) { batch.failed.push(id); d.failed = e.message; console.warn("Evaluation failed for", id, e); }
      batch.done++; persist(); showBatch(); renderList();
      if (id === state.selId) renderEvaluation();
    }
  };
  await Promise.all(Array.from({ length: Math.min(BATCH_CONCURRENCY, ids.length) }, worker));
  const finished = batch; batch = null;
  showBatch(finished); renderList();
}

/* ---------- selection ---------- */
async function loadDialogDetails(id) {
  const d = state.dialogs.find(x => x.id === id);
  if (d && !d.messages.length) { const score = d.score; Object.assign(d, normalize(await api(EP.dialogue(id)))); d.score ??= score; }
  try { state.evals[id] = fromServer(await api(EP.evaluation(id)), d); }
  catch (e) { if (e.status === 404) delete state.evals[id]; else throw e; }
}
async function selectDialog(id) {
  state.selId = id; renderList(); renderTranscript(); renderEvaluation();
  if (state.source !== "api") return;
  let failed = null;
  $("criteria-container").innerHTML = `<p class="text-slate-500 text-center py-10">Loading...</p>`;
  try { await loadDialogDetails(id); } catch (err) { failed = err.message; }
  if (state.selId !== id) return;
  renderTranscript(); renderEvaluation(); renderList();
  if (failed) $("criteria-container").innerHTML = `<p class="text-red-700 text-center py-10">Could not load the dialog: ${esc(failed)}</p>`;
}


/* ---------- theme ---------- */
function initTheme() {
  const btn = $("btn-theme");
  const sync = () => {
    const dark = document.documentElement.dataset.theme === "dark";
    setIconLabel(btn, dark ? "sun" : "moon", dark ? "Light theme" : "Dark theme");
    btn.setAttribute("aria-pressed", String(dark));
  };
  btn.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next; try { localStorage.setItem("cqa.theme", next); } catch {}
    sync();
  });
  sync();
}

/* ---------- start-up and shared click handling ---------- */
// One click handler for buttons that components render with data-* attributes.
document.addEventListener("click", e => {
  const t = e.target.closest("[data-dialog],[data-goto],[data-dispute],[data-decide],[data-close],[data-del]"); if (!t) return;
  if (t.dataset.dialog) selectDialog(t.dataset.dialog);
  else if (t.dataset.goto) goTo(+t.dataset.goto, t.dataset.to ? +t.dataset.to : +t.dataset.goto);
  else if (t.dataset.dispute) openAppeal(t.dataset.dispute);
  else if (t.dataset.decide) decide(t.dataset.decide);
  else if (t.dataset.close) closeModal(t.dataset.close);
  else if (t.dataset.del) { state.draft.splice(+t.dataset.del, 1); renderChecklistEditor(); }
});
document.addEventListener("DOMContentLoaded", async () => {
  hydrateIcons();
  initTheme(); initSidebar(); initDialogueList(); initScorePanel(); initAppealModal(); initChecklistEditor();
  await fetchDialogs();
  await loadChecklist();
  await loadAppeals();
});
window.addEventListener("beforeunload", e => { if (batch) { e.preventDefault(); e.returnValue = ""; } });

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
