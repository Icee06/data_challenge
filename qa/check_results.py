"""Step 7: check a results file before handing it in.

  python check_results.py ../results/prod/results.json --expect 10 [--dataset control.json]

Checks: the number of dialogues, all 8 criteria present once, only allowed answers per criterion,
N/A only where the checklist allows it, evidence message numbers inside the dialogue, quotes found in
the cited message (when --dataset is given), and totals between 0 and 100.
Exit code 0 = file is fine, 1 = problems found. Use it alongside the team's export_results.py --check.
"""
import argparse, sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from compare import CRIT, NA_OK, crit_id, load_json, load_dataset, norm_answer, evidence_index  # noqa: E402

ALLOWED = {"c1": {"YES", "NO"}, "c2": {"YES", "NO"}, "c3": {"YES", "NO"}, "c4": {20, 15, 10, 5, 0}, "c5": {"YES", "NO"},
           "c6": {10, 7, 4, 0}, "c7": {"YES", "NO"}, "c8": {"YES", "NO"}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("--expect", type=int, help="expected number of dialogues")
    ap.add_argument("--dataset", help="the transcripts the results were made from, to check evidence and quotes")
    a = ap.parse_args()
    j = load_json(a.results)
    items = j if isinstance(j, list) else (j.get("results") or j.get("evaluations") or j.get("records") or [])
    data = load_dataset(a.dataset) if a.dataset else {}
    problems, seen = [], set()
    for it in items:
        cid = str(it.get("conversation_id") or it.get("dialogue_id") or it.get("id") or "?")
        if cid in seen:
            problems.append(f"{cid}: appears twice")
        seen.add(cid)
        crits = it.get("criteria") or it.get("results") or it.get("answers") or []
        if isinstance(crits, dict):
            crits = [{"id": k, **(v if isinstance(v, dict) else {"answer": v})} for k, v in crits.items()]
        got = {}
        for c in crits:
            k = crit_id(c.get("id") or c.get("criterion_id") or c.get("criterion") or c.get("code") or c.get("name"))
            if not k:
                problems.append(f"{cid}: unknown criterion {c.get('id') or c.get('criterion_id')!r}"); continue
            if k in got:
                problems.append(f"{cid} {k}: listed twice")
            ans = norm_answer(c.get("answer", c.get("status", c.get("result", c.get("value")))))
            if k in ("c4", "c6") and ans in ("YES", "NO") and isinstance(c.get("score"), (int, float)):
                ans = norm_answer(c["score"])
            got[k] = ans
            ok = ALLOWED[k] | ({"NA"} if k in NA_OK else set())
            if ans not in ok:
                problems.append(f"{cid} {k}: answer {ans!r} not allowed (allowed: {sorted(map(str, ok))})")
            ev = evidence_index(c)
            msgs = data.get(cid, {}).get("transcript")
            if msgs is not None and ev is not None:
                if not 0 <= ev < len(msgs):
                    problems.append(f"{cid} {k}: evidence #{ev} outside the dialogue (0–{len(msgs) - 1})")
                else:
                    evd = c.get("evidence") if isinstance(c.get("evidence"), dict) else {}
                    q = c.get("quote") or evd.get("quote")
                    if q and q.strip().strip("«»\"“”") not in msgs[ev]["text"]:
                        problems.append(f"{cid} {k}: quote not found in message #{ev}")
        missing = [k for k in CRIT if k not in got]
        if missing:
            problems.append(f"{cid}: missing {', '.join(missing)}")
        fs = it.get("final_score")
        if fs is not None and not (isinstance(fs, (int, float)) and 0 <= fs <= 100):
            problems.append(f"{cid}: final_score {fs!r} is not between 0 and 100")
    if a.expect is not None and len(seen) != a.expect:
        problems.append(f"expected {a.expect} dialogues, found {len(seen)}")
    if data:
        absent = sorted(set(data) - seen)
        if absent:
            problems.append(f"no results for: {', '.join(absent)}")
    print(f"{a.results}: {len(seen)} dialogues checked.")
    if problems:
        print(f"{len(problems)} problem(s):"); [print(" -", p) for p in problems]; sys.exit(1)
    print("OK: every dialogue has all 8 criteria with allowed answers.")


if __name__ == "__main__":
    main()
