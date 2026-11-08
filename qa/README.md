# qa/ — работа аналитика

## Что уже сделано

| Шаг | Статус | Где |
|---|---|---|
| 1. Правила | готово | `rules.md` |
| 2. Ручная оценка вслепую, 20 диалогов | готово: 160 ответов с доказательствами и обоснованием | `manual_evaluation.xlsx`, лист **Manual scoring** (колонки Manual result, Evidence message #, Manual note, Sample); исходник — `manual_scores.py` |
| 3. Сравнение с ИИ | ждёт `results/dev/results.json` | `compare.py` |
| 4. Ошибки, топ-5 | ждёт результатов ИИ | `compare.py` → `reports/errors.md` |
| 5. Было / стало | ждёт перепрогона | `compare.py --rerun` → `reports/before_after.md` |
| 6. Аналитика по 65 | ждёт результатов ИИ; по ручной выборке — готово | `reports/manual_sample.md`, `charts/manual_*.png`; по ИИ — `reports/analytics.md`, `charts/criteria_fulfilment.png`, `charts/score_by_language_channel.png` |
| 7. Контроль | скрипт готов | `check_results.py` |
| 8. Для жюри | собирается автоматически | `reports/jury.md`, `charts/agreement_by_criterion.png` |

Ручную оценку делал не человек, а Claude по правилам чек-листа. Перед защитой аналитик должен **просмотреть все 160 ответов** (колонка Manual note объясняет каждый) и особенно спорные случаи из `rules.md`. Пока этого не сделано, называть выборку «экспертной» нельзя.

## Как запустить оставшиеся шаги

Нужен Python 3.10+ с `openpyxl` и `matplotlib` (`pip install openpyxl matplotlib`). Команды — из папки `qa/`.

**Шаги 3, 4, 6, 8** — после прогона DEV:

```
python compare.py --dataset ../backend/data/DEV.json --first ../results/dev/results.json
```

Скрипт впишет ответы ИИ в колонку **AI result** (по всем 65 диалогам), сам предложит **Error type** и **Recommendation** для каждого расхождения (только в пустые ячейки — ваши правки не затираются) и создаст отчёты и графики. Откройте `manual_evaluation.xlsx` и подтвердите или исправьте типы ошибок. Колонки Match и лист Summary пересчитаются при открытии в Excel.

**Шаг 5** — после исправления промпта и перепрогона 20 диалогов:

```
python compare.py --dataset ../backend/data/DEV.json --first ../results/dev/results.json --rerun ../results/dev/results_v2.json
```

Ответы перепрогона попадут в **AI result (re-run)**, отчёт — в `reports/before_after.md` отдельно по рабочим и отложенным, график — в `charts/agreement_by_criterion.png`.

**Шаг 7** — контрольный набор:

```
python check_results.py ../results/prod/results.json --expect 10 --dataset ../backend/data/<контрольный набор>.json
```

Проверяет число диалогов, все 8 критериев, допустимые ответы, N/A только где разрешено, номера доказательств и цитаты. Запускайте вместе с командой из задания (`export_results.py --check`).

**Аналитика ручной выборки** (уже запущена; перезапустить после правок ручных ответов):

```
python sample_profile.py --dataset ../backend/data/DEV.json
```

Если поменяли ответы в Excel, перенесите их в `manual_scores.py`, чтобы отчёты считались по тем же ответам.

## Формат результатов ИИ, который понимает скрипт

Список (или `{"results": [...]}`) объектов с `conversation_id` и `criteria`. У критерия: id (`c1`…`c8` или `contact`, `needs`, `identification`, `consultation`, `empathy`, `dialogue_management`, `closing`, `gross_errors`), ответ (`answer` или `status`: YES/NO/NA, PASS/FAIL, ступень шкалы) и доказательство (`evidence_index` или `evidence.message_index`, плюс `quote`).

## Важно

- Отложенные диалоги (p1-ru-006, p1-ru-015, p1-ru-024, p1-kz-003, p1-kz-010, p1-en-006, p1-en-010, p4-ru-005) ИИ-инженеру не показывать: в топ-5 они не попадают.
- Говорить «совпадение с нашей ручной выборкой», а не «точность».
- Ссылку API с ключом `sig=` никуда не пересылать.

## Файлы

- `manual_evaluation.xlsx` — рабочая таблица (листы How to use, Summary, Conversations, Messages, Checklist, Manual scoring).
- `manual_scores.py` — ручные ответы и спорные случаи (источник для отчётов).
- `compare.py`, `sample_profile.py`, `check_results.py` — скрипты шагов 3–8.
- `build_workbook.py` — пересборка пустой таблицы из DEV.json (ручные ответы потом заполняются заново).
- `rules.md`, `reports/`, `charts/`.
