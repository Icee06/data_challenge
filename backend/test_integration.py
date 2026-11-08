"""The real AI package (backend/ai/) together with the backend checklist, the database and scoring.

test_api.py replaces the whole AI module with fakes, so it cannot catch a format mismatch between
what the backend sends (evaluation_service.checklist_for_ai) and what ai/prompt.py + ai/validator.py
expect. Here only the LLM call is fake: prompt building, validation, id mapping and scoring are real.
No network. Run from backend/:  pytest -q
"""
import asyncio
import json

import pytest

import checklist_service
import db
import evaluation_service as ev
import main
import scoring_engine as se
from ai.evaluator import evaluate_dialogue
from ai.prompt import build_system_prompt

RECORD = {"conversation_id": "t-1", "channel": "agent chat", "language": "RU", "audio_url": None, "transcript": [
    {"timestamp": "00:00", "speaker": "client", "text": "Где посмотреть мой тариф?"},
    {"timestamp": "00:02", "speaker": "bot", "text": "Соединяю с оператором."},
    {"timestamp": "00:40", "speaker": "operator", "text": "Здравствуйте! Меня зовут Алина. Тариф во вкладке «Кабинет»."},
    {"timestamp": "00:50", "speaker": "client", "text": "Нашла, спасибо."},
    {"timestamp": "01:00", "speaker": "operator", "text": "Пожалуйста. Хорошего вечера!"},
]}


def fake_llm_for(checklist, critical=False):
    """Answers every criterion the AI package was given, in the shape its prompt asks for."""
    def llm(system, user):
        assert "No critical error defined" not in system  # the gross-error rule must reach the prompt
        crit = []
        for c in checklist["criteria"]:
            if c["type"] == "scale":
                crit.append({"criterion_id": c["id"], "status": "PASS", "score": max(c["scale"]),
                             "evidence": None, "reason": "ok"})
            elif c["id"] == "needs":
                crit.append({"criterion_id": c["id"], "status": "NA", "score": None, "evidence": None,
                             "reason": "the question was clear"})
            else:
                crit.append({"criterion_id": c["id"], "status": "PASS", "score": c["weight"], "reason": "ok",
                             "evidence": {"speaker": "operator", "timestamp": "00:40", "quote": "Меня зовут Алина"}})
        evidence = [{"speaker": "operator", "timestamp": "01:00", "quote": "Хорошего вечера"}] if critical else []
        return json.dumps({"criteria": crit, "critical_error": critical, "critical_error_evidence": evidence,
                           "summary": "s"}, ensure_ascii=False)
    return llm


@pytest.fixture()
def real_ai(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    ev.set_evaluator(lambda d, c: evaluate_dialogue(d, c, llm=fake_llm_for(c)))
    yield
    ev.set_evaluator(None)


def by_id(view, cid):
    return next(c for c in view["criteria"] if c["id"] == cid)


def test_checklist_sent_to_the_ai_has_the_ai_format():
    sent = ev.checklist_for_ai(se.default_checklist())
    assert [c["id"] for c in sent["criteria"]] == [
        "contact", "needs", "identification", "consultation", "empathy", "dialogue", "closing"]
    assert sent["criteria"][3]["scale"] == [0, 5, 10, 15, 20]
    assert sent["criteria"][3]["scale_steps"]["20"].startswith("Все существенные")
    assert sent["criteria"][0]["na_condition"] is None and sent["criteria"][1]["na_condition"]
    assert "оскорбление" in sent["critical_error"]["description"]
    prompt = build_system_prompt(sent, "RU")
    assert "allowed scores exactly: 0, 5, 10, 15, 20" in prompt
    assert "[gross_errors]" not in prompt and "No critical error defined" not in prompt


def test_real_ai_package_end_to_end(real_ai):
    v = asyncio.run(ev.evaluate("DEV", RECORD))
    assert v["total"] == 100.0 and v["critical_error"] is False
    assert by_id(v, "needs")["applicable"] is False
    assert by_id(v, "contact")["evidence"][0]["message_index"] == 2
    assert by_id(v, "consultation")["answer"] == 20 and by_id(v, "dialogue")["answer"] == 10


def test_gross_error_reaches_the_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    ev.set_evaluator(lambda d, c: evaluate_dialogue(d, c, llm=fake_llm_for(c, critical=True)))
    try:
        v = asyncio.run(ev.evaluate("DEV", RECORD))
    finally:
        ev.set_evaluator(None)
    g = by_id(v, "gross_errors")
    assert v["critical_error"] is True and g["violation"] is True and g["evidence"][0]["message_index"] == 4
    assert v["total"] == 100.0  # the flag never changes the total


def test_replaced_criterion_after_gross_errors(real_ai):
    """The control check: replace a criterion in the UI, the new one is added at the end of the list."""
    data = se.default_checklist().model_dump()
    data["criteria"] = [c for c in data["criteria"] if c["id"] != "closing"] + [{
        "id": "compliance", "name": "Соблюдение скрипта", "type": "scale", "weight": 5, "section": "flow",
        "description": "Оператор следует скрипту.",
        "scale": [{"score": 5, "description": "полностью"}, {"score": 3, "description": "частично"},
                  {"score": 0, "description": "нет"}]}]
    checklist_service.save_new_version(se.Checklist.model_validate(data), note="control check")
    v = asyncio.run(ev.evaluate("DEV", RECORD))
    assert v["checklist_version"] == 2
    assert by_id(v, "compliance")["answer"] == 5 and "closing" not in [c["id"] for c in v["criteria"]]
    assert v["total"] == 100.0


def test_criterion_without_weight_is_rejected(tmp_path, monkeypatch):
    """'Add criterion' in the UI starts at weight 0; saving it like that must fail with a clear message."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    data = se.default_checklist().model_dump()
    data["criteria"].append({"id": "new", "name": "Новый критерий", "type": "yes_no", "weight": 0, "description": "x"})
    with pytest.raises(checklist_service.ServiceError) as e:
        checklist_service.save_new_version(se.Checklist.model_validate(data))
    assert e.value.status_code == 422 and "Новый критерий" in e.value.message


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_cache_saved_with_curl_or_powershell_is_read(tmp_path, monkeypatch, encoding):
    raw = {"error": "", "isSuccess": True, "data": {"dataset_group_id": "DEV", "total_records": 1, "records": [RECORD]}}
    (tmp_path / "DEV.json").write_text(json.dumps(raw, ensure_ascii=False), encoding=encoding)
    monkeypatch.setattr(main, "CACHE_DIR", tmp_path)
    main._store.clear()
    try:
        assert asyncio.run(main.ensure_loaded("DEV")) == "disk_cache"
        assert list(main._store["DEV"]) == ["t-1"]
    finally:
        main._store.clear()
