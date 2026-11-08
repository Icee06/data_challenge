"""CQA Backend API: dataset proxy, AI evaluation, scoring, appeals, checklist versions.

Запуск из папки backend/:
    uvicorn main:app --reload --port 8000

Переменные окружения (.env), см. .env.example:
    ORGANIZER_API_URL   полный URL GET организаторов (содержит sig=, это секрет, не коммитить)
    LLM_PROVIDER, LLM_API_KEY, LLM_MODEL   читаются пакетом ai/
    FRONTEND_DIR        необязательно: путь к папке фронтенда
"""
import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, List, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import appeal_service
import checklist_service
import dialogue_service
import evaluation_service
import scoring_engine as se
from schemas import (AppealCreate, AppealDecision, BatchEvaluateRequest, ChecklistSave, ServiceError)

load_dotenv()

ORGANIZER_API_URL = os.getenv("ORGANIZER_API_URL", "").strip()
CACHE_DIR = Path(__file__).resolve().parent / "data"
GROUP_ID_RE = re.compile(r"^[\w-]{1,64}$")


def _find_frontend() -> Path:
    if os.getenv("FRONTEND_DIR"):
        return Path(os.environ["FRONTEND_DIR"])
    root = Path(__file__).resolve().parent.parent
    for name in ("frontend", "frontend_ZAKO"):
        if (root / name).is_dir():
            return root / name
    return root / "frontend"


FRONTEND_DIR = _find_frontend()


@asynccontextmanager
async def lifespan(_: FastAPI):
    checklist_service.ensure_seeded()
    yield


app = FastAPI(
    title="CQA Backend & Analytics Engine",
    description="Freedom Broker Conversation Quality Analytics API",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ServiceError)
async def service_error_handler(_: Request, exc: ServiceError):
    body: dict = {"detail": exc.message}
    if exc.problems:
        body["problems"] = exc.problems
    return JSONResponse(status_code=exc.status_code, content=body)


# group_id -> {conversation_id -> record}. Хранение в изолированных бакетах памяти.
_store: dict[str, dict[str, dict]] = {}


# --------------------------------------------------------------------------- #
# Organizer API & disk cache
# --------------------------------------------------------------------------- #
def check_group_id(group_id: str) -> str:
    if not GROUP_ID_RE.match(group_id):
        raise HTTPException(422, "group_id may contain only letters, digits, '_' and '-'.")
    return group_id


async def fetch_from_organizer(group_id: str) -> list[dict]:
    if not ORGANIZER_API_URL:
        raise HTTPException(500, "ORGANIZER_API_URL is not set (check .env file).")

    url = httpx.URL(ORGANIZER_API_URL).copy_merge_params({"group_id": group_id})

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
    except (httpx.HTTPError, ValueError) as exc:
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
        pass


def _load_disk_cache(group_id: str) -> Optional[list[dict]]:
    """Reads data/<group_id>.json in any shape we may find there: the records list this server writes,
    or the raw organizer response saved with curl ({"isSuccess", "data": {"records": [...]}}).
    PowerShell's '>' writes UTF-16, so that encoding is accepted too."""
    try:
        raw = _cache_file(group_id).read_bytes()
        text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig")
        data = json.loads(text)
    except (OSError, ValueError):
        return None
    if isinstance(data, dict):
        inner = data.get("data") if isinstance(data.get("data"), dict) else data
        data = inner.get("records")
    if not isinstance(data, list):
        return None
    return [r for r in data if isinstance(r, dict)]


async def ensure_loaded(group_id: str, refresh: bool = False) -> str:
    """Гарантирует загрузку группы диалогов в память (memory | organizer | disk_cache)."""
    check_group_id(group_id)
    if group_id in _store and not refresh:
        return "memory"
    if not refresh:  # after a restart, use the saved copy instead of waiting on the organizer API
        cached = _load_disk_cache(group_id)
        if cached is not None:
            _store[group_id] = {r["conversation_id"]: r for r in cached if r.get("conversation_id")}
            return "disk_cache"

    try:
        records = await fetch_from_organizer(group_id)
        source = "organizer"
        _save_disk_cache(group_id, records)
    except HTTPException:
        if group_id in _store:
            return "memory"
        records = _load_disk_cache(group_id)
        if records is None:
            raise
        source = "disk_cache"

    _store[group_id] = {r["conversation_id"]: r for r in records if r.get("conversation_id")}
    return source


