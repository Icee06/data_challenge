"""CQA analyst pipeline: steps 3–6 and 8 from the analyst plan.

Reads the AI results, writes them into manual_evaluation.xlsx next to the manual answers, groups the
disagreements by error type, and writes the reports and charts:

  reports/errors.md        step 4: errors grouped by type and criterion, top 5 for the AI engineer
  reports/before_after.md  step 5: first run vs re-run, working and held-out separately
  reports/analytics.md     step 6: all 65 dialogues from the AI results
  reports/jury.md          step 8: agreement table, charts and examples
  charts/*.png             2 analytics charts + the agreement chart for the jury

Usage (from the qa/ folder):
  python compare.py --dataset ../backend/data/DEV.json --first ../results/dev/results.json
  python compare.py --dataset ../backend/data/DEV.json --first ../results/dev/results.json --rerun ../results/dev/results_v2.json

Answers it understands: YES/NO/NA (also PASS/FAIL/N/A, Да/Нет, true/false) and scale steps.
Criterion ids: c1…c8 or the names contact, needs, identification, consultation, empathy, dialogue, closing, gross_errors.
"""
import argparse, json, os, re, sys
from collections import Counter, defaultdict

from openpyxl import load_workbook

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from manual_scores import SCORES  # noqa: E402  (blind manual answers, also written into the workbook)

CRIT = ["c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8"]
NAME = {"c1": "Установление контакта", "c2": "Выявление потребности", "c3": "Идентификация", "c4": "Консультация",
        "c5": "Эмпатия", "c6": "Управление диалогом", "c7": "Завершение", "c8": "Грубые ошибки"}
WEIGHT = {"c1": 10, "c2": 15, "c3": 20, "c4": 20, "c5": 15, "c6": 15, "c7": 5, "c8": 0}
TOP = {"c4": 20, "c6": 10}
NA_OK = {"c2", "c3", "c4", "c7"}
ALIASES = {
    "c1": ["contact", "greeting", "установление"], "c2": ["need", "потребност"], "c3": ["ident", "kyc", "verification", "идентиф"],
    "c4": ["consult", "solution", "answer_quality", "консульт"], "c5": ["empathy", "эмпат"],
    "c6": ["dialog", "management", "speed", "grammar", "управлен"], "c7": ["clos", "ending", "farewell", "заверш"],
    "c8": ["gross", "critical", "violation", "груб"],
}
RULE = {
    "c1": "Приветствие оценивается по первой реплике ОПЕРАТОРА (не бота): нужны и приветствие на языке клиента, и имя.",
    "c2": "N/A — только если данных хватало с самого начала; «Да» — оператор учёл известные детали и уточнил недостающее; «Нет» — проигнорировал детали.",
    "c3": "«Нет» — данные раскрыты или выполнено защищённое действие без подтверждения личности; N/A — если оператор только подсказал путь в меню.",
    "c4": "Ступень по тому, закрыты ли ВСЕ существенные вопросы клиента: ответ мимо второго вопроса — 10, пропущена мелочь — 15.",
    "c5": "При недовольстве клиента нужна реакция на конкретную проблему, а не формальная вежливость.",
    "c6": "Клиенту пришлось переспрашивать из-за формулировок — 4; один ответ мимо вопроса без переспроса — 7.",
    "c7": "«Да» — только итог ИЛИ предложение помощи И прощание; одно прощание — «Нет»; клиент ушёл — N/A.",
    "c8": "Флаг только при оскорблении, услуге в обход компании или раскрытии конфиденциальных данных.",
}
WORK = "p1-ru-001 p1-ru-003 p1-ru-007 p1-ru-010 p1-ru-020 p1-ru-025 p1-kz-001 p1-kz-004 p1-en-003 p1-en-007 p4-ru-001 p4-ru-002".split()
HELD = "p1-ru-006 p1-ru-015 p1-ru-024 p1-kz-003 p1-kz-010 p1-en-006 p1-en-010 p4-ru-005".split()
GROUP = {**{i: "Working" for i in WORK}, **{i: "Held-out" for i in HELD}}

# chart tokens (light mode of the reference palette)
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
S1, S2 = "#2a78d6", "#eb6834"


