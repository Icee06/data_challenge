#!/usr/bin/env python3
"""Export evaluations to results/<dev|prod>/results.json  (appendix "Как сдать результаты", JSON 1.0).

It reads everything from the RUNNING backend, so evaluate first (UI or /docs), then run from backend/:

    python export_results.py FBroker --folder prod --team-name "Team"
    python export_results.py DEV --team-name "Team"

The file is validated against the appendix rules BEFORE it is written; if anything is wrong nothing is
written and the problems are printed. Dialogues without a saved evaluation are exported as
status="error" (never hidden, never a fake zero), as the appendix requires.
"""
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

CODES = ["contact", "discovery", "identification", "consultation", "empathy", "dialogue", "closure", "critical"]
# backend criterion id -> code required by the results format
ID_TO_CODE = {
    "contact": "contact", "needs": "discovery", "discovery": "discovery", "identification": "identification",
    "consultation": "consultation", "empathy": "empathy", "dialogue": "dialogue",
    "closing": "closure", "closure": "closure", "gross_errors": "critical", "critical": "critical",
}
BOOL_CODES = {"contact", "discovery", "identification", "empathy", "closure", "critical"}
NULLABLE = {"discovery", "identification", "consultation", "closure"}
SCALE_VALUES = {"consultation": {0, 5, 10, 15, 20}, "dialogue": {0, 4, 7, 10}}
LOCAL_PROVIDERS = {"ollama", "local", "lmstudio", "llamacpp", "llama.cpp", "vllm", "llamafile"}
REPO_ROOT = Path(__file__).resolve().parent.parent
NOT_EVALUATED = "Диалог не оценён: сохранённого результата нет (оценка не выполнялась или завершилась ошибкой)."


class ExportError(Exception):
    pass


# ----------------------------------------------------------------- conversion
def to_value(code: str, c: Dict[str, Any]) -> Any:
    ans = c.get("answer")
    if ans == "na":
        return None
    if ans == "yes":
        return True  # for "critical" this means: a violation was found
    if ans == "no":
        return False
    if isinstance(ans, (int, float)) and not isinstance(ans, bool):
        return int(ans) if float(ans).is_integer() else ans
    raise ExportError(f"criterion '{c.get('id')}': cannot convert answer {ans!r}")


def turns_for(c: Dict[str, Any], transcript: List[Dict[str, Any]]) -> List[int]:
    """1-based turn numbers over the ORIGINAL transcript array. With no quoted evidence (skipped action,
    N/A, no violation) we list the operator turns that were checked, like the appendix example does."""
    n = len(transcript)
    turns = sorted({e["message_index"] + 1 for e in (c.get("evidence") or [])
                    if isinstance(e.get("message_index"), int) and 0 <= e["message_index"] < n})
    if turns:
        return turns
    ops = [i + 1 for i, m in enumerate(transcript) if m.get("speaker") == "operator"]
    return ops or list(range(1, n + 1))


def rationale_for(c: Dict[str, Any]) -> str:
    text = (c.get("explanation") or "").strip()
    if text:
        return text
    if c.get("checked_range"):
        return f"Проверен участок: {c['checked_range']}."
    return "Пояснение не указано."


def error_record(conversation_id: str, message: str) -> Dict[str, Any]:
    return {"conversation_id": conversation_id, "status": "error", "criteria": [], "score": None, "error": message}


