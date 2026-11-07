"""Validates the LLM answer and makes it safe for Backend.

Rules enforced here:
- valid JSON, every checklist criterion present exactly once
- status in PASS / FAIL / NA, N/A only where the criterion allows it
- binary score is forced to max (PASS) or 0 (FAIL); scale score must be an allowed value
- evidence quotes must exist in the transcript; speaker/timestamp are taken from the real turn
- PASS without a verified quote is downgraded to FAIL (no evidence, no credit)
- critical_error true only with at least one verified quote
"""
import json
import re
import unicodedata


class ValidationError(Exception):
    pass


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s)).lower()
    return re.sub(r"\s+", " ", s).strip()


def parse_json(raw: str) -> dict:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < 0:
        raise ValidationError("no JSON object in the answer")
    try:
        return json.loads(raw[start:end + 1])
    except json.JSONDecodeError as e:
        raise ValidationError(f"invalid JSON: {e}")


def find_quote(quote, transcript):
    """Return the transcript turn containing the quote, or None."""
    if not quote or not str(quote).strip():
        return None
    q = _norm(quote)
    for turn in transcript:
        if q in _norm(turn.get("text", "")):
            return turn
    return None


def _verify_evidence(ev, transcript, flags, label):
    if not ev:
        return None
    turn = find_quote(ev.get("quote") if isinstance(ev, dict) else None, transcript)
    if turn is None:
        flags.append(f"{label}: quote not found in transcript, evidence removed")
        return None
    return {"speaker": turn.get("speaker"), "timestamp": turn.get("timestamp"),
            "quote": ev["quote"].strip()}


def validate(raw_or_dict, checklist: dict, dialogue: dict) -> dict:
    data = parse_json(raw_or_dict) if isinstance(raw_or_dict, str) else raw_or_dict
    transcript = dialogue["transcript"]
    spec = {c["id"]: c for c in checklist["criteria"]}
    flags, out, seen = [], [], set()

    items = data.get("criteria")
    if not isinstance(items, list):
        raise ValidationError("'criteria' must be a list")

    for it in items:
        cid = str(it.get("criterion_id"))
        if cid not in spec:
            flags.append(f"unknown criterion {cid} ignored")
            continue
        if cid in seen:
            raise ValidationError(f"duplicate criterion {cid}")
        seen.add(cid)
        c = spec[cid]
        status = str(it.get("status", "")).upper()
        if status not in ("PASS", "FAIL", "NA"):
            raise ValidationError(f"criterion {cid}: bad status {it.get('status')!r}")
        if status == "NA" and not c.get("na_condition"):
            raise ValidationError(f"criterion {cid}: N/A not allowed")

        evidence = _verify_evidence(it.get("evidence"), transcript, flags, f"criterion {cid}")
        reason = str(it.get("reason", "")).strip()

        if status == "NA":
            score = None
        elif c.get("type") == "scale":
            allowed = c["scale"]
            try:
                score = float(it.get("score"))
            except (TypeError, ValueError):
                raise ValidationError(f"criterion {cid}: scale score missing")
            if score not in [float(v) for v in allowed]:
                raise ValidationError(f"criterion {cid}: score {score} not in {allowed}")
            status = "PASS" if score == max(allowed) else "FAIL"
        else:
            score = c["weight"] if status == "PASS" else 0

        if status == "PASS" and evidence is None:
            flags.append(f"criterion {cid}: PASS without verified quote, downgraded to FAIL")
            status, score = "FAIL", 0
            reason = (reason + " [no verifiable evidence]").strip()

        out.append({"criterion_id": cid, "status": status, "score": score,
                    "max_score": c["weight"], "evidence": evidence, "reason": reason})

    missing = set(spec) - seen
    if missing:
        raise ValidationError(f"missing criteria: {sorted(missing)}")
    out.sort(key=lambda x: list(spec).index(x["criterion_id"]))

    ce_ev = []
    for ev in data.get("critical_error_evidence") or []:
        v = _verify_evidence(ev, transcript, flags, "critical_error")
        if v:
            ce_ev.append(v)
    critical = bool(data.get("critical_error")) and bool(ce_ev)
    if data.get("critical_error") and not ce_ev:
        flags.append("critical_error claimed without verified quote, set to false")

    return {
        "conversation_id": dialogue.get("id"),
        "language": data.get("language"),
        "criteria": out,
        "critical_error": critical,
        "critical_error_evidence": ce_ev,
        "summary": str(data.get("summary", "")).strip(),
        "validation_flags": flags,
    }
