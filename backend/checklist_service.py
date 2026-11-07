"""Checklist storage with versions. The active version is what new evaluations use.

Saving never edits an old version: it creates a new one and makes it active. Old
evaluations keep pointing at the version they were made with. Weights do not have to
sum to 100 (the scoring engine normalises), so a mismatch is only a warning.
"""
from typing import Any, Dict, List, Tuple

import db
import scoring_engine as se
from schemas import ServiceError


def ensure_seeded() -> None:
    """First start: store the PDF checklist as version 1."""
    if db.count_checklists() == 0:
        default = se.default_checklist()
        db.insert_checklist(default.name, default.model_dump(), note="Default checklist from the PDF", activate=True)


def get(version: int) -> se.Checklist:
    ensure_seeded()
    row = db.get_checklist(version)
    if row is None:
        raise ServiceError(404, f"Checklist version {version} not found.")
    return se.Checklist.model_validate(row["config"])


def get_active() -> Tuple[int, se.Checklist]:
    ensure_seeded()
    row = db.get_active_checklist()
    return row["version"], se.Checklist.model_validate(row["config"])


def resolve(version: Any = None) -> Tuple[int, se.Checklist]:
    return get_active() if version is None else (version, get(version))


def warnings_for(cl: se.Checklist) -> List[str]:
    out: List[str] = []
    if abs(cl.weights_total - 100) > 1e-9:
        out.append(f"Weights sum to {cl.weights_total:g}, not 100. Scores are normalised to 100 automatically.")
    for c in cl.criteria:
        if not c.description.strip():
            out.append(f"Criterion '{c.id}' has no description; the AI prompt will only see its name.")
        if c.allow_na and not c.na_condition.strip():
            out.append(f"Criterion '{c.id}' allows N/A but has no N/A condition; the AI will have to guess.")
    return out


def save_new_version(cl: se.Checklist, note: str = "") -> Dict[str, Any]:
    ensure_seeded()
    cl = cl.model_copy(deep=True)
    for c in cl.criteria:
        c.id = c.id.strip()
        c.name = c.name.strip()
    if not any(c.type != "flag" and c.weight > 0 for c in cl.criteria):
        raise ServiceError(422, "At least one non-flag criterion must have a weight above 0.")
    version = db.insert_checklist(cl.name, cl.model_dump(), note=note, activate=True)
    return {"version": version, "is_active": True, "weights_total": cl.weights_total, "warnings": warnings_for(cl),
            "checklist": cl.model_dump()}


def list_versions() -> List[Dict[str, Any]]:
    ensure_seeded()
    out = []
    for r in db.list_checklists():
        cl = se.Checklist.model_validate(r["config"])
        out.append({"version": r["version"], "name": r["name"], "is_active": r["is_active"], "note": r["note"],
                    "created_at": r["created_at"], "criteria_count": len(cl.criteria),
                    "weights_total": cl.weights_total, "criteria": [c.id for c in cl.criteria]})
    return out
