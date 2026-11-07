"""Appeals: a user disputes one criterion, a reviewer confirms or changes it.

A reviewer decision never edits the AI's stored answers. 'change' becomes an override that the
scoring engine applies on top, so the card can always show: original answer -> decision -> final.
"""
from typing import Any, Dict, List, Optional

import checklist_service
import db
import evaluation_service as ev
import scoring_engine as se
from schemas import ServiceError


def _criterion(view: Dict[str, Any], criterion_id: str) -> Dict[str, Any]:
    return next(c for c in view["criteria"] if c["id"] == criterion_id)


def card(appeal: Dict[str, Any], view: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """An appeal together with the disputed criterion, so the reviewer sees everything in one place."""
    view = view or ev.get_view_by_id(appeal["evaluation_id"])
    c = _criterion(view, appeal["criterion_id"])
    return {
        **appeal,
        "criterion": {k: c.get(k) for k in ("id", "name", "type", "weight", "answer", "original_answer", "explanation",
                                            "evidence", "checked_range", "source", "points", "applicable")},
        "evaluation": {"evaluation_id": view["evaluation_id"], "total": view["total"],
                       "original_total": view["original_total"], "checklist_version": view["checklist_version"]},
    }


def create(evaluation_id: int, criterion_id: str, comment: str, author: Optional[str] = None) -> Dict[str, Any]:
    if not comment.strip():
        raise ServiceError(422, "An appeal needs a comment.")
    row = db.get_evaluation(evaluation_id)
    if row is None:
        raise ServiceError(404, f"Evaluation {evaluation_id} not found.")
    view = ev.build_view(row)
    if criterion_id not in {c["id"] for c in view["criteria"]}:
        raise ServiceError(422, f"Criterion '{criterion_id}' is not part of this evaluation.")
    if db.open_appeal_exists(evaluation_id, criterion_id):
        raise ServiceError(409, f"There is already an open appeal for '{criterion_id}'.")
    shown = _criterion(view, criterion_id)["answer"]  # the answer being disputed
    appeal_id = db.insert_appeal(evaluation_id, criterion_id, comment.strip(), author, shown)
    return card(db.get_appeal(appeal_id), view)


def decide(appeal_id: int, decision: str, reason: str, answer: Any = None,
           reviewer: Optional[str] = None) -> Dict[str, Any]:
    appeal = db.get_appeal(appeal_id)
    if appeal is None:
        raise ServiceError(404, f"Appeal {appeal_id} not found.")
    if appeal["status"] != "open":
        raise ServiceError(409, f"Appeal {appeal_id} is already decided ({appeal['status']}).")
    if not reason.strip():
        raise ServiceError(422, "A decision needs a reason.")

    row = db.get_evaluation(appeal["evaluation_id"])
    cl_answers = row["answers"]
    cid = appeal["criterion_id"]
    existing = ev.overrides_from(db.list_appeals(evaluation_id=row["id"]))
    before = ev.score_row(row)

    if decision == "confirm":
        status = "confirmed"
        stored = next(c.answer for c in before.final.criteria if c.id == cid)
    else:
        if answer is None:
            raise ServiceError(422, "decision 'change' needs the new answer (yes / no / na / a scale step).")
        new = [o for o in existing if o["criterion_id"] != cid] + [
            {"criterion_id": cid, "answer": answer, "reason": reason.strip(), "reviewer": reviewer}]
        try:
            after = se.score_with_overrides(checklist_service.get(row["checklist_version"]), cl_answers, new)
        except se.ScoringError as exc:
            raise ServiceError(422, "The new answer is not valid for this criterion.", exc.problems)
        old_answer = next(c.answer for c in before.final.criteria if c.id == cid)
        stored = next(c.answer for c in after.final.criteria if c.id == cid)
        if stored == old_answer:
            raise ServiceError(422, "The new answer equals the current one; use decision 'confirm' instead.")
        status = "changed"

    db.decide_appeal(appeal_id, status, stored, reason.strip(), reviewer)
    view = ev.build_view(db.get_evaluation(row["id"]))
    return {"appeal": card(db.get_appeal(appeal_id), view), "evaluation": view}


def list_cards(evaluation_id: Optional[int] = None, status: Optional[str] = None,
               group_id: Optional[str] = None) -> List[Dict[str, Any]]:
    appeals = db.list_appeals(evaluation_id=evaluation_id, status=status, group_id=group_id)
    views: Dict[int, Dict[str, Any]] = {}
    out = []
    for a in appeals:
        if a["evaluation_id"] not in views:
            views[a["evaluation_id"]] = ev.get_view_by_id(a["evaluation_id"])
        out.append(card(a, views[a["evaluation_id"]]))
    return out
