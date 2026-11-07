"""Dialogue normalisation: one format for chats and calls, quote lookup, fragments.

Original timestamps are never changed. Each message only gets two extra fields:
  index       position in the transcript (what evidence links point to)
  offset_sec  seconds from the start, used to seek the audio player
Chats use MM:SS, so offset = that value. Calls use ISO 8601, so offset = distance
from the first message. ASSUMPTION: the recording starts at the first message;
check this on a real call and shift here if the audio has a lead-in.
"""
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

_CLOCK = re.compile(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{2})(?:\.\d+)?\s*$")  # MM:SS or HH:MM:SS


def channel_type(record: Dict[str, Any]) -> str:
    ch = (record.get("channel") or "").lower()
    return "call" if ("call" in ch or "voice" in ch or record.get("audio_url")) else "chat"


def _is_clock(ts: Any) -> bool:
    return ts is not None and bool(_CLOCK.match(str(ts)))


def _seconds(ts: Any) -> Optional[float]:
    if ts is None or str(ts).strip() == "":
        return None
    s = str(ts).strip()
    m = _CLOCK.match(s)
    if m:
        return int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def normalize_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Raw organizer record -> same record with `type` and per-message `index`/`offset_sec`."""
    transcript = record.get("transcript") or []
    secs = [_seconds(u.get("timestamp")) for u in transcript]
    absolute = any(u.get("timestamp") and not _is_clock(u["timestamp"]) for u in transcript)
    base = next((s for s in secs if s is not None), 0.0) if absolute else 0.0
    out: List[Dict[str, Any]] = []
    for i, (u, s) in enumerate(zip(transcript, secs)):
        item = dict(u)
        item["index"] = i
        item["offset_sec"] = None if s is None else round(max(s - base, 0.0), 3)
        out.append(item)
    res = dict(record)
    res["transcript"] = out
    res["type"] = channel_type(record)
    return res


def to_ai_dialogue(record: Dict[str, Any]) -> Dict[str, Any]:
    """The shape ai.evaluator.evaluate_dialogue() takes (same as run_dev.normalize())."""
    return {
        "id": str(record.get("conversation_id")),
        "type": channel_type(record),
        "language": record.get("language"),
        "audio_url": record.get("audio_url"),
        "transcript": [
            {"speaker": u.get("speaker"), "timestamp": u.get("timestamp"), "text": u.get("text") or ""}
            for u in (record.get("transcript") or [])
        ],
    }


def _norm(text: str) -> str:
    text = (text or "").casefold().replace("ё", "е")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text)).strip()


def locate_quote(quote: str, transcript: List[Dict[str, Any]], timestamp: Optional[str] = None,
                 speaker: Optional[str] = None) -> Optional[int]:
    """Index of the message containing `quote` (case, spacing and punctuation tolerant).
    When several messages match, prefer the one with the same timestamp and speaker."""
    nq = _norm(quote)
    if not nq:
        return None
    hits = [i for i, u in enumerate(transcript) if nq in _norm(u.get("text") or "")]
    if not hits:
        return None
    for i in hits:
        u = transcript[i]
        if (timestamp is None or u.get("timestamp") == timestamp) and (speaker is None or u.get("speaker") == speaker):
            return i
    return hits[0]


def fragment(transcript: List[Dict[str, Any]], index: int, radius: int = 2) -> List[Dict[str, Any]]:
    """Messages around `index` for the reviewer; the evidence message is flagged."""
    lo, hi = max(0, index - radius), min(len(transcript), index + radius + 1)
    return [{**dict(transcript[i]), "index": i, "is_evidence": i == index} for i in range(lo, hi)]


def whole_range(transcript: List[Dict[str, Any]]) -> str:
    ts = [u.get("timestamp") for u in transcript if u.get("timestamp")]
    return f"весь диалог ({ts[0]}–{ts[-1]})" if ts else "весь диалог"
