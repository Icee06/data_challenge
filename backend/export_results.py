"""Export results in the organizers' format (Student Guide appendix, "Результаты оценки, формат JSON 1.0").

Run from backend/ (the server does not need to be running):

  python export_results.py --set dev                                   # DEV  -> ../results/dev/results.json
  python export_results.py --set prod --group <control group_id> --evaluate-missing
  python export_results.py --check ../results/prod/results.json --expect 10

What it does
  * Takes every dialogue of the dataset (same loading as the API: cache file first, then the organizer API).
  * For each one it reads the latest saved evaluation under the active checklist, the same result the UI shows,
    reviewer decisions included (use --ai-only to export the AI answers without them).
  * --evaluate-missing first evaluates dialogues that have no saved result (one AI call each, one at a time).
  * A dialogue without a result is written as status "error" with the reason. It is never replaced by a zero score.
  * Before writing, the file is checked against the appendix rules; problems are printed and the exit code is 1.

Format rules implemented (appendix pages 2-3)
  criterion codes  contact, discovery, identification, consultation, empathy, dialogue, closure, critical
  value            true = "Да", false = "Нет", null = N/A, a number = scale step before weighting
  evidence_turns   numbers of the supporting messages, counted from 1 over the original transcript
                   (the app counts from #0, so message #7 in the app is 8 here)
  final_score      0-100, two decimals, N/A weight redistributed; critical_flag equals the value of "critical"
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent

# backend criterion id -> code required by the organizers
CODES = {"contact": "contact", "needs": "discovery", "identification": "identification",
         "consultation": "consultation", "empathy": "empathy", "dialogue": "dialogue",
         "closing": "closure", "gross_errors": "critical"}
REQUIRED = ["contact", "discovery", "identification", "consultation", "empathy", "dialogue", "closure", "critical"]
BOOL = {"contact", "empathy", "critical"}                     # true / false
BOOL_OR_NA = {"discovery", "identification", "closure"}       # true / false / null
SCALE = {"consultation": {0, 5, 10, 15, 20}, "dialogue": {0, 4, 7, 10}}
SCALE_NA = {"consultation"}                                   # dialogue has no N/A


# --------------------------------------------------------------------------- building
def _num(x: Any) -> Any:
    return int(x) if isinstance(x, float) and x.is_integer() else x


def criterion_entry(c: Dict[str, Any], ai_only: bool) -> Dict[str, Any]:
    """One criterion of the evaluation card (evaluation_service.build_view) -> organizer format."""
    answer = c["original_answer"] if (ai_only and c.get("source") == "reviewer") else c["answer"]
    if isinstance(answer, str):
        answer = answer.strip().lower()
    if c["type"] == "flag":
        value: Any = answer == "yes"
    elif answer == "na":
        value = None
    elif c["type"] == "scale":
        value = _num(answer)
    else:
        value = answer == "yes"
    turns = sorted({e["message_index"] + 1 for e in c.get("evidence") or [] if e.get("message_index") is not None})
    rationale = (c.get("explanation") or "").strip()
    if not rationale and c.get("checked_range"):
        rationale = f"Проверено: {c['checked_range']}"
    return {"criterion_id": CODES.get(c["id"], c["id"]), "value": value, "rationale": rationale, "evidence_turns": turns}


def result_entry(cid: str, view: Dict[str, Any], ai_only: bool) -> Dict[str, Any]:
    criteria = [criterion_entry(c, ai_only) for c in view["criteria"]]
    critical = next((x["value"] for x in criteria if x["criterion_id"] == "critical"), bool(view.get("critical_error")))
    total = view["original_total"] if ai_only else view["total"]
    return {"conversation_id": cid, "status": "ok", "criteria": criteria,
            "score": {"final_score": None if total is None else round(float(total), 2), "critical_flag": bool(critical)}}


def error_entry(cid: str, reason: str) -> Dict[str, Any]:
    return {"conversation_id": cid, "status": "error", "criteria": [], "score": None, "error": reason}


def _mode_from_env() -> str:
    base = os.getenv("LLM_BASE_URL", "")
    return "local" if any(h in base for h in ("localhost", "127.0.0.1")) else "external_api"


async def build(group: str, team: str, mode: Optional[str] = None, version: Optional[int] = None,
                ai_only: bool = False, evaluate_missing: bool = False, log=print) -> Tuple[Dict[str, Any], Dict[str, int]]:
    import main  # loads .env, the dataset store and the services
    import evaluation_service as ev
    from schemas import ServiceError

    await main.ensure_loaded(group)
    records = list(main._store[group].values())
    results, failures, models = [], {}, []
    lengths = {str(r["conversation_id"]): len(r.get("transcript") or []) for r in records}

    for n, rec in enumerate(records, 1):
        cid = str(rec["conversation_id"])
        view = None
        try:
            view = ev.get_latest_view(group, cid, version)
        except ServiceError as e:
            if e.status_code != 404:
                failures[cid] = e.message
        if view is None and evaluate_missing and cid not in failures:
            try:
                view = await ev.evaluate(group, rec, version)
                log(f"[{n}/{len(records)}] evaluated {cid}: {view['total']}")
            except ServiceError as e:
                failures[cid] = e.message + (f" ({'; '.join(e.problems)})" if e.problems else "")
            except Exception as e:  # network, quota ...
                failures[cid] = f"{type(e).__name__}: {e}"
            if cid in failures:
                log(f"[{n}/{len(records)}] FAILED {cid}: {failures[cid]}")
        if view is None:
            results.append(error_entry(cid, failures.get(cid) or "Оценка не получена: нет сохранённого результата для активного чек-листа."))
            continue
        if view.get("model") and view["model"] not in models:
            models.append(view["model"])
        results.append(result_entry(cid, view, ai_only))

    model_name = ", ".join(models) or os.getenv("LLM_MODEL", "") or "unknown"
    doc = {"schema_version": "1.0", "team_name": team,
           "model": {"mode": mode or _mode_from_env(), "name": model_name}, "results": results}
    return doc, lengths


# --------------------------------------------------------------------------- checking
def _two_decimals(x: float) -> bool:
    return abs(round(x, 2) - x) < 1e-9


def check(doc: Any, expect: Optional[int] = None, lengths: Optional[Dict[str, int]] = None) -> Tuple[List[str], List[str]]:
    """Returns (problems, warnings). Problems break the appendix rules; warnings deserve a look."""
    P, W = [], []
    if not isinstance(doc, dict):
        return ["the file must contain one JSON object"], W
    if doc.get("schema_version") != "1.0":
        P.append('schema_version must be "1.0"')
    if not str(doc.get("team_name") or "").strip():
        P.append("team_name is empty (use --team or TEAM_NAME in .env)")
    m = doc.get("model")
    if not isinstance(m, dict) or m.get("mode") not in ("local", "external_api", "hybrid") or not str(m.get("name") or "").strip():
        P.append('model must be {"mode": "local" | "external_api" | "hybrid", "name": "<model name>"}')
    res = doc.get("results")
    if not isinstance(res, list):
        return P + ["results must be a list"], W
    if expect is not None and len(res) != expect:
        P.append(f"{len(res)} records, expected exactly {expect}")
    seen = set()
    for i, r in enumerate(res):
        cid = r.get("conversation_id") if isinstance(r, dict) else None
        at = f"record {i + 1} ({cid})"
        if not isinstance(cid, str) or not cid:
            P.append(f"{at}: conversation_id missing"); continue
        if cid in seen:
            P.append(f"{at}: duplicate conversation_id")
        seen.add(cid)
        if lengths is not None and cid not in lengths:
            P.append(f"{at}: id is not in the dataset from the API")
        st = r.get("status")
        if st == "error":
            if r.get("criteria") != [] or r.get("score") is not None or not str(r.get("error") or "").strip():
                P.append(f'{at}: an error record needs criteria [], score null and a reason in "error"')
            else:
                W.append(f"{at}: status error: {r['error'][:120]}")
            continue
        if st != "ok":
            P.append(f'{at}: status must be "ok" or "error"'); continue
        crit = r.get("criteria")
        if not isinstance(crit, list):
            P.append(f"{at}: criteria must be a list"); continue
        codes = [c.get("criterion_id") for c in crit if isinstance(c, dict)]
        if len(codes) != len(set(codes)):
            P.append(f"{at}: a criterion appears more than once")
        missing = [c for c in REQUIRED if c not in codes]
        extra = [c for c in codes if c not in REQUIRED]
        if missing:
            (W if extra else P).append(f"{at}: missing criteria {missing}" + (f" (the checklist has {extra} instead)" if extra else ""))
        values = {}
        for c in crit:
            code, v = c.get("criterion_id"), c.get("value")
            values[code] = v
            if code in BOOL and not isinstance(v, bool):
                P.append(f"{at}: {code} must be true or false, got {v!r}")
            elif code in BOOL_OR_NA and not (v is None or isinstance(v, bool)):
                P.append(f"{at}: {code} must be true, false or null, got {v!r}")
            elif code in SCALE and not ((v is None and code in SCALE_NA) or (not isinstance(v, bool) and v in SCALE[code])):
                P.append(f"{at}: {code} must be one of {sorted(SCALE[code])}{' or null' if code in SCALE_NA else ''}, got {v!r}")
            turns = c.get("evidence_turns")
            if not isinstance(turns, list) or not all(isinstance(t, int) and not isinstance(t, bool) and t >= 1 for t in turns):
                P.append(f"{at}: {code}.evidence_turns must be a list of message numbers counted from 1")
            elif lengths and cid in lengths and any(t > lengths[cid] for t in turns):
                P.append(f"{at}: {code}.evidence_turns {turns} go past the last message ({lengths[cid]})")
            if not isinstance(c.get("rationale"), str):
                P.append(f"{at}: {code}.rationale must be text")
        sc = r.get("score")
        if not isinstance(sc, dict):
            P.append(f"{at}: score must be an object"); continue
        fs = sc.get("final_score")
        if fs is not None and (isinstance(fs, bool) or not isinstance(fs, (int, float)) or not 0 <= fs <= 100 or not _two_decimals(fs)):
            P.append(f"{at}: final_score must be 0-100 with at most two decimals, got {fs!r}")
        if fs is None:
            W.append(f"{at}: final_score is null (no applicable weighted criterion)")
        if not isinstance(sc.get("critical_flag"), bool) or ("critical" in values and sc.get("critical_flag") != values["critical"]):
            P.append(f"{at}: critical_flag must be true/false and equal the value of criterion critical")
    return P, W


# --------------------------------------------------------------------------- CLI
def _report(P: List[str], W: List[str], doc: Dict[str, Any]) -> None:
    res = doc.get("results") or []
    ok = sum(1 for r in res if isinstance(r, dict) and r.get("status") == "ok")
    print(f"\n{len(res)} records: {ok} ok, {len(res) - ok} error")
    for w in W[:15]:
        print("  warning:", w)
    if len(W) > 15:
        print(f"  ... {len(W) - 15} more warnings")
    for p in P[:30]:
        print("  PROBLEM:", p)
    print("Format check passed." if not P else f"Format check FAILED: {len(P)} problem(s).")


def cli() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Cyrillic on Windows consoles
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", choices=["dev", "prod"], default="dev", help="which results file to write")
    ap.add_argument("--group", help="dataset group_id (default DEV for --set dev; required for prod)")
    ap.add_argument("--out", help="output path (default ../results/<set>/results.json)")
    ap.add_argument("--team", default=os.getenv("TEAM_NAME", ""), help="team name (or TEAM_NAME in .env)")
    ap.add_argument("--mode", choices=["local", "external_api", "hybrid"], help="model mode (default: from .env)")
    ap.add_argument("--checklist-version", type=int, help="default: the active checklist")
    ap.add_argument("--ai-only", action="store_true", help="export AI answers without reviewer decisions")
    ap.add_argument("--evaluate-missing", action="store_true", help="evaluate dialogues without a saved result first")
    ap.add_argument("--expect", type=int, help="required number of records (default 65 for dev, 10 for prod)")
    ap.add_argument("--check", metavar="FILE", help="only check an existing results.json")
    a = ap.parse_args()

    if a.check:
        doc = json.loads(Path(a.check).read_text(encoding="utf-8"))
        P, W = check(doc, a.expect)
        _report(P, W, doc)
        return 1 if P else 0

    if a.set == "prod" and not a.group:
        ap.error("--set prod needs --group <control group_id> from the organizers")
    group = a.group or "DEV"
    expect = a.expect if a.expect is not None else (65 if a.set == "dev" else 10)
    out = Path(a.out) if a.out else ROOT / "results" / a.set / "results.json"

    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
    team = a.team or os.getenv("TEAM_NAME", "")
    try:
        doc, lengths = asyncio.run(build(group, team, a.mode, a.checklist_version, a.ai_only, a.evaluate_missing))
    except Exception as e:  # dataset not loadable, database problem ...
        print(f"Could not build the results: {getattr(e, 'detail', None) or e}")
        return 2
    P, W = check(doc, expect, lengths)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out}")
    _report(P, W, doc)
    return 1 if P else 0


if __name__ == "__main__":
    sys.exit(cli())