def get_record(group_id: str, conversation_id: str) -> dict:
    record = _store.get(group_id, {}).get(conversation_id)
    if record is None:
        raise HTTPException(404, f"Dialogue '{conversation_id}' not found in group '{group_id}'.")
    return record


def _title(r: dict) -> str:
    """The client's first real message, shown in the registry list (skips IVR digits like "2")."""
    for m in r.get("transcript") or []:
        text = (m.get("text") or "").strip()
        if m.get("speaker") == "client" and text and not re.fullmatch(r"[\d#*\s.]+", text):
            return text[:60] + ("…" if len(text) > 60 else "")
    return ""


def _summary(r: dict) -> dict:
    return {
        "conversation_id": r["conversation_id"],
        "title": _title(r),
        "channel": r.get("channel"),
        "type": dialogue_service.channel_type(r),
        "language": r.get("language"),
        "message_count": len(r.get("transcript") or []),
        "has_audio": bool(r.get("audio_url")),
    }


# --------------------------------------------------------------------------- #
# Health, dataset, dialogues
# --------------------------------------------------------------------------- #
@app.get("/health")
@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "organizer_url_configured": bool(ORGANIZER_API_URL),
        "loaded_groups": list(_store.keys()),
        "llm_provider": os.getenv("LLM_PROVIDER"),
        "llm_model": os.getenv("LLM_MODEL"),
    }


@app.get("/api/dataset/{group_id}")
async def load_dataset(group_id: str, refresh: bool = False):
    """Загрузка или принудительное обновление датасета (напр. DEV или контрольная группа)."""
    source = await ensure_loaded(group_id, refresh=refresh)
    by_language: dict = {}
    by_channel: dict = {}
    for r in _store[group_id].values():
        by_language[r.get("language")] = by_language.get(r.get("language"), 0) + 1
        by_channel[r.get("channel")] = by_channel.get(r.get("channel"), 0) + 1
    return {
        "group_id": group_id,
        "source": source,
        "total_records": len(_store[group_id]),
        "by_language": by_language,
        "by_channel": by_channel,
    }


@app.get("/api/dialogues")
async def list_dialogues(
    group_id: str = "DEV",
    language: Optional[str] = None,
    channel: Optional[str] = None,
    status: Optional[str] = None,  # evaluated | pending
    q: Optional[str] = None,  # substring of the conversation id
    checklist_version: Optional[int] = None,
):
    """Реестр диалогов + статус оценки по активному (или выбранному) чек-листу."""
    await ensure_loaded(group_id)
    items = list(_store[group_id].values())
    if language:
        items = [r for r in items if (r.get("language") or "").upper() == language.upper()]
    if channel:
        items = [r for r in items if channel.lower() in (r.get("channel") or "").lower()]
    if q:
        items = [r for r in items if q.lower() in r["conversation_id"].lower()]
    done = evaluation_service.summaries(group_id, checklist_version)
    rows = []
    for r in items:
        ev = done.get(r["conversation_id"])
        rows.append({**_summary(r), "evaluated": ev is not None, "evaluation": ev})
    if status == "evaluated":
        rows = [x for x in rows if x["evaluated"]]
    elif status == "pending":
        rows = [x for x in rows if not x["evaluated"]]
    return {"group_id": group_id, "total": len(rows), "evaluated_total": sum(x["evaluated"] for x in rows),
            "dialogues": rows}


@app.get("/api/dialogues/{conversation_id}")
@app.get("/api/dialogue/{conversation_id}")
async def get_dialogue(conversation_id: str, group_id: str = "DEV"):
    """Полная карточка: транскрипт (с index и offset_sec для плеера), аудио, язык."""
    await ensure_loaded(group_id)
    return dialogue_service.normalize_record(get_record(group_id, conversation_id))


