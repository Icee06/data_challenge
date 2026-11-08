"""Step 6 on the manual sample: what the 20 hand-scored dialogues show about operator quality.
Runs now, without the AI results:  python sample_profile.py --dataset ../backend/data/DEV.json
Writes reports/manual_sample.md, charts/manual_criteria_fulfilment.png and charts/manual_score_by_language_channel.png."""
import argparse, os, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from compare import CRIT, NAME, TOP, GROUP, total, load_dataset, hbar, pct, HERE  # noqa: E402
from manual_scores import SCORES, JUDGEMENT_CALLS  # noqa: E402

ap = argparse.ArgumentParser(); ap.add_argument("--dataset", required=True); ap.add_argument("--out", default=HERE)
a = ap.parse_args()
data = load_dataset(a.dataset)
os.makedirs(os.path.join(a.out, "reports"), exist_ok=True); os.makedirs(os.path.join(a.out, "charts"), exist_ok=True)

ans = {cid: {c: v[0] for c, v in zip(CRIT, rows)} for cid, rows in SCORES.items()}
tot = {cid: total(v) for cid, v in ans.items()}
fulfil, na_rate, napp = {}, {}, {}
for c in CRIT[:-1]:
    got = [ans[cid][c] for cid in ans]
    app = [x for x in got if x != "NA"]; napp[c] = len(app)
    fulfil[c] = (sum(x / TOP[c] for x in app) if c in TOP else sum(x == "YES" for x in app)) / len(app) * 100 if app else None
    na_rate[c] = sum(x == "NA" for x in got) / len(got) * 100
gross = [cid for cid in ans if ans[cid]["c8"] == "YES"]
groups = defaultdict(list)
for cid, v in tot.items():
    r = data[cid]
    groups[f"{r['language']} {'звонок' if r['channel'] == 'voice call' else 'чат'}"].append(v)
weak = sorted([c for c in fulfil if fulfil[c] is not None], key=lambda c: fulfil[c])[:3]
vals = [v for v in tot.values() if v is not None]

md = ["# Ручная выборка: что видно без ИИ", "",
      "20 диалогов (12 рабочих + 8 отложенных), оценены вслепую до ответов ИИ. Это внутренняя экспертная выборка, не официальный Ground Truth.", "",
      f"- Средний балл: **{sum(vals) / len(vals):.2f}** из 100.",
      f"- Самые слабые критерии: " + ", ".join(f"{c} {NAME[c]} ({fulfil[c]:.0f}%)" for c in weak) + ".",
      f"- Грубые ошибки: **{len(gross)}**.", "",
      "## Выполнение и N/A по критериям", "", "| Критерий | Выполнение (без N/A) | Применим в диалогах | Частота N/A |", "|---|---:|---:|---:|"]
md += [f"| {c} {NAME[c]} | {pct(fulfil[c])} | {napp[c]} из 20 | {pct(na_rate[c])} |" for c in CRIT[:-1]]
md += ["", "c2 и c3 применимы лишь в 8 и 6 диалогах: один промах сильно меняет процент."]
md += ["", "## Язык и канал", "", "| Группа | Диалогов | Средний балл |", "|---|---:|---:|"]
md += [f"| {g} | {len(v)} | {sum(v) / len(v):.2f} |" for g, v in sorted(groups.items())]
md += ["", "## Баллы по диалогам", "", "| Диалог | Выборка | Балл |", "|---|---|---:|"]
md += [f"| {cid} | {GROUP[cid]} | {tot[cid]} |" for cid in sorted(tot, key=lambda k: (GROUP[k], k))]
md += ["", "## Наблюдения", "",
       "- Почти все операторы здороваются и представляются в чатах; в звонках приветствие или имя теряются (p4-ru-002, p4-ru-005).",
       "- Главная слабость — завершение: чаще всего оператор только прощается, без итога и без предложения помощи.",
       "- Вторая — второй вопрос клиента остаётся без ответа (p1-ru-003, p1-ru-007, p1-en-007): это сразу снижает консультацию до 10 и ведение диалога до 7 или 4.",
       "- Выявление потребности и идентификация проваливаются редко, но в важных местах: оператор игнорирует детали клиента (p1-ru-003, p4-ru-001) и проверяет счёт без подтверждения личности (p4-ru-005).",
       "- Звонки заметно слабее чатов: бессвязные реплики, переспросы, недовольство клиента без реакции (p4-ru-001).",
       "- Грубых ошибок в выборке нет.", "", "## Спорные правила, которые нужно согласовать с командой", ""]
md += [f"- **{c}.** {t}" for c, t in JUDGEMENT_CALLS]
open(os.path.join(a.out, "reports", "manual_sample.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")

order = CRIT[:-1]
hbar(os.path.join(a.out, "charts", "manual_criteria_fulfilment.png"), [f"{c} {NAME[c]}" for c in order], [fulfil[c] for c in order],
     "Выполнение критериев, ручная выборка", "% диалогов, где критерий выполнен (N/A не учитываются)", note="20 диалогов, оценены вручную до ответов ИИ.")
gl = sorted(groups)
hbar(os.path.join(a.out, "charts", "manual_score_by_language_channel.png"), [f"{g} ({len(groups[g])})" for g in gl],
     [sum(groups[g]) / len(groups[g]) for g in gl], "Средний балл по языку и каналу, ручная выборка", "Средний итоговый балл, 0–100",
     fmt="{:.1f}", note="В скобках — число диалогов.")
print("avg", round(sum(vals) / len(vals), 2), "weakest", weak, {g: round(sum(v) / len(v), 1) for g, v in groups.items()})
