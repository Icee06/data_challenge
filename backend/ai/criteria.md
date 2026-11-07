# Scoring rules for the official checklist (criteria.md)

Source: "Чек-лист качества чатов" (Freedom Broker hackathon). One checklist for RU, KZ and EN chats.
Criteria 01-07 are scored (weights sum to 100). Criterion 08 (gross errors) is a separate flag, no points.
The machine-readable version is `checklist.json`; the prompt is built from it.

| ID | Criterion | Type | Weight | N/A allowed |
|---|---|---|---|---|
| 01 | Установление контакта (contact) | Yes/No | 10 | no |
| 02 | Выявление потребности (needs) | Yes/No/N/A | 15 | yes |
| 03 | Идентификация (identification) | Yes/No/N/A | 20 | yes |
| 04 | Корректная и полная консультация | scale 0/5/10/15/20, N/A | 20 | yes |
| 05 | Эмпатия и клиентоориентированность | Yes/No | 15 | no |
| 06 | Управление диалогом, скорость и грамотность | scale 0/4/7/10 | 15 | no |
| 07 | Завершение (closing) | Yes/No/N/A | 5 | yes |
| 08 | Грубые ошибки | flag, no points | - | no |

## Rules
- Yes = full weight, No = 0. Scale: pick exactly one listed step; the TOP step gives full weight
  (06: step 10 of 10 gives 15 points, so weighted = step / 10 * 15).
- No = the action was needed and missed or done wrongly. N/A = criterion not applicable by its condition.
  Identification required but not confirmed in the chat = No, never N/A. A general tariff question needs no identification.
- 04 judges whether the request was solved correctly and completely; 06 judges how clearly and coherently the
  operator communicated. A clear answer to one of two key questions can score high on 06 and partial on 04.
- 08 is true if the OPERATOR insulted the client, offered a paid service bypassing the company (the offer alone
  is enough: transferring money to the operator personally, or asking for the personal number for a private paid
  consultation), or disclosed confidential information.
- Excluded from scoring: the 3-minute first-reaction threshold, tags and attachments.
- Evidence: cite the specific message or system message and say what it proves. For a missed action say where it
  was expected. For N/A say which condition holds.
  Example: "Завершение — Нет. После ответа на вопрос оператор закрыл чат без прощания. Основание — последняя реплика оператора и сообщение о закрытии чата."

## Division of labour
- The LLM returns status, scale step, quote and reason.
- Backend (scoring_engine.py) converts scale steps to points, removes N/A criteria, redistributes their weights
  proportionally so the maximum stays 100, rounds to 2 decimals, and returns no total if no weighted criterion applies.
  The validator also returns `weighted_score` (before N/A redistribution) as a convenience.
