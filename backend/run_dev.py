"""Run dialogues through evaluate_dialogue() and save qa/test_results_v1.csv.

Usage (from backend/):
  python run_dev.py --limit 10
  python run_dev.py --ids 001,024 --out ../qa/test_results_v2.csv
  python run_dev.py --checklist ai/checklist.json
  python run_dev.py --fake        # offline smoke test, no API calls

Reads backend/data/DEV.json by default. Accepted shapes: a list of dialogues, or a dict with a
"dialogues"/"data"/"items" list. Each dialogue needs an id and a transcript list; common field
names (conversation_id, messages, role, time, content...) are normalised below. Adjust
`normalize()` if the organizers' format differs.
"""
import argparse
import csv
import json
import os
import sys
import time

from ai import llm_client
from ai.evaluator import EvaluationFailed, evaluate_dialogue

HERE = os.path.dirname(os.path.abspath(__file__))


def load_env(path):
    """Minimal .env loader (no python-dotenv needed)."""
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def normalize(d):
    turns = d.get("transcript") or d.get("messages") or d.get("turns") or d.get("dialogue") or []
    out = []
    for t in turns:
        out.append({
            "speaker": t.get("speaker") or t.get("role") or t.get("from") or "?",
            "timestamp": t.get("timestamp") or t.get("time") or t.get("ts"),
            "text": t.get("text") or t.get("content") or t.get("message") or "",
        })
    return {
        "id": str(d.get("id") or d.get("conversation_id") or d.get("dialogue_id")),
        "type": d.get("type") or d.get("channel") or "unknown",
        "transcript": out,
    }


def load_dialogues(path):
    data = json.load(open(path, encoding="utf-8"))
    if isinstance(data, dict):
        for k in ("dialogues", "data", "items", "conversations"):
            if isinstance(data.get(k), list):
                data = data[k]
                break
    return [normalize(d) for d in data]


def fake_llm(checklist):
    def f(system, user):
        crit = [{"criterion_id": c["id"], "status": "FAIL", "score": 0, "evidence": None,
                 "reason": "fake"} for c in checklist["criteria"]]
        return json.dumps({"criteria": crit, "critical_error": False,
                           "critical_error_evidence": [], "summary": "fake run"})
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(HERE, "data", "DEV.json"))
    ap.add_argument("--checklist", default=os.path.join(HERE, "ai", "checklist.template.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "..", "qa", "test_results_v1.csv"))
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--ids", default="")
    ap.add_argument("--sleep", type=float, default=0.0)
    ap.add_argument("--fake", action="store_true")
    a = ap.parse_args()

    load_env(os.path.join(HERE, ".env"))
    checklist = json.load(open(a.checklist, encoding="utf-8"))
    dialogues = load_dialogues(a.data)
    if a.ids:
        want = set(a.ids.split(","))
        dialogues = [d for d in dialogues if d["id"] in want]
    else:
        dialogues = dialogues[:a.limit]

    llm = fake_llm(checklist) if a.fake else None
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    cols = ["dialogue_id", "language", "criterion_id", "status", "score", "max_score",
            "speaker", "timestamp", "quote", "reason", "critical_error", "flags", "error"]
    ok = bad = 0
    with open(a.out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for d in dialogues:
            try:
                r = evaluate_dialogue(d, checklist, llm=llm)
            except EvaluationFailed as e:
                w.writerow({"dialogue_id": d["id"], "error": str(e)})
                bad += 1
                print(f"[FAIL] {d['id']}: {e}", file=sys.stderr)
                continue
            for c in r["criteria"]:
                ev = c["evidence"] or {}
                w.writerow({
                    "dialogue_id": d["id"], "language": r["detected_language"],
                    "criterion_id": c["criterion_id"], "status": c["status"],
                    "score": c["score"], "max_score": c["max_score"],
                    "speaker": ev.get("speaker"), "timestamp": ev.get("timestamp"),
                    "quote": ev.get("quote"), "reason": c["reason"],
                    "critical_error": r["critical_error"],
                    "flags": "; ".join(r["validation_flags"]), "error": "",
                })
            ok += 1
            print(f"[ok] {d['id']} ({r['detected_language']}) flags={len(r['validation_flags'])}")
            if a.sleep:
                time.sleep(a.sleep)
    print(f"\nDone: {ok} ok, {bad} failed -> {os.path.abspath(a.out)}")
    print("LLM usage:", llm_client.USAGE)


if __name__ == "__main__":
    main()
