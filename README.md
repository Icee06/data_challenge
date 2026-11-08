# Freedom Broker · Conversation Quality Analytics (CQA)

Prototype for the Freedom Broker CQA hackathon. It scores client–operator chats and calls against an editable
quality checklist with an LLM, shows the exact message behind every score, and lets a reviewer handle appeals.

- **Explainable:** every criterion has an answer, points, weight, a short reason and a link to the supporting
  message. A click scrolls the transcript to it; for calls, the player jumps to that moment.
- **The math is not done by the AI:** the model only returns answers and evidence. Points, N/A weight
  redistribution, scale steps, rounding and the gross-error flag are calculated by the backend from the checklist.
- **Human in the loop:** dispute a criterion, a reviewer confirms or changes it with a reason, the total is
  recalculated. The original AI answer, the decision and the final result are all kept.
- **Editable checklist:** add, remove or change criteria, weights, scales and N/A rules in the UI. Each save is a new
  version; old results stay on the version they were made with.
- **Datasets by `group_id`:** DEV and the control set are loaded from the organizers' API and cached separately.
- **RU, KZ and EN**, chats and voice calls.

## Quick start

Requirements: Python 3.11+ and an LLM key: Gemini (free tier from [AI Studio](https://aistudio.google.com)),
any OpenAI-compatible API such as NVIDIA NIM, or a local model through Ollama.

Windows (PowerShell):

```powershell
git clone <repository-url>
cd cqa-hackathon\backend
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env        # then fill it in, see below
uvicorn main:app --port 8000
```

macOS / Linux:

```bash
git clone <repository-url>
cd cqa-hackathon/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill it in, see below
uvicorn main:app --port 8000
```

Open **http://localhost:8000/** for the app and **http://localhost:8000/docs** for the API.
The backend serves the frontend, so there is nothing else to start.

### `.env`

| Key | What to put |
|---|---|
| `ORGANIZER_API_URL` | The full GET link for DEV from the organizers. It contains `sig=`, so it is a secret. `group_id` is swapped automatically. |
| `LLM_PROVIDER` | `gemini`, `anthropic`, or `openai` for any OpenAI-compatible API (NVIDIA, Groq, OpenRouter, Ollama) |
| `LLM_API_KEY` | Your API key |
| `LLM_MODEL` | e.g. `gemini-3.5-flash-lite`, or `qwen/qwen2.5-72b-instruct` on NVIDIA |
| `LLM_BASE_URL` | Only for `openai`: the chat-completions URL, e.g. `https://integrate.api.nvidia.com/v1/chat/completions` |
| `LLM_TIMEOUT` | Optional, seconds per call (default 90) |
| `TEAM_NAME` | Written into `results.json` |

`.env` is in `.gitignore`. Never commit it.

## Using the app

1. **Dataset:** `DEV` is loaded at start. For another set, type its `group_id` and press **Load**.
2. **Evaluate:** pick a conversation and press **Run evaluation**, or **Evaluate N pending** for the whole list.
   A conversation that is already evaluated comes back from the database without a new AI call.
3. **Evidence:** press the green evidence block under a criterion to jump to the message.
4. **Appeal:** press **Dispute** on a criterion and write a comment.
5. **Review:** open **Reviewer queue**, choose the decision, write the reason, save. The total is recalculated.
6. **Checklist:** **Edit checklist** → change criteria → **Save and re-evaluate**. Re-evaluating makes one AI call per
   conversation in the loaded dataset.

## Results for the organizers

`results/dev/results.json` (65 DEV dialogues) and `results/prod/results.json` (10 control chats) are produced by
`backend/export_results.py` in the format of the guide's appendix (JSON 1.0). Run it from `backend/`:

```bash
python export_results.py --set dev                                   # DEV -> ../results/dev/results.json
python export_results.py --set prod --group <control group_id> --evaluate-missing
python export_results.py --check ../results/prod/results.json --expect 10
```

- It uses the saved results under the active checklist (reviewer decisions included; `--ai-only` leaves them out).
- `--evaluate-missing` first evaluates conversations that have no result yet.
- A conversation that could not be evaluated is written as `"status": "error"` with the reason, never as a zero.
- Before writing, the file is checked against the appendix rules (record count, unique ids, all 8 criteria, value
  types, `evidence_turns` counted from 1, `final_score`, `critical_flag`). Problems are printed and the exit code is 1.

Then commit and **push** both files before the stop-code.

## Tests

```bash
cd backend
pytest -q
```

The tests use fake models and never call a real API: scoring math against hand-calculated values, every endpoint,
appeals, checklist versions, the AI prompt and validator, and `test_integration.py`, which runs the real `ai/`
package on the backend checklist (only the LLM call is faked).

## How it is built

```
cqa-hackathon/
├─ backend/                FastAPI app (run everything from here)
│  ├─ main.py              routes, dataset proxy and cache, serves the frontend
│  ├─ scoring_engine.py    all the math: weights, N/A, scales, rounding, gross errors, overrides
│  ├─ evaluation_service.py  dialogue -> AI answers -> validated -> stored -> scored
│  ├─ appeal_service.py    appeals and reviewer decisions
│  ├─ checklist_service.py checklist versions
│  ├─ dialogue_service.py  message numbering, audio offsets, quote lookup
│  ├─ db.py                SQLite: checklists, evaluations, appeals
│  ├─ schemas.py           request models and the AI answer contract
│  ├─ export_results.py    results.json for the organizers
│  └─ ai/                  prompt, LLM client, validator, language detection
├─ frontend/               plain HTML, CSS and JavaScript, no build step
├─ results/dev|prod/       results.json for the organizers
├─ qa/                     manual evaluation sample, comparison, reports
└─ docs/                   architecture, cost, demo script
```

Details: [docs/architecture.md](docs/architecture.md). API reference: [backend/API.md](backend/API.md) and `/docs`.

## Model and cost

The model is set in `.env`: `gemini-3.5-flash-lite` on the Gemini free tier by default, or for example
`qwen/qwen2.5-72b-instruct` on NVIDIA NIM. One evaluation is one LLM call for the
whole checklist, plus a retry only when the answer fails validation. Token counts are available at `/api/usage`;
the cost estimate is in [docs/cost.md](docs/cost.md).

## Known limits

- The LLM can be wrong. Every score shows its evidence, and a reviewer can change it.
- Free API tiers have daily limits. Results are cached, so each conversation is evaluated once per checklist version.
- For calls, the audio position assumes the recording starts at the first message.
- Quality was checked against our own manual sample (`qa/`), not against the organizers' ground truth.
- No sign-in, as allowed by the task: submitting an appeal and reviewing it are separate actions in the UI.
- The frontend has no external dependencies and uses a relative API base, so it can be moved into a
  Dynamics 365 HTML web resource.