# --------------------------------------------------------------------------- #
# Pure math scoring
# --------------------------------------------------------------------------- #
class ScoreRequest(BaseModel):
    checklist: Optional[se.Checklist] = None
    answers: List[se.Answer]
    overrides: List[se.Override] = []


@app.get("/api/checklist/default")
async def get_default_checklist():
    return se.default_checklist()


@app.post("/api/score")
async def score_answers(req: ScoreRequest):
    """Быстрый математический расчёт/перерасчёт баллов с учётом апелляций."""
    try:
        return se.score_with_overrides(req.checklist or se.default_checklist(), req.answers, req.overrides)
    except se.ScoringError as exc:
        raise HTTPException(422, {"problems": exc.problems})


# --------------------------------------------------------------------------- #
# Checklist (versioned)
# --------------------------------------------------------------------------- #
@app.get("/api/checklist")
async def get_checklist():
    version, cl = checklist_service.get_active()
    return {"version": version, "checklist": cl, "warnings": checklist_service.warnings_for(cl)}


@app.get("/api/checklist/versions")
async def checklist_versions():
    return {"versions": checklist_service.list_versions()}


@app.get("/api/checklist/versions/{version}")
async def get_checklist_version(version: int):
    return {"version": version, "checklist": checklist_service.get(version)}


@app.put("/api/checklist")
async def put_checklist(body: ChecklistSave):
    """Сохраняет НОВУЮ версию и делает её активной. Старые оценки остаются на своих версиях.
    Чтобы применить к диалогам: POST /api/evaluate/batch (кэш для новой версии пуст)."""
    cl = se.Checklist.model_validate(body.model_dump(exclude={"change_note"}))
    return checklist_service.save_new_version(cl, note=body.change_note)


# --------------------------------------------------------------------------- #
# AI evaluation. NOTE: /batch MUST be declared before /{conversation_id}, otherwise
# FastAPI matches "batch" as a conversation id.
# --------------------------------------------------------------------------- #
@app.post("/api/evaluate/batch")
async def evaluate_batch(req: BatchEvaluateRequest):
    """Пакетная оценка с ограничением параллельности. Возвращает краткую сводку по каждому диалогу;
    полную карточку отдаёт GET /api/evaluation/{conversation_id}."""
    await ensure_loaded(req.group_id)
    ids = req.conversation_ids or list(_store.get(req.group_id, {}).keys())
    if not ids:
        raise HTTPException(404, f"No dialogues found for group '{req.group_id}'.")
    version, _ = checklist_service.resolve(req.checklist_version)

    semaphore = asyncio.Semaphore(max(1, min(req.concurrency_limit, 10)))
    results: list = []
    errors: list = []

    async def worker(cid: str):
        async with semaphore:
            try:
                view = await evaluation_service.evaluate(
                    req.group_id, get_record(req.group_id, cid), version, req.force_refresh)
                results.append({
                    "conversation_id": cid, "evaluation_id": view["evaluation_id"], "total": view["total"],
                    "critical_error": view["critical_error"], "cached": view["cached"],
                })
            except ServiceError as exc:
                errors.append({"conversation_id": cid, "error": exc.message, "problems": exc.problems})
            except HTTPException as exc:
                errors.append({"conversation_id": cid, "error": str(exc.detail)})
            except Exception as exc:  # one bad dialogue must not kill the batch
                errors.append({"conversation_id": cid, "error": f"{type(exc).__name__}: {exc}"})

    await asyncio.gather(*(worker(cid) for cid in ids))

    totals = [r["total"] for r in results if r["total"] is not None]
    return {
        "group_id": req.group_id,
        "checklist_version": version,
        "total_requested": len(ids),
        "successful": len(results),
        "cached_retrieved": sum(1 for r in results if r["cached"]),
        "newly_evaluated": sum(1 for r in results if not r["cached"]),
        "critical_errors_found": sum(1 for r in results if r["critical_error"]),
        "average_score": round(sum(totals) / len(totals), 2) if totals else None,
        "failed_count": len(errors),
        "errors": errors,
        "results": sorted(results, key=lambda r: r["conversation_id"]),
    }


