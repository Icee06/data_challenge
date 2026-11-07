# Demo script: 5 minutes

Follows the timing in the student guide: 1 min interface, 2 min evaluation and evidence, 1 min appeal,
1 min checklist change. Lines to say are in Russian; adapt them, but keep the clicks.

**Roles**

| Who | Does |
|---|---|
| Presenter (Team Lead) | Speaks, keeps time |
| Driver (Frontend) | Clicks; never types long text live (comments and reasons are prepared) |
| Analyst | Answers questions about quality and the manual sample |
| AI Engineer | Answers questions about the model, prompt and cost |

## Fill in before the rehearsal

| Slot | What to pick | ID |
|---|---|---|
| A | A RU chat with a clear evaluation, at least one evidence link and a mid-range total | `p1-ru-___` |
| B | A dialogue with an N/A criterion and a sensible explanation | `p1-__-___` |
| C | A dialogue for the appeal: a criterion where the AI is arguably wrong, **no appeal on it yet** | `p1-__-___` |
| D | A voice call with audio | `p4-ru-___` |
| New criterion | Name, weight, description, ready to paste | e.g. «Соблюдение скрипта», 5, «Да — оператор следует скрипту приветствия и завершения.» |

Prepared texts:
- Appeal comment: «Оператор попрощался в последней реплике: "Хорошего вечера!"»
- Reviewer reason: «Прощание есть в последней реплике оператора, критерий выполнен.»

## 30 minutes before

- [ ] Server running from `backend/`: `uvicorn main:app --port 8000`; `http://localhost:8000/health` says `ok`
- [ ] DEV fully evaluated (the list shows scores, not "Not evaluated"); A, B, C, D open without errors
- [ ] Control set loaded and evaluated; `results/prod/results.json` pushed
- [ ] The checklist is at the version you want to start from (Edit checklist shows the 8 PDF criteria)
- [ ] An LLM key with quota left in `.env` (the checklist change in minute 4 makes new AI calls)
- [ ] Browser: one tab, zoom 110–125% so the jury can read, dark theme, notifications off, laptop on power
- [ ] Backup: screenshots of each step in a folder, in case the projector or network fails

## 0:00–1:00 · Interface and a conversation

Clicks: dataset field shows `DEV` → filter **KZ** → reset → open **A**.

> «Это CQA для Freedom Broker. Слева реестр из 65 диалогов DEV: язык, канал и итоговый балл, фильтры по языку,
> типу и статусу. Набор берётся из API организаторов по group_id и кешируется. В центре транскрипт: клиент слева,
> оператор справа, реплики бота и системы показаны как контекст. Для звонков здесь же плеер с записью.»

Optional, 10 seconds: open **D** and press play.

## 1:00–3:00 · Evaluation, evidence, N/A and the total

Clicks: on **A**, point at the total and the sections → click a green evidence block → the message lights up →
open **B** → point at the N/A criterion.

> «Справа оценка по восьми критериям чек-листа. ИИ отвечает только Да, Нет, N/A или ступень шкалы и обязан
> привести реплику-доказательство. Баллы, веса и итог считает не модель, а код по правилам из PDF.»
>
> (click evidence) «Нажимаем на обоснование, и транскрипт переходит к той самой реплике. Для пропущенного
> действия показываем проверенный участок разговора.»
>
> (on B) «Здесь критерий неприменим: модель объясняет почему, а вес критерия перераспределяется между
> остальными, так что максимум остаётся 100. Грубые ошибки отмечаются отдельным флагом и итог не обнуляют.»
>
> «Модель — Gemini Flash-Lite, один вызов на диалог. На нашей ручной выборке из N диалогов совпадение X%.»

## 3:00–4:00 · Appeal, reviewer decision, new total

Clicks: open **C** → **Dispute** on the criterion → paste the comment → **Submit appeal** → **Reviewer queue** →
the card shows the AI answer, the comment and the dialogue fragment → choose the new answer → paste the reason →
**Save decision** → close.

> «Менеджер не согласен с оценкой и подаёт апелляцию с комментарием. Проверяющий видит спорную оценку,
> комментарий и фрагмент диалога, меняет ответ и обязательно указывает причину.»
>
> (point at the score trail) «Итог пересчитан: оценка ИИ была X, после проверки Y. В карточке видно исходный
> ответ модели, решение и финальный результат.»

## 4:00–5:00 · Change a criterion and apply the checklist

Clicks: **Edit checklist** → **Add criterion** (or edit the one the jury asks for) → paste name, weight and
description → **Save and re-evaluate** → OK → talk over the progress bar → open a re-evaluated dialogue.

> «Чек-лист настраивается в интерфейсе: формулировка, вес, тип ответа, шкала и условие N/A. Сохранение создаёт
> новую версию, старые оценки остаются на своей версии. Промпт строится из чек-листа, поэтому новый критерий
> работает без изменения кода.»
>
> (when a dialogue is done) «Вот новый критерий в оценке, итог пересчитан с учётом его веса.»

Tip: before saving, load the **control set** (10 chats) or filter the list, so the re-evaluation is short.
It makes one AI call per conversation in the loaded dataset.

## If something breaks live

| Problem | Do this |
|---|---|
| AI call fails (quota, network) | Already evaluated dialogues still open from the database. Show those and say it was evaluated earlier |
| Organizer API down | Nothing to do: the dataset comes from the saved copy |
| Re-evaluation is slow | Keep talking; open a dialogue that already finished |
| Page shows an error | Refresh the page; the state is on the server |
| Projector or laptop fails | Use the screenshots |

## Likely questions

| Question | Answer |
|---|---|
| How accurate is it? | Agreement with our manual sample: X% overall, Y% on the held-out part. Teams do not get the official ground truth |
| Who calculates the score? | The backend, by the PDF rules: weights, N/A redistribution, scale steps, rounding to two decimals |
| What if the AI is wrong? | Every score has evidence; a reviewer can change it through an appeal, and the total is recalculated |
| What about Kazakh? | Show the KZ agreement next to RU and EN |
| What does it cost? | One LLM call per dialogue; free tier during the hackathon; cost per evaluation in `docs/cost.md` |
| Can it go into Dynamics 365? | The frontend is plain HTML/CSS/JS with a relative API base and no build step, so it can become an HTML web resource |
