"""Builds the system and user prompts from a checklist dict.

Checklist shape (agree it with Backend; mirrors schemas.py):
{
  "criteria": [
    {"id": "01", "name": "...", "description": "...", "weight": 10,
     "type": "binary" | "scale", "scale": [0, 5, 10],   # scale only
     "na_condition": "when this criterion does not apply" | null}
  ],
  "critical_error": {"description": "..."}
}
The LLM never sees or changes weights beyond max_score; Backend computes the total.
"""
import json

from .language import language_instruction

SYSTEM_TEMPLATE = """You are a strict, fair quality-assurance expert for a brokerage call centre.
You read ONE client-operator conversation and score the OPERATOR against the checklist below.

## Checklist
{criteria_block}

## Critical (gross) error
{critical_block}

## Status definitions (use exactly these values)
- PASS: the operator clearly met the criterion. Requires evidence (an exact operator quote).
- FAIL: the operator had the chance to meet it and did not, or met it only partly. Evidence is a quote
  showing the violation if one exists, otherwise null (the absence of the behaviour is the reason).
- NA: the criterion cannot apply in this conversation, per its N/A condition. Never use NA just because
  you are unsure or the evidence is weak. Do NOT treat NA as FAIL.
For SCALE criteria "score" must be exactly one of the listed scale values (never any other number);
status is PASS when you give the top value and FAIL for any lower value. For NA, score is null.

## Rules
1. Evidence: copy quotes exactly from the transcript with the real speaker and timestamp. If you cannot find
   an exact quote, set evidence to null. Never invent, paraphrase or translate a quote.
2. Judge only what is written. Do not assume facts that are not in the transcript.
3. Do not compute totals and do not use weights; Backend does that.
4. Mark critical_error true only when you can cite exact quote(s) in critical_error_evidence.
5. {language_rule}
6. Keep each "reason" to one or two sentences, in English.

## Output
Return ONLY one JSON object, no markdown, in this exact shape:
{schema}
"""

SCHEMA_EXAMPLE = {
    "conversation_id": "<id>",
    "language": "RU|KZ|EN|MIXED",
    "criteria": [
        {"criterion_id": "01", "status": "PASS|FAIL|NA", "score": 10,
         "evidence": {"speaker": "operator", "timestamp": "00:05", "quote": "<exact quote>"},
         "reason": "<why>"},
        {"criterion_id": "02", "status": "FAIL", "score": 0, "evidence": None, "reason": "<why>"},
    ],
    "critical_error": False,
    "critical_error_evidence": [],
    "summary": "<one or two sentences>",
}


def _criterion_line(c):
    if c.get("type") == "scale":
        vals = ", ".join(str(v) for v in c.get("scale", []))
        kind = f"SCALE, allowed scores exactly: {vals}"
    else:
        kind = f"BINARY, score is {c['weight']} for PASS and 0 for FAIL"
    na = c.get("na_condition") or "never N/A"
    return (f"- [{c['id']}] {c['name']} (max {c['weight']}; {kind})\n"
            f"    Rule: {c['description']}\n    N/A when: {na}")


def build_system_prompt(checklist: dict, lang: str) -> str:
    criteria_block = "\n".join(_criterion_line(c) for c in checklist["criteria"])
    ce = checklist.get("critical_error", {}).get("description", "No critical error defined.")
    return SYSTEM_TEMPLATE.format(
        criteria_block=criteria_block,
        critical_block=ce,
        language_rule=language_instruction(lang),
        schema=json.dumps(SCHEMA_EXAMPLE, ensure_ascii=False, indent=1),
    )


def format_transcript(transcript) -> str:
    """One line per turn: [timestamp] speaker: text."""
    lines = []
    for t in transcript:
        ts = t.get("timestamp") or "--:--"
        lines.append(f"[{ts}] {t.get('speaker', '?')}: {t.get('text', '')}")
    return "\n".join(lines)


def build_user_prompt(dialogue: dict) -> str:
    return (f"Conversation id: {dialogue.get('id')}\n"
            f"Channel: {dialogue.get('type', 'unknown')}\n\n"
            f"<transcript>\n{format_transcript(dialogue['transcript'])}\n</transcript>")
