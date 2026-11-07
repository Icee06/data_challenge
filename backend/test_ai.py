import json
import os

import pytest

from ai.evaluator import EvaluationFailed, evaluate_dialogue
from ai.language import detect_language
from ai.validator import ValidationError, find_quote, validate

HERE = os.path.dirname(__file__)
CHECKLIST = json.load(open(os.path.join(HERE, "ai", "checklist.json"), encoding="utf-8"))

DIALOGUE = {"id": "001", "type": "chat", "transcript": [
    {"speaker": "operator", "timestamp": "00:05", "text": "Здравствуйте! Чем могу помочь?"},
    {"speaker": "client", "timestamp": "00:09", "text": "Хочу купить акции."},
    {"speaker": "operator", "timestamp": "00:15", "text": "Гарантирую доходность 50% в год."},
]}


def answer(**over):
    crit = []
    for c in CHECKLIST["criteria"]:
        crit.append({"criterion_id": c["id"], "status": "FAIL", "score": 0,
                     "evidence": None, "reason": "no"})
    crit[0] = {"criterion_id": "01", "status": "PASS", "score": 10,
               "evidence": {"speaker": "x", "timestamp": "x",
                            "quote": "Здравствуйте! Чем могу помочь?"}, "reason": "greeted"}
    base = {"conversation_id": "001", "language": "RU", "criteria": crit,
            "critical_error": False, "critical_error_evidence": [], "summary": "s"}
    base.update(over)
    return base


def test_language():
    assert detect_language("Здравствуйте, чем помочь?") == "RU"
    assert detect_language("Сәлеметсіз бе, қалайсыз? Көмектесейін") == "KZ"
    assert detect_language("Hello, how can I help you today?") == "EN"
    assert detect_language("Привет, I want to buy акции please now") == "MIXED"


def test_find_quote_tolerates_case_and_spaces():
    assert find_quote("здравствуйте!  чем  могу помочь", DIALOGUE["transcript"]) is not None
    assert find_quote("invented text", DIALOGUE["transcript"]) is None


def test_validate_fixes_speaker_and_timestamp():
    r = validate(answer(), CHECKLIST, DIALOGUE)
    ev = r["criteria"][0]["evidence"]
    assert ev["speaker"] == "operator" and ev["timestamp"] == "00:05"


def test_pass_without_real_quote_is_downgraded():
    a = answer()
    a["criteria"][0]["evidence"]["quote"] = "Добрый день, меня зовут Анна"
    r = validate(a, CHECKLIST, DIALOGUE)
    assert r["criteria"][0]["status"] == "FAIL" and r["criteria"][0]["score"] == 0
    assert r["validation_flags"]


def test_critical_error_needs_evidence():
    r = validate(answer(critical_error=True), CHECKLIST, DIALOGUE)
    assert r["critical_error"] is False
    ev = [{"speaker": "operator", "timestamp": "00:15", "quote": "Гарантирую доходность 50% в год."}]
    r = validate(answer(critical_error=True, critical_error_evidence=ev), CHECKLIST, DIALOGUE)
    assert r["critical_error"] is True and r["critical_error_evidence"][0]["timestamp"] == "00:15"


def test_na_rules_and_binary_score_forced():
    a = answer()
    a["criteria"][4] = {"criterion_id": "05", "status": "NA", "score": None, "evidence": None, "reason": ""}
    with pytest.raises(ValidationError):  # 05 has no N/A condition
        validate(a, CHECKLIST, DIALOGUE)
    a = answer()
    a["criteria"][2] = {"criterion_id": "03", "status": "NA", "score": 99, "evidence": None, "reason": ""}
    assert validate(a, CHECKLIST, DIALOGUE)["criteria"][2]["score"] is None
    a = answer()
    a["criteria"][0]["score"] = 3  # model tries an arbitrary score
    assert validate(a, CHECKLIST, DIALOGUE)["criteria"][0]["score"] == 10


