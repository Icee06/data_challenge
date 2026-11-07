"""
backend/llm_client.py

LLM evaluation client for CQA.

One LLM call per dialogue: all checklist criteria are scored in a single
JSON response. The LLM only returns verdicts, evidence message indices and
explanations. All math (weights, N/A redistribution, rounding, critical error
flag aggregation) lives in scoring_engine.py.

Providers:
  * Gemini (primary)  - env GEMINI_API_KEY, optional GEMINI_MODEL
  * Groq   (fallback) - env GROQ_API_KEY,  optional GROQ_MODEL

Checklist format expected by this module (build it from the checklist PDF /
the checklist editor; stored as JSON):

{
  "id": "default",
  "criteria": [
    {
      "id": "c01",
      "name": "Greeting",
      "prompt": "Did the operator greet the client at the start?",
      "type": "boolean",          # "boolean" (yes/no) or "scale"
      "scale_max": 5,             # only for type == "scale" (min is 0)
      "allow_na": true,
      "na_rule": "N/A if the client never waited for an operator reply.",
      "weight": 10,
      "critical": false           # true for the "gross error" criterion
    }
  ]
}
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"

MAX_RETRIES = 4
REQUEST_TIMEOUT = 120.0

LANG_NAMES = {"RU": "Russian", "KZ": "Kazakh", "EN": "English"}


class LLMError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def checklist_hash(checklist: Dict[str, Any]) -> str:
    """Stable hash of a checklist, use it together with conversation_id as cache key."""
    raw = json.dumps(checklist, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _format_transcript(transcript: List[Dict[str, Any]]) -> str:
    """Number every turn so the model can cite evidence by index, not by quote."""
    lines = []
    for i, u in enumerate(transcript):
        text = (u.get("text") or "").replace("\n", " ").strip()
        lines.append(f"[{i}] ({u.get('timestamp', '')}) {u.get('speaker', '?')}: {text}")
    return "\n".join(lines)


def _scale_steps(c: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Discrete rubric steps for a scale criterion, best score first."""
    return sorted(c.get("scale") or [], key=lambda s: -s["score"])


def _scale_max(c: Dict[str, Any]) -> float:
    steps = c.get("scale")
    if steps:
        return float(max(s["score"] for s in steps))
    return float(c.get("scale_max", 5))


def _guidance(c: Dict[str, Any]) -> str:
    return f"\n  guidance: {c['guidance']}" if c.get("guidance") else ""


def _general_notes(checklist: Dict[str, Any]) -> str:
    notes = checklist.get("general_notes") or []
    if not notes:
        return ""
    return "GENERAL NOTES:\n" + "\n".join(f"- {n}" for n in notes)


def _format_criteria(checklist: Dict[str, Any]) -> str:
    blocks = []
    for c in checklist["criteria"]:
        if c.get("type") == "scale":
            steps = _scale_steps(c)
            if steps:
                rubric = "\n".join(f"      {s['score']}: {s['description']}" for s in steps)
                answer = "scale: choose EXACTLY one of these scores:\n" + rubric
            else:
                answer = f"scale: integer score from 0 to {c.get('scale_max', 5)}"
        else:
            answer = "boolean: verdict is \"yes\" or \"no\""
        na = (
            f"N/A allowed. Use \"na\" when: {c.get('na_rule') or 'the criterion is not applicable to this dialogue'}"
            if c.get("allow_na")
            else "N/A NOT allowed for this criterion."
        )
        crit = "\nThis is the GROSS-ERROR criterion: \"yes\" means the violation happened." if c.get("critical") else ""
        blocks.append(
            f"- id: {c['id']}\n"
            f"  name: {c.get('name', '')}\n"
            f"  question: {c.get('prompt', '')}{_guidance(c)}\n"
            f"  answer type: {answer}\n"
            f"  {na}{crit}"
        )
    return "\n".join(blocks)


def build_prompt(record: Dict[str, Any], checklist: Dict[str, Any], explain_lang: str = "RU") -> str:
    transcript = record["transcript"]
    lang = LANG_NAMES.get(record.get("language", ""), record.get("language", ""))
    explain = LANG_NAMES.get(explain_lang, "Russian")
    return f"""You are a strict, fair quality-assurance reviewer of customer-support conversations at a brokerage.
You evaluate ONLY the OPERATOR (a human agent). Bot and system messages are context only: never penalize the operator for what the bot did.

Conversation id: {record.get('conversation_id')}
Channel: {record.get('channel')}
Dialogue language: {lang}
Each turn is numbered like [index] (timestamp) speaker: text.

TRANSCRIPT:
{_format_transcript(transcript)}

CHECKLIST (evaluate every criterion, in this order):
{_format_criteria(checklist)}

{_general_notes(checklist)}

RULES:
1. Return one result for EVERY criterion id in the checklist, no more, no less.
2. verdict is "yes", "no" or "na". For scale criteria always fill "score" with one of the allowed scores (verdict "yes" if score > 0, otherwise "no"). For boolean criteria set score to 1 for "yes", 0 for "no". For "na" set score to 0.
3. evidence_indices: the turn indexes (integers from the transcript) that support your verdict. Prefer operator turns. Use only indexes that exist. Give 1-3 indexes when the action happened or was done badly.
4. If an expected action was MISSING, evidence_indices may be empty, and checked_range must describe the part of the conversation you checked, e.g. "turns 0-12 (00:00-03:40)".
5. If verdict is "na", na_reason must explain why the criterion does not apply and explicit N/A conditions of that criterion must be met. Never use "na" when N/A is not allowed.
6. explanation: 1-3 short sentences written in {explain}, grounded in the transcript. Do not invent quotes or facts. Judge only what is visible in the transcript.
7. Be consistent with the dialogue language: Kazakh and Russian may be mixed, treat both as valid.
8. Output JSON only, matching the schema."""


