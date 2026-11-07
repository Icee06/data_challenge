"""Thin LLM client. Standard library only (no installs needed).

Env vars (put them in backend/.env, never in Git):
  LLM_PROVIDER  anthropic | openai   (openai = any OpenAI-compatible endpoint)
  LLM_API_KEY
  LLM_MODEL     e.g. claude-sonnet-5-5
  LLM_BASE_URL  optional override
  LLM_TIMEOUT   seconds, default 90
"""
import json
import os
import time
import urllib.error
import urllib.request


class LLMError(Exception):
    pass


# Running totals for docs/cost.md (every call, retries included).
USAGE = {"calls": 0, "input_tokens": 0, "output_tokens": 0}


def _post(url, headers, body, timeout):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def complete(system: str, user: str, max_tokens: int = 4000, retries: int = 2) -> str:
    """Return the raw text answer. Retries network errors / 429 / 5xx with backoff."""
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    key = os.getenv("LLM_API_KEY")
    model = os.getenv("LLM_MODEL", "claude-sonnet-5-5")
    timeout = float(os.getenv("LLM_TIMEOUT", "90"))
    if not key:
        raise LLMError("LLM_API_KEY is not set")

    if provider == "anthropic":
        url = os.getenv("LLM_BASE_URL", "https://api.anthropic.com/v1/messages")
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {
            "model": model, "max_tokens": max_tokens, "temperature": 0,
            "system": system, "messages": [{"role": "user", "content": user}],
        }
    else:
        url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1/chat/completions")
        headers = {"Authorization": f"Bearer {key}", "content-type": "application/json"}
        body = {
            "model": model, "temperature": 0, "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }

    last = None
    for attempt in range(retries + 1):
        try:
            data = _post(url, headers, body, timeout)
            USAGE["calls"] += 1
            u = data.get("usage", {})
            USAGE["input_tokens"] += u.get("input_tokens", u.get("prompt_tokens", 0))
            USAGE["output_tokens"] += u.get("output_tokens", u.get("completion_tokens", 0))
            if provider == "anthropic":
                return "".join(b.get("text", "") for b in data["content"])
            return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            last = LLMError(f"HTTP {e.code}: {e.read().decode()[:300]}")
            if e.code not in (429, 500, 502, 503, 529):
                raise last
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as e:
            last = LLMError(f"{type(e).__name__}: {e}")
        time.sleep(2 ** attempt)
    raise last
