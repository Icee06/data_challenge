"""CQA backend: dataset proxy + cache.

Evaluation endpoints (scoring_engine.py / llm_client.py) plug in below the
"Dialogues" section and can reuse `ensure_loaded()` and `get_record()`.

Run from the backend/ folder:
    uvicorn main:app --reload --port 8000
.env (backend/.env or repo root):
    ORGANIZER_API_URL=<the GET link from the task page, with or without ?group_id=DEV>
"""
import json
import os
import re
from pathlib import Path
from typing import List, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import scoring_engine as se

load_dotenv()

ORGANIZER_API_URL = os.getenv("ORGANIZER_API_URL", "").strip()
CACHE_DIR = Path(__file__).resolve().parent / "data"
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend_ZAKO"
GROUP_ID_RE = re.compile(r"^[\w-]{1,64}$")

app = FastAPI(title="CQA Backend", version="1.1.0")

# No auth/cookies in this project, so credentials stay off ("*" + credentials is not valid CORS).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# group_id -> {conversation_id -> record}. One bucket per group, so loading the
# control set at hour 23 never overwrites DEV.
_store: dict[str, dict[str, dict]] = {}


# --------------------------------------------------------------------------- #
# Organizer API + cache
# --------------------------------------------------------------------------- #
def check_group_id(group_id: str) -> str:
    if not GROUP_ID_RE.match(group_id):
        raise HTTPException(422, "group_id may contain only letters, digits, '_' and '-'.")
    return group_id


async def fetch_from_organizer(group_id: str) -> list[dict]:
    if not ORGANIZER_API_URL:
        raise HTTPException(500, "ORGANIZER_API_URL is not set (see .env).")

    # Works whether the URL in .env already has ?group_id=DEV or not.
    url = httpx.URL(ORGANIZER_API_URL).copy_merge_params({"group_id": group_id})

    # Only transport errors are caught here. Our own HTTPExceptions are raised
    # after the try-block so they are not swallowed and re-wrapped as 500.
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            payload = resp.json()
    except httpx.TimeoutException:
        raise HTTPException(504, "Organizer API timed out.")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            502, f"Organizer API returned HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        )
    except (httpx.HTTPError, ValueError) as exc:  # ValueError = body is not JSON
        raise HTTPException(502, f"Organizer API request failed: {exc}")

    if not isinstance(payload, dict) or not payload.get("isSuccess"):
        err = payload.get("error") if isinstance(payload, dict) else "unexpected response"
        raise HTTPException(502, f"Organizer API error: {err or 'unknown'}")

    records = (payload.get("data") or {}).get("records")
    if not isinstance(records, list):
        raise HTTPException(502, "Organizer API response has no data.records list.")
    return records


def _cache_file(group_id: str) -> Path:
    return CACHE_DIR / f"{group_id}.json"


def _save_disk_cache(group_id: str, records: list[dict]) -> None:
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        _cache_file(group_id).write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # cache is best-effort


