"""One dialogue -> AI answers -> validated -> stored -> scored.

The AI only returns ANSWERS. All numbers come from scoring_engine, and totals are never stored:
they are recomputed on every read from the stored answers plus reviewer decisions.

AI SEAM (the only place that knows the AI package's output shape):
  checklist_for_ai()       what we hand to ai.evaluator.evaluate_dialogue(dialogue, checklist)
  ai_result_to_answers()   AI result (PASS/FAIL/NA, ids "01".."07") -> scoring_engine answers
Everything else in the backend talks scoring_engine.Answer only.
"""
import asyncio
import logging
import os
from typing import Any, Callable, Dict, List, Optional

import checklist_service
import db
import dialogue_service as ds
import scoring_engine as se
from schemas import ServiceError

log = logging.getLogger("cqa.evaluation")

Evaluator = Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]
_evaluator: Optional[Evaluator] = None  # tests inject a fake here


def set_evaluator(fn: Optional[Evaluator]) -> None:
    global _evaluator
    _evaluator = fn


def _default_evaluator() -> Evaluator:
    try:
        from ai.evaluator import evaluate_dialogue
    except ImportError as exc:
        raise ServiceError(503, f"AI module is not available (backend/ai/evaluator.py): {exc}")
    return evaluate_dialogue


def checklist_for_ai(cl: se.Checklist) -> Dict[str, Any]:
    """Checklist object -> dict for the AI package. The full model dump carries id, name, type,
    weight, allow_na, scale, description and na_condition, so the prompt can be built from it."""
    return cl.model_dump()


# ------------------------------------------------------------- AI -> answers
_PASS = {"PASS", "YES", "Y", "TRUE", "ДА"}
_FAIL = {"FAIL", "NO", "N", "FALSE", "НЕТ"}
_NA = {"NA", "N/A", "Н/А"}


def _resolve_id(raw: Any, cl: se.Checklist) -> Optional[str]:
    """Exact id first; otherwise a numeric id "01".."08" means the Nth criterion of the checklist."""
    ids = [c.id for c in cl.criteria]
    raw = str(raw).strip()
    if raw in ids:
        return raw
    if raw.isdigit() and 1 <= int(raw) <= len(ids):
        return ids[int(raw) - 1]
    return None


