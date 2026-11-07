"""SQLite storage: checklist versions, evaluations (with history) and appeals.

Design notes
  * An evaluation is stored with the checklist VERSION it was made under, so editing the
    checklist never touches old results, and a new version simply has no cache entry yet.
  * Re-running with force_refresh inserts a NEW row (history is kept). The latest row per
    (group_id, conversation_id, checklist_version) is the current result.
  * Only the AI's answers are stored. Totals are recomputed on read from answers + reviewer
    decisions, so a total can never go out of date.
Uses data/cqa.db (a new file: the old data/cqa_evaluations.db had a different schema).
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path(os.getenv("CQA_DB_PATH") or Path(__file__).resolve().parent / "data" / "cqa.db")
_ready: set = set()

SCHEMA = """
CREATE TABLE IF NOT EXISTS checklists (
    version     INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    config_json TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    is_active   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evaluations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id          TEXT NOT NULL,
    conversation_id   TEXT NOT NULL,
    checklist_version INTEGER NOT NULL REFERENCES checklists(version),
    language          TEXT,
    channel           TEXT,
    answers_json      TEXT NOT NULL,
    ai_json           TEXT NOT NULL,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_eval_key ON evaluations(group_id, conversation_id, checklist_version, id);
CREATE TABLE IF NOT EXISTS appeals (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_id        INTEGER NOT NULL REFERENCES evaluations(id),
    criterion_id         TEXT NOT NULL,
    comment              TEXT NOT NULL,
    author               TEXT,
    status               TEXT NOT NULL DEFAULT 'open',
    original_answer_json TEXT NOT NULL,
    decision_answer_json TEXT,
    decision_reason      TEXT,
    reviewer             TEXT,
    created_at           TEXT NOT NULL,
    decided_at           TEXT
);
CREATE INDEX IF NOT EXISTS ix_appeal_eval ON appeals(evaluation_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _loads(text: Optional[str]) -> Any:
    return None if text is None else json.loads(text)


@contextmanager
def connect():
    """One short-lived connection per operation; commits on success, always closes."""
    path = Path(DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        if str(path) not in _ready:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
            _ready.add(str(path))
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ------------------------------------------------------------------ checklists
def _checklist(r: sqlite3.Row) -> Dict[str, Any]:
    return {"version": r["version"], "name": r["name"], "config": json.loads(r["config_json"]),
            "note": r["note"], "is_active": bool(r["is_active"]), "created_at": r["created_at"]}


def count_checklists() -> int:
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM checklists").fetchone()[0]


def insert_checklist(name: str, config: Dict[str, Any], note: str = "", activate: bool = True) -> int:
    with connect() as c:
        if activate:
            c.execute("UPDATE checklists SET is_active = 0")
        cur = c.execute(
            "INSERT INTO checklists (name, config_json, note, is_active, created_at) VALUES (?,?,?,?,?)",
            (name, json.dumps(config, ensure_ascii=False), note, 1 if activate else 0, _now()),
        )
        return cur.lastrowid


def get_checklist(version: int) -> Optional[Dict[str, Any]]:
    with connect() as c:
        r = c.execute("SELECT * FROM checklists WHERE version = ?", (version,)).fetchone()
        return _checklist(r) if r else None


def get_active_checklist() -> Optional[Dict[str, Any]]:
    with connect() as c:
        r = c.execute("SELECT * FROM checklists WHERE is_active = 1 ORDER BY version DESC LIMIT 1").fetchone()
        return _checklist(r) if r else None


def list_checklists() -> List[Dict[str, Any]]:
    with connect() as c:
        return [_checklist(r) for r in c.execute("SELECT * FROM checklists ORDER BY version DESC")]


# ----------------------------------------------------------------- evaluations
def _evaluation(r: sqlite3.Row) -> Dict[str, Any]:
    return {"id": r["id"], "group_id": r["group_id"], "conversation_id": r["conversation_id"],
            "checklist_version": r["checklist_version"], "language": r["language"], "channel": r["channel"],
            "answers": json.loads(r["answers_json"]), "ai": json.loads(r["ai_json"]),
            "created_at": r["created_at"]}


def insert_evaluation(group_id: str, conversation_id: str, checklist_version: int, language: Optional[str],
                      channel: Optional[str], answers: List[Dict[str, Any]], ai: Dict[str, Any]) -> int:
    with connect() as c:
        cur = c.execute(
            "INSERT INTO evaluations (group_id, conversation_id, checklist_version, language, channel,"
            " answers_json, ai_json, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (group_id, conversation_id, checklist_version, language, channel,
             json.dumps(answers, ensure_ascii=False), json.dumps(ai, ensure_ascii=False, default=str), _now()),
        )
        return cur.lastrowid


def get_evaluation(evaluation_id: int) -> Optional[Dict[str, Any]]:
    with connect() as c:
        r = c.execute("SELECT * FROM evaluations WHERE id = ?", (evaluation_id,)).fetchone()
        return _evaluation(r) if r else None


def latest_evaluation(group_id: str, conversation_id: str, checklist_version: int) -> Optional[Dict[str, Any]]:
    with connect() as c:
        r = c.execute(
            "SELECT * FROM evaluations WHERE group_id = ? AND conversation_id = ? AND checklist_version = ?"
            " ORDER BY id DESC LIMIT 1", (group_id, conversation_id, checklist_version)).fetchone()
        return _evaluation(r) if r else None


def latest_evaluations(group_id: str, checklist_version: int) -> Dict[str, Dict[str, Any]]:
    """conversation_id -> newest evaluation, for the registry."""
    with connect() as c:
        rows = c.execute(
            "SELECT e.* FROM evaluations e JOIN ("
            "  SELECT conversation_id, MAX(id) AS mid FROM evaluations"
            "  WHERE group_id = ? AND checklist_version = ? GROUP BY conversation_id) m ON e.id = m.mid",
            (group_id, checklist_version)).fetchall()
        return {r["conversation_id"]: _evaluation(r) for r in rows}


def evaluation_history(group_id: str, conversation_id: str) -> List[Dict[str, Any]]:
    with connect() as c:
        rows = c.execute("SELECT * FROM evaluations WHERE group_id = ? AND conversation_id = ? ORDER BY id DESC",
                         (group_id, conversation_id)).fetchall()
        return [_evaluation(r) for r in rows]


# --------------------------------------------------------------------- appeals
def _appeal(r: sqlite3.Row) -> Dict[str, Any]:
    return {"id": r["id"], "evaluation_id": r["evaluation_id"], "group_id": r["group_id"],
            "conversation_id": r["conversation_id"], "criterion_id": r["criterion_id"],
            "comment": r["comment"], "author": r["author"], "status": r["status"],
            "original_answer": _loads(r["original_answer_json"]),
            "decision_answer": _loads(r["decision_answer_json"]),
            "decision_reason": r["decision_reason"], "reviewer": r["reviewer"],
            "created_at": r["created_at"], "decided_at": r["decided_at"]}


_APPEAL_SELECT = ("SELECT a.*, e.group_id AS group_id, e.conversation_id AS conversation_id "
                  "FROM appeals a JOIN evaluations e ON e.id = a.evaluation_id")


def insert_appeal(evaluation_id: int, criterion_id: str, comment: str, author: Optional[str],
                  original_answer: Any) -> int:
    with connect() as c:
        cur = c.execute(
            "INSERT INTO appeals (evaluation_id, criterion_id, comment, author, status, original_answer_json,"
            " created_at) VALUES (?,?,?,?, 'open', ?, ?)",
            (evaluation_id, criterion_id, comment, author, json.dumps(original_answer, ensure_ascii=False), _now()))
        return cur.lastrowid


def get_appeal(appeal_id: int) -> Optional[Dict[str, Any]]:
    with connect() as c:
        r = c.execute(_APPEAL_SELECT + " WHERE a.id = ?", (appeal_id,)).fetchone()
        return _appeal(r) if r else None


def list_appeals(evaluation_id: Optional[int] = None, status: Optional[str] = None,
                 group_id: Optional[str] = None) -> List[Dict[str, Any]]:
    sql, args = _APPEAL_SELECT + " WHERE 1=1", []
    if evaluation_id is not None:
        sql += " AND a.evaluation_id = ?"
        args.append(evaluation_id)
    if status:
        sql += " AND a.status = ?"
        args.append(status)
    if group_id:
        sql += " AND e.group_id = ?"
        args.append(group_id)
    with connect() as c:
        return [_appeal(r) for r in c.execute(sql + " ORDER BY a.id DESC", args)]


def open_appeal_exists(evaluation_id: int, criterion_id: str) -> bool:
    with connect() as c:
        return c.execute("SELECT 1 FROM appeals WHERE evaluation_id = ? AND criterion_id = ? AND status = 'open'",
                         (evaluation_id, criterion_id)).fetchone() is not None


def decide_appeal(appeal_id: int, status: str, decision_answer: Any, reason: str, reviewer: Optional[str]) -> None:
    with connect() as c:
        c.execute("UPDATE appeals SET status = ?, decision_answer_json = ?, decision_reason = ?, reviewer = ?,"
                  " decided_at = ? WHERE id = ?",
                  (status, json.dumps(decision_answer, ensure_ascii=False), reason, reviewer, _now(), appeal_id))