def build_record(view: Dict[str, Any], transcript: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_code: Dict[str, Dict[str, Any]] = {}
    for c in view["criteria"]:
        code = ID_TO_CODE.get(c["id"])
        if code is None:
            raise ExportError(
                f"criterion '{c['id']}' has no code in the results format. The export needs the ORIGINAL 8-criteria "
                f"checklist: pass --checklist-version N (see GET /api/checklist/versions).")
        if code in by_code:
            raise ExportError(f"two criteria map to '{code}' in checklist version {view.get('checklist_version')}")
        by_code[code] = c
    missing = [x for x in CODES if x not in by_code]
    if missing:
        raise ExportError(f"checklist version {view.get('checklist_version')} lacks: {', '.join(missing)}. "
                          f"Pass --checklist-version N of the original checklist.")
    if view.get("total") is None:
        return error_record(view["conversation_id"], "Итог не рассчитан: нет применимых критериев с весом.")

    criteria = [{"criterion_id": code, "value": to_value(code, by_code[code]),
                 "rationale": rationale_for(by_code[code]), "evidence_turns": turns_for(by_code[code], transcript)}
                for code in CODES]
    critical = criteria[-1]["value"]
    return {"conversation_id": view["conversation_id"], "status": "ok", "criteria": criteria,
            "score": {"final_score": round(float(view["total"]), 2), "critical_flag": bool(critical)}}


# ------------------------------------------------------------------- fetching
def collect(client, group_id: str, checklist_version: Optional[int] = None) -> Tuple[List[Dict[str, Any]], List[str]]:
    r = client.get("/api/dialogues", params={"group_id": group_id})
    r.raise_for_status()
    ids = [d["conversation_id"] for d in r.json()["dialogues"]]
    results, models = [], []
    for cid in ids:
        params: Dict[str, Any] = {"group_id": group_id}
        if checklist_version is not None:
            params["checklist_version"] = checklist_version
        r = client.get(f"/api/evaluation/{cid}", params=params)
        if r.status_code == 404:
            results.append(error_record(cid, NOT_EVALUATED))
            continue
        r.raise_for_status()
        view = r.json()
        d = client.get(f"/api/dialogues/{cid}", params={"group_id": group_id})
        d.raise_for_status()
        results.append(build_record(view, d.json().get("transcript") or []))
        if view.get("model"):
            models.append(view["model"])
    return results, models


def infer_mode(provider: Optional[str]) -> Optional[str]:
    if not provider:
        return None
    return "local" if provider.lower() in LOCAL_PROVIDERS else "external_api"


def build_payload(client, group_id: str, team_name: str, mode: Optional[str] = None,
                  model_name: Optional[str] = None, checklist_version: Optional[int] = None) -> Dict[str, Any]:
    results, models = collect(client, group_id, checklist_version)
    try:
        health = client.get("/health").json()
    except Exception:
        health = {}
    name = model_name or ", ".join(dict.fromkeys(models)) or health.get("llm_model")
    mode = mode or infer_mode(health.get("llm_provider"))
    if not name:
        raise ExportError("Model name unknown (LLM_MODEL is not set): pass --model-name.")
    if not mode:
        raise ExportError("Model mode unknown (LLM_PROVIDER is not set): pass --mode local|external_api|hybrid.")
    return {"schema_version": "1.0", "team_name": team_name, "model": {"mode": mode, "name": name},
            "results": results}


# ----------------------------------------------------------------- validation
def validate_payload(p: Dict[str, Any], expected_ids: Optional[List[str]] = None,
                     expected_count: Optional[int] = None) -> List[str]:
    out: List[str] = []
    if p.get("schema_version") != "1.0":
        out.append("schema_version must be '1.0'")
    if not str(p.get("team_name") or "").strip():
        out.append("team_name is empty")
    model = p.get("model") or {}
    if model.get("mode") not in ("local", "external_api", "hybrid"):
        out.append("model.mode must be local, external_api or hybrid")
    if not str(model.get("name") or "").strip():
        out.append("model.name is empty")
    res = p.get("results")
    if not isinstance(res, list):
        return out + ["results must be a list"]
    ids = [r.get("conversation_id") for r in res]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        out.append(f"duplicate conversation_id: {dup}")
    if expected_ids is not None and sorted(ids) != sorted(expected_ids):
        out.append(f"ids differ from the API: missing {sorted(set(expected_ids) - set(ids))}, "
                   f"extra {sorted(set(ids) - set(expected_ids))}")
    if expected_count is not None and len(res) != expected_count:
        out.append(f"expected exactly {expected_count} records, got {len(res)}")
    for r in res:
        cid = r.get("conversation_id")
        if r.get("status") == "error":
            if r.get("criteria") != [] or r.get("score") is not None or not str(r.get("error") or "").strip():
                out.append(f"{cid}: an error record needs criteria=[], score=null and an error text")
            continue
        if r.get("status") != "ok":
            out.append(f"{cid}: status must be 'ok' or 'error'")
            continue
        crit = r.get("criteria") or []
        codes = [c.get("criterion_id") for c in crit]
        if sorted(codes) != sorted(CODES):
            out.append(f"{cid}: criteria must be exactly {CODES}, got {codes}")
            continue
        vals = {c["criterion_id"]: c.get("value") for c in crit}
        for c in crit:
            code, v = c["criterion_id"], c.get("value")
            if v is None:
                ok = code in NULLABLE
            elif code in BOOL_CODES:
                ok = isinstance(v, bool)
            else:
                ok = isinstance(v, int) and not isinstance(v, bool) and v in SCALE_VALUES[code]
            if not ok:
                out.append(f"{cid}/{code}: invalid value {v!r}")
            if not str(c.get("rationale") or "").strip():
                out.append(f"{cid}/{code}: empty rationale")
            turns = c.get("evidence_turns")
            if not (isinstance(turns, list) and all(isinstance(t, int) and not isinstance(t, bool) and t >= 1
                                                    for t in turns)):
                out.append(f"{cid}/{code}: evidence_turns must be a list of integers >= 1")
        s = r.get("score") or {}
        fs = s.get("final_score")
        if not (isinstance(fs, (int, float)) and not isinstance(fs, bool) and 0 <= fs <= 100 and round(fs, 2) == fs):
            out.append(f"{cid}: final_score must be 0..100 with at most 2 decimals, got {fs!r}")
        if s.get("critical_flag") != vals.get("critical"):
            out.append(f"{cid}: critical_flag must equal the 'critical' value")
    return out


# ------------------------------------------------------------------------ CLI
def main(argv: Optional[List[str]] = None, client=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Export evaluations to results/<dev|prod>/results.json")
    ap.add_argument("group_id", help="DEV or FBroker (the control set)")
    ap.add_argument("--folder", choices=["dev", "prod"], help="default: DEV -> dev, FBroker -> prod")
    ap.add_argument("--team-name", default=os.getenv("TEAM_NAME"), help="or set env TEAM_NAME")
    ap.add_argument("--model-name", help="default: the model stored with the evaluations")
    ap.add_argument("--mode", choices=["local", "external_api", "hybrid"], help="default: from LLM_PROVIDER")
    ap.add_argument("--checklist-version", type=int, help="default: the active checklist")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--out", help="override the output path")
    ap.add_argument("--force", action="store_true", help="allow exporting the SWAP set")
    a = ap.parse_args(argv)

    if a.group_id.lower() == "swap" and not a.force:
        print("Refusing: the organizers said NOT to evaluate SWAP or add it to results/prod yet (use --force).")
        return 2
    folder = a.folder or {"dev": "dev", "fbroker": "prod"}.get(a.group_id.lower())
    if not folder:
        print("Pass --folder dev|prod for this group.")
        return 2
    if not (a.team_name or "").strip():
        print("Team name missing: pass --team-name \"...\" (or set env TEAM_NAME).")
        return 2

    own = client is None
    client = client or httpx.Client(base_url=a.base_url, timeout=60)
    try:
        payload = build_payload(client, a.group_id, a.team_name.strip(), a.mode, a.model_name, a.checklist_version)
        listing = client.get("/api/dialogues", params={"group_id": a.group_id}).json()
        expected = [d["conversation_id"] for d in listing["dialogues"]]
    except httpx.ConnectError:
        print(f"Backend is not running at {a.base_url}. Start it: uvicorn main:app --port 8000")
        return 1
    except (ExportError, httpx.HTTPError) as exc:
        print(f"Export failed: {exc}")
        return 1
    finally:
        if own:
            client.close()

    problems = validate_payload(payload, expected_ids=expected, expected_count=10 if folder == "prod" else None)
    if problems:
        print("NOT WRITTEN. Problems:")
        for p in problems:
            print("  -", p)
        return 1

    path = Path(a.out) if a.out else REPO_ROOT / "results" / folder / "results.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    again = json.loads(path.read_text(encoding="utf-8"))  # re-read what was really written
    if validate_payload(again, expected_ids=expected):
        print("Written file failed re-validation, do not push it.")
        return 1

    ok = [r for r in payload["results"] if r["status"] == "ok"]
    bad = [r for r in payload["results"] if r["status"] == "error"]
    print(f"Wrote {path}\n  records: {len(payload['results'])}  ok: {len(ok)}  error: {len(bad)}")
    print(f"  team: {payload['team_name']} | model: {payload['model']['name']} ({payload['model']['mode']})")
    for r in payload["results"]:
        if r["status"] == "ok":
            print(f"  {r['conversation_id']:<14} {r['score']['final_score']:>6}  critical={r['score']['critical_flag']}")
        else:
            print(f"  {r['conversation_id']:<14} ERROR")
    if bad:
        print(f"\n{len(bad)} dialogue(s) not evaluated. Press 'Evaluate pending' again, then re-run this script.")
    print("\nNext: git add results && git commit -m \"results\" && git push")
    return 0


if __name__ == "__main__":
    sys.exit(main())