def _evidence(raw: Any, transcript: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items = [raw] if isinstance(raw, dict) else list(raw or [])
    out = []
    for e in items:
        if not isinstance(e, dict) or not str(e.get("quote") or "").strip():
            continue
        idx = e.get("message_index")
        if not (isinstance(idx, int) and 0 <= idx < len(transcript)):
            idx = ds.locate_quote(e["quote"], transcript, e.get("timestamp"), e.get("speaker"))
        msg = transcript[idx] if idx is not None else {}
        out.append({"message_index": idx, "quote": e["quote"],
                    "timestamp": e.get("timestamp") or msg.get("timestamp"),
                    "speaker": e.get("speaker") or msg.get("speaker")})
    return out


def ai_result_to_answers(ai: Dict[str, Any], cl: se.Checklist, record: Dict[str, Any]) -> List[Dict[str, Any]]:
    transcript = record.get("transcript") or []
    by_id: Dict[str, Dict[str, Any]] = {}
    for item in ai.get("criteria") or []:
        rid = _resolve_id(item.get("criterion_id"), cl)
        if rid and rid not in by_id:
            by_id[rid] = item

    answers, problems = [], []
    whole = ds.whole_range(transcript)
    for c in cl.criteria:
        if c.type == "flag":  # gross errors: polarity is inverted (yes = a violation was found)
            violation = bool(ai.get("critical_error"))
            ev = _evidence(ai.get("critical_error_evidence"), transcript)
            if violation:
                expl = "Выявлено грубое нарушение. Подтверждающие реплики приведены в доказательствах."
            else:
                expl = "Оскорблений, предложений в обход компании и раскрытия конфиденциальной информации не найдено."
            answers.append({"criterion_id": c.id, "answer": "yes" if violation else "no", "explanation": expl,
                            "evidence": ev, "checked_range": None if ev else whole})
            continue

        item = by_id.get(c.id)
        if item is None:
            problems.append(f"AI answer has no result for criterion '{c.id}'")
            continue
        status = str(item.get("status") or "").strip().upper()
        if status in _NA:
            value: Any = "na"
        elif c.type == "scale":
            value = item.get("score")
        elif status in _PASS:
            value = "yes"
        elif status in _FAIL:
            value = "no"
        else:
            problems.append(f"criterion '{c.id}': unknown status {item.get('status')!r}")
            continue
        ev = _evidence(item.get("evidence"), transcript)
        checked = item.get("checked_range")
        if not ev and not checked and not (value == "yes"):
            checked = whole  # skipped action / N/A: show which part of the chat was checked
        answers.append({"criterion_id": c.id, "answer": value, "explanation": str(item.get("reason") or ""),
                        "evidence": ev, "checked_range": checked})
    if problems:
        raise ServiceError(502, "AI answer does not match the checklist.", problems)
    return answers


# ----------------------------------------------------------------- reading
def overrides_from(appeals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Reviewer 'change' decisions, newest per criterion, in the form scoring_engine expects."""
    latest: Dict[str, Dict[str, Any]] = {}
    for a in sorted(appeals, key=lambda x: x["id"]):
        if a["status"] == "changed":
            latest[a["criterion_id"]] = {"criterion_id": a["criterion_id"], "answer": a["decision_answer"],
                                         "reason": a["decision_reason"], "reviewer": a["reviewer"]}
    return list(latest.values())


def _appeal_brief(a: Dict[str, Any]) -> Dict[str, Any]:
    return {k: a[k] for k in ("id", "status", "comment", "author", "decision_reason", "reviewer",
                              "created_at", "decided_at")}


def score_row(row: Dict[str, Any], appeals: Optional[List[Dict[str, Any]]] = None) -> se.ScoreOutcome:
    cl = checklist_service.get(row["checklist_version"])
    if appeals is None:
        appeals = db.list_appeals(evaluation_id=row["id"])
    return se.score_with_overrides(cl, row["answers"], overrides_from(appeals))


def build_view(row: Dict[str, Any], cached: bool = False) -> Dict[str, Any]:
    """Everything the evaluation card needs. `total` is the FINAL one (after reviewer decisions)."""
    appeals = db.list_appeals(evaluation_id=row["id"])
    outcome = score_row(row, appeals)
    final = outcome.final.model_dump(mode="json")
    latest_by_crit: Dict[str, Dict[str, Any]] = {}
    for a in sorted(appeals, key=lambda x: x["id"]):
        latest_by_crit[a["criterion_id"]] = a
    for c in final["criteria"]:
        a = latest_by_crit.get(c["id"])
        c["appeal"] = _appeal_brief(a) if a else None
    ai = row["ai"]
    return {
        "evaluation_id": row["id"], "group_id": row["group_id"], "conversation_id": row["conversation_id"],
        "checklist_version": row["checklist_version"], "language": row["language"], "channel": row["channel"],
        "created_at": row["created_at"], "cached": cached,
        "total": final["total"], "max_total": final["max_total"], "total_note": final["total_note"],
        "critical_error": final["critical_error"], "applicable_weight": final["applicable_weight"],
        "original_total": outcome.original.total, "total_delta": outcome.total_delta, "changed": outcome.changed,
        "criteria": final["criteria"], "sections": final["sections"],
        "summary": ai.get("summary"), "detected_language": ai.get("detected_language"),
        "validation_flags": ai.get("validation_flags") or [], "model": ai.get("model"),
        "appeals": [_appeal_brief(a) | {"criterion_id": a["criterion_id"]} for a in appeals],
    }


def get_view_by_id(evaluation_id: int) -> Dict[str, Any]:
    row = db.get_evaluation(evaluation_id)
    if row is None:
        raise ServiceError(404, f"Evaluation {evaluation_id} not found.")
    return build_view(row)


def get_latest_view(group_id: str, conversation_id: str, version: Optional[int] = None) -> Dict[str, Any]:
    version, _ = checklist_service.resolve(version)
    row = db.latest_evaluation(group_id, conversation_id, version)
    if row is None:
        raise ServiceError(404, f"No evaluation of '{conversation_id}' under checklist version {version}.")
    return build_view(row)


def summaries(group_id: str, version: Optional[int] = None) -> Dict[str, Dict[str, Any]]:
    """conversation_id -> short result for the registry list."""
    version, _ = checklist_service.resolve(version)
    appeals_by_eval: Dict[int, List[Dict[str, Any]]] = {}
    for a in db.list_appeals(group_id=group_id):
        appeals_by_eval.setdefault(a["evaluation_id"], []).append(a)
    out = {}
    for cid, row in db.latest_evaluations(group_id, version).items():
        ap = appeals_by_eval.get(row["id"], [])
        oc = score_row(row, ap)
        out[cid] = {"evaluation_id": row["id"], "checklist_version": version, "total": oc.final.total,
                    "critical_error": oc.final.critical_error, "appeals_open": sum(a["status"] == "open" for a in ap),
                    "appeals_total": len(ap), "reviewed": bool(oc.changed), "created_at": row["created_at"]}
    return out


# -------------------------------------------------------------- evaluating
async def evaluate(group_id: str, record: Dict[str, Any], version: Optional[int] = None,
                   force: bool = False) -> Dict[str, Any]:
    version, cl = checklist_service.resolve(version)
    cid = str(record["conversation_id"])

    if not force:
        row = db.latest_evaluation(group_id, cid, version)
        if row:
            return build_view(row, cached=True)

    dialogue = ds.to_ai_dialogue(record)
    if not dialogue["transcript"]:
        raise ServiceError(422, f"Dialogue '{cid}' has an empty transcript.")

    evaluator = _evaluator or _default_evaluator()
    try:
        ai = await asyncio.to_thread(evaluator, dialogue, checklist_for_ai(cl))
    except ServiceError:
        raise
    except Exception as exc:  # timeouts, bad JSON after retries, quota errors ...
        log.exception("AI evaluation failed for %s", cid)
        raise ServiceError(502, f"AI evaluation failed: {exc}")
    if not isinstance(ai, dict):
        raise ServiceError(502, "AI module returned something that is not an object.")

    answers = ai_result_to_answers(ai, cl, record)
    try:
        se.score(cl, answers)  # never store an answer the scoring engine rejects
    except se.ScoringError as exc:
        raise ServiceError(502, "AI answer was rejected by the scoring engine.", exc.problems)

    meta = {"summary": ai.get("summary"), "detected_language": ai.get("detected_language"),
            "validation_flags": ai.get("validation_flags") or [], "model": os.getenv("LLM_MODEL"),
            "provider": os.getenv("LLM_PROVIDER"), "raw": ai}
    row_id = db.insert_evaluation(group_id, cid, version, record.get("language"), record.get("channel"),
                                  answers, meta)
    return build_view(db.get_evaluation(row_id), cached=False)