def _load_disk_cache(group_id: str) -> Optional[list[dict]]:
    try:
        return json.loads(_cache_file(group_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


async def ensure_loaded(group_id: str, refresh: bool = False) -> str:
    """Make sure `group_id` is in memory. Returns where the data came from:
    'memory' | 'organizer' | 'disk_cache'.

    Lazy: after a server restart the first request reloads the data, so the
    demo never needs a manual "fetch" step. If the organizer API is down, the
    last saved copy on disk is used.
    """
    check_group_id(group_id)
    if group_id in _store and not refresh:
        return "memory"

    try:
        records = await fetch_from_organizer(group_id)
        source = "organizer"
        _save_disk_cache(group_id, records)
    except HTTPException:
        if group_id in _store:  # failed refresh: keep what we have
            return "memory"
        records = _load_disk_cache(group_id)
        if records is None:
            raise
        source = "disk_cache"

    # Records are stored untouched: original timestamps, speaker labels, audio_url.
    _store[group_id] = {r["conversation_id"]: r for r in records if r.get("conversation_id")}
    return source


def get_record(group_id: str, conversation_id: str) -> dict:
    """Sync helper for evaluation code. Call `await ensure_loaded(group_id)` first."""
    record = _store.get(group_id, {}).get(conversation_id)
    if record is None:
        raise HTTPException(404, f"Dialogue '{conversation_id}' not found in group '{group_id}'.")
    return record


def _summary(r: dict) -> dict:
    return {
        "conversation_id": r["conversation_id"],
        "channel": r.get("channel"),  # "agent chat" | "voice call"
        "language": r.get("language"),  # "RU" | "KZ" | "EN"
        "message_count": len(r.get("transcript") or []),
        "has_audio": bool(r.get("audio_url")),
    }


# --------------------------------------------------------------------------- #
# Dataset
# --------------------------------------------------------------------------- #
@app.get("/api/health")
async def health():
    return {"status": "ok", "organizer_url_configured": bool(ORGANIZER_API_URL), "loaded_groups": list(_store)}


@app.get("/api/dataset/{group_id}")
async def load_dataset(group_id: str, refresh: bool = False):
    """Load (or reload with ?refresh=true) a dataset group, e.g. DEV or the control group_id."""
    source = await ensure_loaded(group_id, refresh=refresh)
    records = _store[group_id].values()
    by_language: dict[str, int] = {}
    by_channel: dict[str, int] = {}
    for r in records:
        by_language[r.get("language")] = by_language.get(r.get("language"), 0) + 1
        by_channel[r.get("channel")] = by_channel.get(r.get("channel"), 0) + 1
    return {
        "group_id": group_id,
        "source": source,
        "total_records": len(_store[group_id]),
        "by_language": by_language,
        "by_channel": by_channel,
    }


# --------------------------------------------------------------------------- #
# Dialogues
# --------------------------------------------------------------------------- #
@app.get("/api/dialogues")
async def list_dialogues(
    group_id: str = "DEV",
    language: Optional[str] = None,  # RU | KZ | EN (case-insensitive)
    channel: Optional[str] = None,  # "chat" | "call" (or the full "agent chat" / "voice call")
):
    """Registry list: light summaries only. Full transcript comes from the detail endpoint."""
    await ensure_loaded(group_id)
    items = list(_store[group_id].values())
    if language:
        items = [r for r in items if (r.get("language") or "").upper() == language.upper()]
    if channel:
        items = [r for r in items if channel.lower() in (r.get("channel") or "").lower()]
    return {"group_id": group_id, "total": len(items), "dialogues": [_summary(r) for r in items]}


@app.get("/api/dialogues/{conversation_id}")
async def get_dialogue(conversation_id: str, group_id: str = "DEV"):
    """Full record: transcript, channel, language, audio_url."""
    await ensure_loaded(group_id)
    return get_record(group_id, conversation_id)


# --------------------------------------------------------------------------- #
# Scoring (pure math: no LLM involved). Used by the UI for checklist defaults
# and for instant recalculation after an appeal decision.
# --------------------------------------------------------------------------- #
class ScoreRequest(BaseModel):
    checklist: Optional[se.Checklist] = None  # omit -> default checklist
    answers: List[se.Answer]
    overrides: List[se.Override] = []  # reviewer decisions


@app.get("/api/checklist/default")
async def get_default_checklist():
    return se.default_checklist()


@app.post("/api/score")
async def score_answers(req: ScoreRequest):
    """Returns {original, final, total_delta, changed}. Without overrides, final == original."""
    try:
        return se.score_with_overrides(req.checklist or se.default_checklist(), req.answers, req.overrides)
    except se.ScoringError as exc:
        raise HTTPException(422, {"problems": exc.problems})


# --------------------------------------------------------------------------- #
# Frontend (optional): serve ../frontend_ZAKO from the same origin, so the demo
# runs with a single command and needs no CORS at all. Keep this block LAST.
# --------------------------------------------------------------------------- #
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