def _response_schema() -> Dict[str, Any]:
    return {
        "type": "OBJECT",
        "properties": {
            "results": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "criterion_id": {"type": "STRING"},
                        "verdict": {"type": "STRING", "enum": ["yes", "no", "na"]},
                        "score": {"type": "NUMBER"},
                        "explanation": {"type": "STRING"},
                        "evidence_indices": {"type": "ARRAY", "items": {"type": "INTEGER"}},
                        "checked_range": {"type": "STRING"},
                        "na_reason": {"type": "STRING"},
                    },
                    "required": [
                        "criterion_id", "verdict", "score", "explanation",
                        "evidence_indices", "checked_range", "na_reason",
                    ],
                },
            }
        },
        "required": ["results"],
    }


def _parse_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    # strip markdown fences if a model adds them
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

async def _post_with_retry(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    delay = 2.0
    last: Optional[httpx.Response] = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = await client.post(url, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            if attempt == MAX_RETRIES - 1:
                raise LLMError(f"network error: {e}") from e
            await asyncio.sleep(delay)
            delay *= 2
            continue
        if resp.status_code in (429, 500, 502, 503, 504):
            last = resp
            retry_after = resp.headers.get("retry-after")
            wait = float(retry_after) if retry_after and retry_after.replace(".", "").isdigit() else delay
            await asyncio.sleep(min(wait, 30))
            delay *= 2
            continue
        return resp
    raise LLMError(f"retries exhausted, last status {last.status_code if last else 'n/a'}")


async def _call_gemini(prompt: str) -> Dict[str, Any]:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise LLMError("GEMINI_API_KEY is not set")
    model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.0,
            "responseMimeType": "application/json",
            "responseSchema": _response_schema(),
        },
    }
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await _post_with_retry(
            client,
            GEMINI_URL.format(model=model),
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json=body,
        )
    if resp.status_code != 200:
        raise LLMError(f"Gemini HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise LLMError(f"Gemini unexpected response: {str(data)[:300]}") from e
    usage = data.get("usageMetadata", {})
    return {
        "parsed": _parse_json(text),
        "provider": "gemini",
        "model": model,
        "usage": {
            "input_tokens": usage.get("promptTokenCount"),
            "output_tokens": usage.get("candidatesTokenCount"),
        },
    }


async def _call_groq(prompt: str) -> Dict[str, Any]:
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise LLMError("GROQ_API_KEY is not set")
    model = os.getenv("GROQ_MODEL", DEFAULT_GROQ_MODEL)
    schema_hint = (
        '\n\nReturn a JSON object: {"results": [{"criterion_id": str, "verdict": "yes"|"no"|"na", '
        '"score": number, "explanation": str, "evidence_indices": [int], '
        '"checked_range": str, "na_reason": str}]}'
    )
    body = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": prompt + schema_hint}],
    }
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await _post_with_retry(
            client, GROQ_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=body,
        )
    if resp.status_code != 200:
        raise LLMError(f"Groq HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise LLMError(f"Groq unexpected response: {str(data)[:300]}") from e
    usage = data.get("usage", {})
    return {
        "parsed": _parse_json(text),
        "provider": "groq",
        "model": model,
        "usage": {
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
        },
    }


# ---------------------------------------------------------------------------
# Validation / normalization
# ---------------------------------------------------------------------------

def _normalize(raw: Dict[str, Any], record: Dict[str, Any], checklist: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Make the model output safe for scoring_engine: every criterion present,
    valid verdicts, clamped scores, only existing evidence indices."""
    transcript = record["transcript"]
    n = len(transcript)
    by_id = {}
    for item in raw.get("results", []) or []:
        if isinstance(item, dict) and item.get("criterion_id") is not None:
            by_id[str(item["criterion_id"])] = item

    out: List[Dict[str, Any]] = []
    for c in checklist["criteria"]:
        cid = c["id"]
        item = by_id.get(cid)
        is_scale = c.get("type") == "scale"
        scale_max = _scale_max(c) if is_scale else 1.0

        if item is None:
            out.append({
                "criterion_id": cid, "verdict": "no", "score": 0.0,
                "max_score": scale_max, "explanation": "Модель не вернула оценку по этому критерию.",
                "evidence": [], "checked_range": "", "na_reason": "", "missing": True,
            })
            continue

        verdict = str(item.get("verdict", "no")).lower()
        if verdict not in ("yes", "no", "na"):
            verdict = "no"
        if verdict == "na" and not c.get("allow_na"):
            verdict = "no"  # N/A is not permitted for this criterion

        try:
            score = float(item.get("score", 0) or 0)
        except (TypeError, ValueError):
            score = 0.0
        if verdict == "na":
            score = 0.0
        elif is_scale:
            allowed = [float(s["score"]) for s in (c.get("scale") or [])]
            if allowed:
                score = min(allowed, key=lambda a: abs(a - score))  # snap to a real rubric step
            else:
                score = max(0.0, min(scale_max, score))
        else:
            score = 1.0 if verdict == "yes" else 0.0

        idxs = []
        for i in item.get("evidence_indices", []) or []:
            if isinstance(i, int) and 0 <= i < n and i not in idxs:
                idxs.append(i)
        evidence = [
            {
                "index": i,
                "timestamp": transcript[i].get("timestamp"),
                "speaker": transcript[i].get("speaker"),
                "text": transcript[i].get("text"),
            }
            for i in idxs
        ]

        out.append({
            "criterion_id": cid,
            "verdict": verdict,
            "score": score,
            "max_score": scale_max,
            "explanation": (item.get("explanation") or "").strip(),
            "evidence": evidence,
            "checked_range": (item.get("checked_range") or "").strip(),
            "na_reason": (item.get("na_reason") or "").strip() if verdict == "na" else "",
            "missing": False,
        })
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def evaluate_dialogue(
    record: Dict[str, Any],
    checklist: Dict[str, Any],
    explain_lang: str = "RU",
) -> Dict[str, Any]:
    """Evaluate one dialogue against a checklist.

    Returns:
    {
      "conversation_id": str,
      "checklist_hash": str,
      "provider": "gemini" | "groq",
      "model": str,
      "usage": {"input_tokens": int|None, "output_tokens": int|None},
      "criteria": [ {criterion_id, verdict, score, max_score, explanation,
                     evidence: [{index, timestamp, speaker, text}],
                     checked_range, na_reason, missing}, ... ]
    }
    Scoring (weights, N/A redistribution, rounding, critical flag) is done by
    scoring_engine.py on top of "criteria".
    """
    prompt = build_prompt(record, checklist, explain_lang)

    providers = [_call_gemini, _call_groq]
    # Allow forcing a provider for tests: LLM_PROVIDER=groq
    if os.getenv("LLM_PROVIDER", "").lower() == "groq":
        providers = [_call_groq, _call_gemini]

    errors: List[str] = []
    result: Optional[Dict[str, Any]] = None
    for call in providers:
        try:
            result = await call(prompt)
            break
        except (LLMError, json.JSONDecodeError) as e:
            errors.append(f"{call.__name__}: {e}")
    if result is None:
        raise LLMError("all providers failed: " + " | ".join(errors))

    return {
        "conversation_id": record["conversation_id"],
        "checklist_hash": checklist_hash(checklist),
        "provider": result["provider"],
        "model": result["model"],
        "usage": result["usage"],
        "criteria": _normalize(result["parsed"], record, checklist),
    }


async def evaluate_many(
    records: List[Dict[str, Any]],
    checklist: Dict[str, Any],
    concurrency: int = 2,
    explain_lang: str = "RU",
) -> List[Dict[str, Any]]:
    """Batch helper with a small concurrency cap to stay inside free-tier RPM.
    Failed dialogues come back as {"conversation_id", "error"} instead of raising."""
    sem = asyncio.Semaphore(concurrency)

    async def one(rec: Dict[str, Any]) -> Dict[str, Any]:
        async with sem:
            try:
                return await evaluate_dialogue(rec, checklist, explain_lang)
            except Exception as e:  # keep the batch going
                return {"conversation_id": rec.get("conversation_id"), "error": str(e)}

    return await asyncio.gather(*(one(r) for r in records))


if __name__ == "__main__":
    # Quick smoke test:  python llm_client.py
    demo_checklist = {
        "id": "demo",
        "criteria": [
            {"id": "c01", "name": "Greeting", "prompt": "Did the operator greet the client?",
             "type": "boolean", "allow_na": False, "weight": 50},
            {"id": "c02", "name": "Politeness", "prompt": "Rate the operator's politeness.",
             "type": "scale", "scale_max": 5, "allow_na": True,
             "na_rule": "N/A if the operator wrote nothing.", "weight": 50},
        ],
    }
    demo_record = {
        "conversation_id": "demo-001", "channel": "agent chat", "language": "EN",
        "transcript": [
            {"timestamp": "00:02", "speaker": "client", "text": "Hi, I can't log in."},
            {"timestamp": "00:10", "speaker": "operator", "text": "Hello! Happy to help, let me check your account."},
        ],
    }
    print(json.dumps(asyncio.run(evaluate_dialogue(demo_record, demo_checklist)), ensure_ascii=False, indent=2))