# ---------------- loading ----------------
def load_json(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise SystemExit(f"Cannot read {path} as JSON")


def load_dataset(path):
    j = load_json(path)
    recs = j["data"]["records"] if isinstance(j, dict) and "data" in j else (j.get("records", j) if isinstance(j, dict) else j)
    return {r["conversation_id"]: r for r in recs}


def crit_id(raw):
    s = str(raw or "").strip().lower()
    m = re.fullmatch(r"c?0?([1-8])", s)
    if m:
        return "c" + m.group(1)
    for c, keys in ALIASES.items():
        if any(k in s for k in keys):
            return c
    return None


def norm_answer(a):
    if isinstance(a, bool):
        return "YES" if a else "NO"
    if a is None:
        return None
    if isinstance(a, (int, float)):
        return int(a) if float(a).is_integer() else a
    s = str(a).strip()
    u = s.upper()
    table = {"YES": "YES", "PASS": "YES", "ДА": "YES", "TRUE": "YES", "NO": "NO", "FAIL": "NO", "НЕТ": "NO", "FALSE": "NO",
             "NA": "NA", "N/A": "NA", "NOT_APPLICABLE": "NA", "NOT APPLICABLE": "NA"}
    if u in table:
        return table[u]
    try:
        f = float(s)
        return int(f) if f.is_integer() else f
    except ValueError:
        return s


def evidence_index(c):
    for k in ("evidence_index", "message_index", "evidence_message", "msg_index"):
        if isinstance(c.get(k), int):
            return c[k]
    ev = c.get("evidence")
    if isinstance(ev, dict):
        for k in ("message_index", "index", "msg_index", "message"):
            if isinstance(ev.get(k), int):
                return ev[k]
    if isinstance(ev, list) and ev and isinstance(ev[0], dict):
        return evidence_index({"evidence": ev[0]})
    return None


def load_results(path):
    """Returns {(conversation_id, cN): {answer, evidence, quote, reason}} and {conversation_id: final_score or None}."""
    j = load_json(path)
    if isinstance(j, dict):
        items = j.get("results") or j.get("evaluations") or j.get("data") or j.get("records") or [j]
        if isinstance(items, dict):
            items = items.get("records") or items.get("results") or list(items.values())
    else:
        items = j
    ans, totals = {}, {}
    for it in items:
        if not isinstance(it, dict):
            continue
        cid = str(it.get("conversation_id") or it.get("dialogue_id") or it.get("id") or "")
        if not cid:
            continue
        crits = it.get("criteria") or it.get("results") or it.get("answers") or []
        if isinstance(crits, dict):
            crits = [{"id": k, **(v if isinstance(v, dict) else {"answer": v})} for k, v in crits.items()]
        for c in crits:
            k = crit_id(c.get("id") or c.get("criterion_id") or c.get("criterion") or c.get("code") or c.get("name"))
            if not k:
                continue
            a = c.get("answer", c.get("status", c.get("result", c.get("value"))))
            if k in TOP and a is not None and norm_answer(a) in ("YES", "NO") and isinstance(c.get("score"), (int, float)):
                a = c["score"]          # scale criterion reported as PASS/FAIL plus a score
            ev = c.get("evidence") if isinstance(c.get("evidence"), dict) else {}
            ans[(cid, k)] = {"answer": norm_answer(a), "evidence": evidence_index(c),
                             "quote": c.get("quote") or ev.get("quote"), "reason": c.get("explanation") or c.get("reason") or ""}
        fs = it.get("final_score", it.get("total", it.get("score")))
        totals[cid] = fs if isinstance(fs, (int, float)) else None
    if not ans:
        raise SystemExit(f"No criterion answers found in {path}; check its format against the docstring.")
    return ans, totals


# ---------------- scoring (checklist PDF rules) ----------------
def total(answers):
    app = [c for c in CRIT if c != "c8" and answers.get(c) not in (None, "NA")]
    W = sum(WEIGHT[c] for c in app)
    if not W:
        return None
    pts = 0.0
    for c in app:
        a = answers[c]
        ratio = (a / TOP[c] if isinstance(a, (int, float)) else 0) if c in TOP else (1 if a == "YES" else 0)
        pts += WEIGHT[c] * 100 / W * ratio
    return round(pts, 2)


def same(a, b):
    return a is not None and b is not None and str(a) == str(b)


# ---------------- error grouping ----------------
def classify(c, manual, ai, ai_ev, man_ev, rec):
    msgs = rec["transcript"] if rec else []
    spk = msgs[ai_ev]["speaker"] if isinstance(ai_ev, int) and 0 <= ai_ev < len(msgs) else None
    if c == "c8":
        return "False critical" if ai == "YES" else "Missed critical"
    if manual == "NA" or ai == "NA":
        return "Wrong N/A"
    if c in TOP and isinstance(manual, (int, float)) and isinstance(ai, (int, float)):
        return "Wrong scale step"
    if spk in ("bot", "system") and c in ("c1", "c2", "c5", "c7"):
        return "Bot confused"
    if rec and rec.get("language") in ("KZ", "EN") and c in ("c1", "c7"):
        return "Language"
    return "Wrong answer"


def evidence_problem(c, ai_ev, quote, rec, answer=None):
    """For rows where the answers agree: is the AI's evidence itself wrong?"""
    msgs = rec["transcript"] if rec else []
    if ai_ev is None:
        return None
    if answer == "NA":
        return None          # N/A may rightly cite a system event, e.g. "client left the chat"
    if not (0 <= ai_ev < len(msgs)):
        return f"номер сообщения #{ai_ev} вне диалога"
    m = msgs[ai_ev]
    if c in ("c1", "c5", "c7") and m["speaker"] in ("bot", "system"):
        return f"сослался на реплику {m['speaker']} (#{ai_ev}), а оценивается оператор"
    if quote and quote.strip().strip("«»\"“”") not in m["text"]:
        return f"цитата не найдена в сообщении #{ai_ev}"
    return None


def fmt_ans(a):
    return {"YES": "Да", "NO": "Нет", "NA": "N/A", None: "—"}.get(a, str(a))


# ---------------- workbook ----------------
def fill_workbook(path, first, rerun, mismatches):
    wb = load_workbook(path)
    ws = wb["Manual scoring"]
    for r in range(2, ws.max_row + 1):
        key = (ws.cell(r, 1).value, ws.cell(r, 2).value)
        if key in first and first[key]["answer"] is not None:
            ws.cell(r, 5, first[key]["answer"])
        if rerun and key in rerun and rerun[key]["answer"] is not None:
            ws.cell(r, 15, rerun[key]["answer"])
        if key in mismatches:
            et, rec_text = mismatches[key]
            if not ws.cell(r, 9).value:
                ws.cell(r, 9, et)          # suggested; the analyst confirms or changes it
            if not ws.cell(r, 10).value:
                ws.cell(r, 10, rec_text)
    wb.calculation.fullCalcOnLoad = True    # Excel recalculates Match and Summary on open
    wb.save(path)


# ---------------- charts ----------------
def style_axes(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=10, length=0)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def hbar(path, labels, values, title, xlabel, xmax=100, fmt="{:.0f}%", note=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(8, 0.48 * len(labels) + 1.6), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    style_axes(ax)
    y = range(len(labels))[::-1]
    bars = ax.barh(list(y), values, color=S1, height=0.62)
    for b, v in zip(bars, values):
        ax.text(b.get_width() + xmax * 0.01, b.get_y() + b.get_height() / 2, fmt.format(v), va="center", fontsize=9.5, color=INK)
    ax.set_yticks(list(y)); ax.set_yticklabels(labels, color=INK)
    ax.set_xlim(0, xmax * 1.1); ax.set_xlabel(xlabel, color=INK2, fontsize=10)
    ax.set_title(title, loc="left", color=INK, fontsize=12.5, fontweight="bold", pad=12)
    if note:
        fig.text(0.01, 0.01, note, fontsize=8.5, color=INK2)
    fig.tight_layout(rect=(0, 0.04 if note else 0, 1, 1))
    fig.savefig(path, facecolor=SURFACE); plt.close(fig)


def hbar_pair(path, labels, a, b, la, lb, title, xlabel, note=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(8, 0.62 * len(labels) + 1.8), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    style_axes(ax)
    n = len(labels); h = 0.36
    ys = [n - 1 - i for i in range(n)]
    ax.barh([y + h / 2 + 0.01 for y in ys], a, height=h, color=S1, label=la)
    ax.barh([y - h / 2 - 0.01 for y in ys], b, height=h, color=S2, label=lb)
    for y, v1, v2 in zip(ys, a, b):
        ax.text(v1 + 1, y + h / 2, f"{v1:.0f}%", va="center", fontsize=9, color=INK)
        ax.text(v2 + 1, y - h / 2, f"{v2:.0f}%", va="center", fontsize=9, color=INK)
    ax.set_yticks(ys); ax.set_yticklabels(labels, color=INK)
    ax.set_xlim(0, 110); ax.set_xlabel(xlabel, color=INK2, fontsize=10)
    ax.set_title(title, loc="left", color=INK, fontsize=12.5, fontweight="bold", pad=30)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2, frameon=False, fontsize=9.5, labelcolor=INK, borderaxespad=0.2)
    if note:
        fig.text(0.01, 0.01, note, fontsize=8.5, color=INK2)
    fig.tight_layout(rect=(0, 0.04 if note else 0, 1, 1))
    fig.savefig(path, facecolor=SURFACE); plt.close(fig)


# ---------------- main ----------------
def agreement(rows):
    comp = [r for r in rows if r["ai"] is not None]
    ok = sum(r["match"] for r in comp)
    return (ok / len(comp) * 100 if comp else None), ok, len(comp)


def pct(v):
    return "—" if v is None else f"{v:.1f}%"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True, help="organizers' DEV response or cache (DEV.json)")
    ap.add_argument("--first", required=True, help="AI results of the first run (results/dev/results.json)")
    ap.add_argument("--rerun", help="AI results after the prompt fix (the 20 sample dialogues at least)")
    ap.add_argument("--workbook", default=os.path.join(HERE, "manual_evaluation.xlsx"))
    ap.add_argument("--out", default=HERE)
    a = ap.parse_args()

    data = load_dataset(a.dataset)
    first, first_tot = load_results(a.first)
    rerun, _ = load_results(a.rerun) if a.rerun else (None, None)
    rep_dir, ch_dir = os.path.join(a.out, "reports"), os.path.join(a.out, "charts")
    os.makedirs(rep_dir, exist_ok=True); os.makedirs(ch_dir, exist_ok=True)

    # ---- step 3: compare on the 20 sample dialogues ----
    rows = []
    for cid, crits in SCORES.items():
        for c, (man, man_ev, note) in zip(CRIT, crits):
            f = first.get((cid, c), {}); r2 = (rerun or {}).get((cid, c), {})
            rows.append({"cid": cid, "c": c, "group": GROUP[cid], "lang": data.get(cid, {}).get("language"), "manual": man, "man_ev": man_ev,
                         "note": note, "ai": f.get("answer"), "ai_ev": f.get("evidence"), "quote": f.get("quote"), "reason": f.get("reason", ""),
                         "match": same(man, f.get("answer")), "ai2": r2.get("answer") if rerun else None,
                         "match2": same(man, r2.get("answer")) if rerun else None})
    missing = sorted({r["cid"] for r in rows if r["ai"] is None})

    # ---- step 4: group disagreements ----
    mism, ev_issues = {}, []
    for r in rows:
        rec = data.get(r["cid"])
        if r["ai"] is not None and not r["match"]:
            r["etype"] = classify(r["c"], r["manual"], r["ai"], r["ai_ev"], r["man_ev"], rec)
            ev_txt = f" (#{r['man_ev']})" if r["man_ev"] is not None else ""
            mism[(r["cid"], r["c"])] = (r["etype"], f"ИИ: {fmt_ans(r['ai'])}, вручную: {fmt_ans(r['manual'])}{ev_txt}. {RULE[r['c']]}")
        elif r["match"]:
            prob = evidence_problem(r["c"], r["ai_ev"], r["quote"], rec, r["ai"])
            if prob:
                ev_issues.append((r, prob))
    fill_workbook(a.workbook, first, rerun, mism)

    err = [r for r in rows if r.get("etype")]
    by_type = Counter(r["etype"] for r in err)
    by_crit = Counter(r["c"] for r in err)
    by_pair = Counter((r["c"], r["etype"]) for r in err)

    def line(r):
        why = r["note"].rstrip(".")
        ev = f" (#{r['man_ev']})" if r["man_ev"] is not None else ""
        return f"«{r['cid']}, {r['c']}: ИИ сказал {fmt_ans(r['ai'])}, а должно быть {fmt_ans(r['manual'])}: {why}{ev}. Правило: {RULE[r['c']]}»"

    top, used = [], set()
    for (c, et), _n in by_pair.most_common():
        for r in err:
            if r["c"] == c and r["etype"] == et and (r["cid"], c) not in used and r["group"] == "Working":
                top.append(r); used.add((r["cid"], c)); break
        if len(top) == 5:
            break

    md = ["# Ошибки ИИ на ручной выборке (шаг 4)", "",
          f"Сравнено ответов: {sum(r['ai'] is not None for r in rows)} из {len(rows)} (20 диалогов × 8 критериев). "
          f"Расхождений: {len(err)}. Совпадение: {pct(agreement(rows)[0])}.", ""]
    if missing:
        md += [f"Нет ответов ИИ для: {', '.join(missing)}.", ""]
    md += ["## По типу ошибки", "", "| Тип | Кол-во | Критерии |", "|---|---:|---|"]
    for et, n in by_type.most_common():
        cs = Counter(r["c"] for r in err if r["etype"] == et)
        md.append(f"| {et} | {n} | {', '.join(f'{c} ({k})' for c, k in cs.most_common())} |")
    md += ["", "## По критерию", "", "| Критерий | Расхождений | Совпадение |", "|---|---:|---:|"]
    for c in CRIT:
        cr = [r for r in rows if r["c"] == c]
        md.append(f"| {c} {NAME[c]} | {by_crit.get(c, 0)} | {pct(agreement(cr)[0])} |")
    md += ["", "## Топ-5 для ИИ-инженера (только рабочая выборка)", ""]
    md += [f"{i}. {line(r)}" for i, r in enumerate(top, 1)] or ["Расхождений нет."]
    if ev_issues:
        md += ["", "## Ответ совпал, но доказательство неверное (Bad evidence / Bot confused)", ""]
        md += [f"- {r['cid']}, {r['c']}: {p}" for r, p in ev_issues]
    md += ["", "## Все расхождения", "", "| Диалог | Выборка | Критерий | Вручную | ИИ | Тип | Наше обоснование |", "|---|---|---|---|---|---|---|"]
    md += [f"| {r['cid']} | {r['group']} | {r['c']} | {fmt_ans(r['manual'])} | {fmt_ans(r['ai'])} | {r['etype']} | {r['note']} |" for r in err]
    md += ["", "Отложенные диалоги в топ-5 не попадают: ИИ-инженеру их не показываем."]
    open(os.path.join(rep_dir, "errors.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")

    # ---- step 5: before / after ----
    if rerun:
        md = ["# Было / стало (шаг 5)", "", "| Выборка | Было | Стало | Сравнено |", "|---|---:|---:|---:|"]
        for lab, grp in (("Рабочие (12)", "Working"), ("Отложенные (8)", "Held-out"), ("Все 20", None)):
            rr = [r for r in rows if grp is None or r["group"] == grp]
            b = agreement(rr)
            aft = [dict(r, ai=r["ai2"], match=r["match2"]) for r in rr]
            af = agreement(aft)
            md.append(f"| {lab} | {pct(b[0])} | {pct(af[0])} | {b[2]} / {af[2]} |")
        md += ["", "Если на рабочих стало заметно лучше, а на отложенных — нет, промпт подогнан под рабочую выборку.", "",
               "## По критериям", "", "| Критерий | Было | Стало |", "|---|---:|---:|"]
        for c in CRIT:
            rr = [r for r in rows if r["c"] == c]
            md.append(f"| {c} {NAME[c]} | {pct(agreement(rr)[0])} | {pct(agreement([dict(r, ai=r['ai2'], match=r['match2']) for r in rr])[0])} |")
        fixed = [r for r in rows if not r["match"] and r["match2"]]
        broke = [r for r in rows if r["match"] and r["match2"] is False]
        md += ["", f"## Исправлено ({len(fixed)})", ""] + [f"- {r['cid']} {r['c']}: {fmt_ans(r['ai'])} → {fmt_ans(r['ai2'])} ({r['group']})" for r in fixed]
        md += ["", f"## Сломалось ({len(broke)})", ""] + ([f"- {r['cid']} {r['c']}: было верно {fmt_ans(r['ai'])}, стало {fmt_ans(r['ai2'])} ({r['group']})" for r in broke] or ["Нет."])
        open(os.path.join(rep_dir, "before_after.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")

    # ---- step 6: analytics over all 65 from the AI results ----
    convs = sorted({k[0] for k in first})
    per = {cid: {c: first.get((cid, c), {}).get("answer") for c in CRIT} for cid in convs}
    tot = {cid: (first_tot.get(cid) if first_tot.get(cid) is not None else total(per[cid])) for cid in convs}
    vals = [v for v in tot.values() if v is not None]
    fulfil, na_rate = {}, {}
    for c in CRIT[:-1]:
        got = [per[cid][c] for cid in convs if per[cid][c] is not None]
        app = [a for a in got if a != "NA"]
        if c in TOP:
            fulfil[c] = sum((a / TOP[c]) if isinstance(a, (int, float)) else 0 for a in app) / len(app) * 100 if app else None
        else:
            fulfil[c] = sum(a == "YES" for a in app) / len(app) * 100 if app else None
        na_rate[c] = sum(a == "NA" for a in got) / len(got) * 100 if got else None
    gross = [cid for cid in convs if per[cid]["c8"] == "YES"]
    groups = defaultdict(list)
    for cid in convs:
        rec = data.get(cid, {})
        key = f"{rec.get('language', '?')} {'звонок' if rec.get('channel') == 'voice call' else 'чат'}"
        if tot[cid] is not None:
            groups[key].append(tot[cid])
    weakest = sorted([c for c in fulfil if fulfil[c] is not None], key=lambda c: fulfil[c])[:3]
    md = ["# Аналитика по всем диалогам (шаг 6)", "", f"Диалогов в результатах ИИ: {len(convs)}.", "",
          f"- Средний балл: **{sum(vals) / len(vals):.2f}** из 100 (по {len(vals)} диалогам с итогом)." if vals else "- Средний балл: нет итогов.",
          f"- Самые слабые критерии: " + ", ".join(f"{c} {NAME[c]} ({fulfil[c]:.0f}%)" for c in weakest) + ".",
          f"- Грубые ошибки: **{len(gross)}**" + (f" ({', '.join(gross)})." if gross else "."), "",
          "## Выполнение и N/A по критериям", "", "| Критерий | Выполнение (без N/A) | Частота N/A |", "|---|---:|---:|"]
    md += [f"| {c} {NAME[c]} | {pct(fulfil[c])} | {pct(na_rate[c])} |" for c in CRIT[:-1]]
    md += ["", "Выполнение: для да/нет — доля «Да», для шкал — средняя доля от верхней ступени.", "",
           "## Язык и канал", "", "| Группа | Диалогов | Средний балл |", "|---|---:|---:|"]
    md += [f"| {g} | {len(v)} | {sum(v) / len(v):.2f} |" for g, v in sorted(groups.items())]
    md += ["", "Графики: `charts/criteria_fulfilment.png`, `charts/score_by_language_channel.png`."]
    open(os.path.join(rep_dir, "analytics.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
    order = [c for c in CRIT[:-1] if fulfil[c] is not None]
    hbar(os.path.join(ch_dir, "criteria_fulfilment.png"), [f"{c} {NAME[c]}" for c in order], [fulfil[c] for c in order],
         "Выполнение критериев по оценке ИИ", "% диалогов, где критерий выполнен (N/A не учитываются)",
         note=f"Результаты ИИ, {len(convs)} диалогов DEV.")
    gl = sorted(groups)
    hbar(os.path.join(ch_dir, "score_by_language_channel.png"), [f"{g} ({len(groups[g])})" for g in gl], [sum(groups[g]) / len(groups[g]) for g in gl],
         "Средний балл по языку и каналу", "Средний итоговый балл, 0–100", fmt="{:.1f}", note="В скобках — число диалогов.")

    # ---- step 8: jury ----
    w, h, al = agreement([r for r in rows if r["group"] == "Working"]), agreement([r for r in rows if r["group"] == "Held-out"]), agreement(rows)
    md = ["# Материалы для жюри (шаг 8)", "", "Формулировка: «совпадение с нашей ручной выборкой», не «точность».", "",
          "| Выборка | Совпадение (первый прогон) | Совпадение (после исправления) | Ответов |", "|---|---:|---:|---:|"]
    for lab, grp, v in (("Все 20 диалогов", None, al), ("Рабочие (12)", "Working", w), ("Отложенные (8), промпт их не видел", "Held-out", h)):
        rr = [r for r in rows if grp is None or r["group"] == grp]
        v2 = agreement([dict(r, ai=r["ai2"], match=r["match2"]) for r in rr])[0] if rerun else None
        md.append(f"| {lab} | {pct(v[0])} | {pct(v2)} | {v[2]} |")
    good = [r for r in rows if r["match"] and r["manual"] in ("NO", "NA", 10, 7, 4) and r["ai_ev"] is not None][:3]
    bad = [r for r in rows if not r["match"] and r["ai"] is not None and r["group"] == "Working"][:2]
    md += ["", "Графики: `charts/agreement_by_criterion.png`, `charts/criteria_fulfilment.png` (и `charts/score_by_language_channel.png`).", "",
           "## ИИ прав, с доказательством", ""]
    md += [f"- {r['cid']}, {r['c']} {NAME[r['c']]}: ИИ — {fmt_ans(r['ai'])}, доказательство #{r['ai_ev']}. {r['note']}" for r in good] or ["Подобрать после прогона."]
    md += ["", "## ИИ ошибся — исправлено апелляцией (показать в интерфейсе)", ""]
    md += [f"- {r['cid']}, {r['c']} {NAME[r['c']]}: ИИ — {fmt_ans(r['ai'])}, проверяющий меняет на {fmt_ans(r['manual'])}. Причина для апелляции: {r['note']}" for r in bad] or ["Расхождений нет."]
    md += ["", "## Ограничения", "", "- Ручная выборка — 20 диалогов, оценены одним аналитиком; это не официальный Ground Truth.",
           "- В звонках возможны ошибки распознавания речи (имена, приветствия).",
           "- Спорные правила (c7 «только прощание», c3 «только путь в меню») записаны в `rules.md`."]
    open(os.path.join(rep_dir, "jury.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
    labels = [f"{c} {NAME[c]}" for c in CRIT]
    v1 = [agreement([r for r in rows if r["c"] == c])[0] or 0 for c in CRIT]
    if rerun:
        v2 = [agreement([dict(r, ai=r["ai2"], match=r["match2"]) for r in rows if r["c"] == c])[0] or 0 for c in CRIT]
        hbar_pair(os.path.join(ch_dir, "agreement_by_criterion.png"), labels, v1, v2, "Первый прогон", "После исправления промпта",
                  "Совпадение ИИ с ручной выборкой", "% совпавших ответов", note="20 диалогов: 12 рабочих + 8 отложенных.")
    else:
        hbar(os.path.join(ch_dir, "agreement_by_criterion.png"), labels, v1, "Совпадение ИИ с ручной выборкой", "% совпавших ответов",
             note="20 диалогов: 12 рабочих + 8 отложенных.")

    print(f"Compared {agreement(rows)[2]} answers; agreement {pct(agreement(rows)[0])}; disagreements {len(err)}.")
    print("Wrote reports/ and charts/. Open manual_evaluation.xlsx to confirm the suggested Error type for each disagreement.")


if __name__ == "__main__":
    main()