@app.post("/api/evaluate/{conversation_id}")
async def evaluate_single(conversation_id: str, group_id: str = "DEV", force_refresh: bool = False,
                          checklist_version: Optional[int] = None):
    """Оценка одного диалога. Повторный вызов отдаёт сохранённый результат (cached=true)."""
    await ensure_loaded(group_id)
    return await evaluation_service.evaluate(group_id, get_record(group_id, conversation_id),
                                              checklist_version, force_refresh)


@app.get("/api/evaluations")
async def list_evaluations(group_id: str = "DEV", checklist_version: Optional[int] = None):
    done = evaluation_service.summaries(group_id, checklist_version)
    return {"group_id": group_id, "total": len(done),
            "evaluations": [{"conversation_id": cid, **v} for cid, v in sorted(done.items())]}


@app.get("/api/evaluations/{evaluation_id}")
async def get_evaluation_by_id(evaluation_id: int):
    return evaluation_service.get_view_by_id(evaluation_id)


@app.get("/api/evaluation/{conversation_id}")
async def get_evaluation(conversation_id: str, group_id: str = "DEV", checklist_version: Optional[int] = None):
    """Последняя оценка диалога по активному (или выбранному) чек-листу. 404, если ещё не оценён."""
    return evaluation_service.get_latest_view(group_id, conversation_id, checklist_version)


# --------------------------------------------------------------------------- #
# Appeals
# --------------------------------------------------------------------------- #
async def _with_fragment(card: dict) -> dict:
    """Add the transcript fragment around the evidence, so the reviewer sees the dispute in context."""
    card = dict(card)
    card["fragment"] = None
    try:
        await ensure_loaded(card["group_id"])
        record = dialogue_service.normalize_record(get_record(card["group_id"], card["conversation_id"]))
    except HTTPException:
        return card
    idxs = [e["message_index"] for e in card["criterion"].get("evidence") or [] if e.get("message_index") is not None]
    if idxs:
        card["fragment"] = dialogue_service.fragment(record["transcript"], idxs[0])
    elif record["transcript"]:  # no evidence (skipped action, N/A): show the checked dialogue
        card["fragment"] = [{**m, "is_evidence": False} for m in record["transcript"]]
    return card


@app.post("/api/appeal")
async def create_appeal(body: AppealCreate):
    card = appeal_service.create(body.evaluation_id, body.criterion_id, body.comment, body.author)
    return await _with_fragment(card)


@app.get("/api/appeals")
async def appeals_queue(status: Optional[str] = "open", group_id: Optional[str] = None):
    """Очередь проверяющего: спорные оценки, комментарий и фрагмент диалога."""
    cards = appeal_service.list_cards(status=None if status in (None, "", "all") else status, group_id=group_id)
    return {"total": len(cards), "appeals": [await _with_fragment(c) for c in cards]}


@app.get("/api/appeals/{evaluation_id}")
async def appeals_of_evaluation(evaluation_id: int):
    cards = appeal_service.list_cards(evaluation_id=evaluation_id)
    return {"evaluation_id": evaluation_id, "appeals": [await _with_fragment(c) for c in cards]}


@app.post("/api/appeal/{appeal_id}/decision")
async def decide_appeal(appeal_id: int, body: AppealDecision):
    """Проверяющий подтверждает или меняет оценку (нужна причина). Итог пересчитывается."""
    result = appeal_service.decide(appeal_id, body.decision, body.reason, body.answer, body.reviewer)
    result["appeal"] = await _with_fragment(result["appeal"])
    return result


# --------------------------------------------------------------------------- #
# LLM usage (for docs/cost.md)
# --------------------------------------------------------------------------- #
@app.get("/api/usage")
async def llm_usage():
    try:
        from ai import llm_client
        usage: Any = dict(llm_client.USAGE)
    except (ImportError, AttributeError):
        usage = None
    return {"usage": usage, "provider": os.getenv("LLM_PROVIDER"), "model": os.getenv("LLM_MODEL")}


# --------------------------------------------------------------------------- #
# Frontend (must stay last: it mounts at "/")
# --------------------------------------------------------------------------- #
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