def test_scale_values_strict():
    a = answer()
    a["criteria"][3] = {"criterion_id": "04", "status": "PASS", "score": 9, "evidence": None, "reason": ""}
    with pytest.raises(ValidationError):
        validate(a, CHECKLIST, DIALOGUE)
    a["criteria"][3]["score"] = 15
    r = validate(a, CHECKLIST, DIALOGUE)
    assert r["criteria"][3]["score"] == 15 and r["criteria"][3]["status"] == "FAIL"
    assert r["criteria"][3]["weighted_score"] == 15


def test_scale_06_top_step_gives_full_weight():
    a = answer()
    a["criteria"][5] = {"criterion_id": "06", "status": "FAIL", "score": 10, "evidence": None, "reason": ""}
    c = validate(a, CHECKLIST, DIALOGUE)["criteria"][5]
    assert c["status"] == "PASS" and c["max_score"] == 10 and c["weight"] == 15
    assert c["weighted_score"] == 15
    a["criteria"][5]["score"] = 7
    assert validate(a, CHECKLIST, DIALOGUE)["criteria"][5]["weighted_score"] == 10.5


def test_checklist_weights_sum_to_100():
    assert sum(c["weight"] for c in CHECKLIST["criteria"]) == 100
    assert len(CHECKLIST["criteria"]) == 7


def test_missing_criterion_and_bad_json():
    a = answer()
    a["criteria"].pop()
    with pytest.raises(ValidationError):
        validate(a, CHECKLIST, DIALOGUE)
    with pytest.raises(ValidationError):
        validate("not json at all", CHECKLIST, DIALOGUE)


def test_code_fence_is_accepted():
    raw = "```json\n" + json.dumps(answer(), ensure_ascii=False) + "\n```"
    assert validate(raw, CHECKLIST, DIALOGUE)["criteria"][0]["status"] == "PASS"


def test_evaluator_retries_then_succeeds():
    calls = []

    def fake(system, user):
        calls.append(user)
        return "garbage" if len(calls) == 1 else json.dumps(answer(), ensure_ascii=False)

    r = evaluate_dialogue(DIALOGUE, CHECKLIST, llm=fake)
    assert len(calls) == 2 and "rejected" in calls[1]
    assert r["detected_language"] == "RU" and "total" not in r


def test_evaluator_gives_up():
    with pytest.raises(EvaluationFailed):
        evaluate_dialogue(DIALOGUE, CHECKLIST, llm=lambda s, u: "nope", max_attempts=2)
    with pytest.raises(EvaluationFailed):
        evaluate_dialogue({"id": "x", "transcript": []}, CHECKLIST, llm=lambda s, u: "")


def test_gemini_request_and_parsing(monkeypatch):
    from ai import llm_client
    seen = {}

    def fake_post(url, headers, body, timeout):
        seen.update(url=url, headers=headers, body=body)
        return {"candidates": [{"content": {"parts": [{"text": '{"ok": 1}'}]}}],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}}

    monkeypatch.setattr(llm_client, "_post", fake_post)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "gemini-test")
    before = dict(llm_client.USAGE)
    assert llm_client.complete("sys", "usr") == '{"ok": 1}'
    assert seen["url"].endswith("/models/gemini-test:generateContent")
    assert seen["headers"]["x-goog-api-key"] == "k"
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "sys"
    assert llm_client.USAGE["input_tokens"] == before["input_tokens"] + 10


def test_unknown_provider_fails_loudly(monkeypatch):
    from ai import llm_client
    monkeypatch.setenv("LLM_PROVIDER", "google")
    monkeypatch.setenv("LLM_API_KEY", "k")
    with pytest.raises(llm_client.LLMError, match="unknown LLM_PROVIDER"):
        llm_client.complete("s", "u")


def test_whole_scale_scores_are_ints():
    a = answer()
    a["criteria"][3] = {"criterion_id": "04", "status": "PASS", "score": 20.0, "evidence": None, "reason": ""}
    assert isinstance(validate(a, CHECKLIST, DIALOGUE)["criteria"][3]["score"], int)
