# CQA backend: API contract

Base URL `http://localhost:8000`. Interactive docs: `/docs`. All bodies are JSON. Errors look like
`{"detail": "...", "problems": ["..."]}`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | server check |
| GET | `/api/dataset/{group_id}?refresh=` | load a dataset (DEV, control) from the organizers, cached on disk |
| GET | `/api/dialogues?group_id&language&channel&status&q` | registry. `status` = evaluated / pending. Each row has `evaluation` (total, critical_error, appeals_open) or null |
| GET | `/api/dialogues/{id}?group_id` | full dialogue. Each message has `index` and `offset_sec` (audio seek). Original `timestamp` untouched |
| POST | `/api/evaluate/{id}?group_id&force_refresh&checklist_version` | evaluate one dialogue (cached unless `force_refresh`) |
| POST | `/api/evaluate/batch` | `{group_id, conversation_ids?, force_refresh?, checklist_version?, concurrency_limit?}`. Returns per-dialogue summaries and `errors` |
| GET | `/api/evaluation/{id}?group_id&checklist_version` | latest evaluation card (404 if not evaluated) |
| GET | `/api/evaluations?group_id` / `/api/evaluations/{evaluation_id}` | list / one card by id |
| POST | `/api/appeal` | `{evaluation_id, criterion_id, comment, author?}` |
| GET | `/api/appeals?status=open&group_id` | reviewer queue (comment, disputed criterion, transcript `fragment`) |
| GET | `/api/appeals/{evaluation_id}` | appeals of one evaluation |
| POST | `/api/appeal/{appeal_id}/decision` | `{decision: "confirm"\|"change", reason, answer?, reviewer?}`. Returns `{appeal, evaluation}` with the new total |
| GET | `/api/checklist` | active checklist `{version, checklist, warnings}` |
| PUT | `/api/checklist` | save a NEW version (becomes active). Body = checklist + optional `change_note`. Then call `/api/evaluate/batch` to apply it |
| GET | `/api/checklist/versions`, `/versions/{n}` | version list / one version |
| POST | `/api/score` | stateless math: `{checklist?, answers, overrides?}` |
| GET | `/api/usage` | LLM token counters (for docs/cost.md) |

## Evaluation card (`GET /api/evaluation/{id}`)
```json
{
  "evaluation_id": 12, "conversation_id": "p1-ru-001", "group_id": "DEV", "checklist_version": 1,
  "total": 85.38, "original_total": 77.69, "total_delta": 7.69, "changed": ["closing"],
  "critical_error": false, "cached": true, "summary": "...", "validation_flags": [],
  "criteria": [{
    "id": "closing", "name": "Завершение", "type": "yes_no", "weight": 5,
    "answer": "yes", "original_answer": "no", "source": "reviewer", "reviewer": "QA-1",
    "applicable": true, "points": 7.69, "effective_weight": 7.69, "explanation": "...",
    "evidence": [{"message_index": 2, "timestamp": "00:20", "speaker": "operator", "quote": "..."}],
    "checked_range": "весь диалог (00:02–00:20)",
    "appeal": {"id": 3, "status": "changed", "comment": "...", "decision_reason": "..."}
  }],
  "sections": [{"id": "start", "name": "...", "points": 16.67, "max_points": 16.67, "percent": 100.0}]
}
```
* `answer`: `"yes"`, `"no"`, `"na"` or a scale step (number). `applicable: false` means N/A: `explanation` says why and
  `checked_range` says which part was checked.
* Gross errors (`type: "flag"`): `violation: true` means a violation WAS found. No points, only `critical_error`.
* `total` is final (after reviewer decisions), `original_total` is the AI-only result. Neither is ever calculated by the LLM.
* Click on evidence: scroll to `transcript[message_index]`. For calls, seek the player to that message's `offset_sec`.
