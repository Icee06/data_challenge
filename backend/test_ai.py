import json
import os

import pytest

from ai.evaluator import EvaluationFailed, evaluate_dialogue
from ai.language import detect_language
from ai.validator import ValidationError, find_quote, validate

HERE = os.path.dirname(__file__)
CHECKLIST = json.load(open(os.path.join(HERE, "ai", "checklist.template.json"), encoding="utf-8"))

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
    a["criteria"][1] = {"criterion_id": "02", "status": "NA", "score": None, "evidence": None, "reason": ""}
    with pytest.raises(ValidationError):  # 02 has no N/A condition
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
    a["criteria"][3]["score"] = 7.5
    r = validate(a, CHECKLIST, DIALOGUE)
    assert r["criteria"][3]["score"] == 7.5 and r["criteria"][3]["status"] == "FAIL"


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
