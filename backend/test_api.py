"""API + service tests. No network, no real LLM: the AI is replaced by fakes that return the same
shape as ai.evaluator.evaluate_dialogue (ids "01".."07", PASS/FAIL/NA). Run from backend/:  pytest -q

Hand-calculated numbers (default checklist, weights 10/15/20/20/15/15/5):
  contact yes 10 | needs N/A | identification N/A | consultation 15 | empathy yes 15 | dialogue 7 -> 10.5
  | closing no 0.  Applicable weight = 10+20+15+15+5 = 65, earned = 10+15+15+10.5+0 = 50.5
  total = 50.5 / 65 * 100 = 77.69
  reviewer changes closing to yes (+5):  55.5 / 65 * 100 = 85.38   (delta 7.69)
"""
import copy
import sys

import pytest
from fastapi.testclient import TestClient

import db
import dialogue_service as ds
import evaluation_service
import main
from schemas import ServiceError

RECORDS = [
    {"conversation_id": "c-ru-1", "channel": "agent chat", "language": "RU", "audio_url": None,
     "transcript": [
         {"timestamp": "00:02", "speaker": "operator", "text": "Здравствуйте! Меня зовут Айгерим, чем могу помочь?"},
         {"timestamp": "00:10", "speaker": "client", "text": "Подскажите, сколько стоит тариф Premium?"},
         {"timestamp": "00:20", "speaker": "operator", "text": "Тариф Premium стоит 5 000 тг в месяц."},
     ]},
    {"conversation_id": "v-kz-1", "channel": "voice call", "language": "KZ", "audio_url": "https://x.test/a.mp3",
     "transcript": [
         {"timestamp": "2026-05-01T10:00:05+05:00", "speaker": "operator", "text": "Сәлеметсіз бе! Мен Айгерімін."},
         {"timestamp": "2026-05-01T10:00:15+05:00", "speaker": "client", "text": "Тарифтер туралы сұрағым бар."},
         {"timestamp": "2026-05-01T10:01:05+05:00", "speaker": "operator", "text": "Premium тарифі айына 5000 теңге."},
     ]},
    {"conversation_id": "e-en-1", "channel": "agent chat", "language": "EN", "audio_url": None,
     "transcript": [
         {"timestamp": "00:01", "speaker": "operator", "text": "Hello, this is Anna. How can I help?"},
         {"timestamp": "00:07", "speaker": "client", "text": "What is the fee for withdrawals?"},
     ]},
]


# ----------------------------------------------------------------- fake AIs
def _ev(msg):
    return {"speaker": msg["speaker"], "timestamp": msg["timestamp"], "quote": msg["text"]}


def fake_ai_numeric(dialogue, checklist):
    """Mimics the AI package: ids 01..07, PASS/FAIL/NA, no total."""
    ops = [u for u in dialogue["transcript"] if u["speaker"] == "operator"]
    last = ops[-1]
    row = lambda cid, status, score, ev, reason: {"criterion_id": cid, "status": status, "score": score,
                                                  "evidence": ev, "reason": reason}
    return {
        "criteria": [
            row("01", "PASS", 10, _ev(ops[0]), "greeted and introduced"),
            row("02", "NA", None, None, "the need was clear from the first message"),
            row("03", "NA", None, None, "general tariff question"),
            row("04", "FAIL", 15, _ev(last), "main question solved, minor detail missed"),
            row("05", "PASS", 15, None, "polite"),
            row("06", "FAIL", 7, None, "one long pause without warning"),
            row("07", "FAIL", 0, None, "chat ended without a goodbye"),
        ],
        "critical_error": False, "critical_error_evidence": [], "summary": "ok",
        "detected_language": dialogue.get("language"), "validation_flags": [],
    }


def fake_generic(dialogue, checklist):
    """Answers perfectly for ANY checklist, using the ids it is given (checklist swap test)."""
    crit = []
    for c in checklist["criteria"]:
        if c["type"] == "flag":
            continue
        score = max(s["score"] for s in c["scale"]) if c["type"] == "scale" else None
        crit.append({"criterion_id": c["id"], "status": "PASS", "score": score, "evidence": None, "reason": "fine"})
    return {"criteria": crit, "critical_error": False, "critical_error_evidence": [], "summary": "generic"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(main, "CACHE_DIR", tmp_path / "cache")
    main._store.clear()

    async def fake_fetch(group_id):
        return copy.deepcopy(RECORDS)

    monkeypatch.setattr(main, "fetch_from_organizer", fake_fetch)
    calls = []

    def evaluator(dialogue, checklist):
        calls.append(dialogue["id"])
        return fake_ai_numeric(dialogue, checklist)

    evaluation_service.set_evaluator(evaluator)
    c = TestClient(main.app)
    c.calls = calls
    yield c
    evaluation_service.set_evaluator(None)
    main._store.clear()


def criterion(view, cid):
    return next(c for c in view["criteria"] if c["id"] == cid)


# ------------------------------------------------------------ routing & data
def test_batch_route_is_not_swallowed_by_single_route(client):
    """Regression: /api/evaluate/batch used to match /api/evaluate/{conversation_id}."""
    res = client.post("/api/evaluate/batch", json={"group_id": "DEV"})
    assert res.status_code == 200
    assert res.json()["total_requested"] == 3


def test_health_and_aliases(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/api/health").status_code == 200
    a = client.get("/api/dialogues/c-ru-1").json()
    b = client.get("/api/dialogue/c-ru-1").json()
    assert a == b


def test_dialogue_normalisation_chat_and_call(client):
    chat = client.get("/api/dialogues/c-ru-1").json()
    assert chat["type"] == "chat"
    assert [m["offset_sec"] for m in chat["transcript"]] == [2, 10, 20]
    assert chat["transcript"][0]["timestamp"] == "00:02"  # original label untouched
    assert [m["index"] for m in chat["transcript"]] == [0, 1, 2]
    call = client.get("/api/dialogues/v-kz-1").json()
    assert call["type"] == "call" and call["audio_url"]
    assert [m["offset_sec"] for m in call["transcript"]] == [0, 10, 60]
    assert call["transcript"][0]["timestamp"].startswith("2026-05-01T10:00:05")


def test_registry_filters(client):
    ids = lambda r: sorted(d["conversation_id"] for d in r.json()["dialogues"])
    assert ids(client.get("/api/dialogues")) == ["c-ru-1", "e-en-1", "v-kz-1"]
    assert ids(client.get("/api/dialogues?language=kz")) == ["v-kz-1"]
    assert ids(client.get("/api/dialogues?channel=call")) == ["v-kz-1"]
    assert ids(client.get("/api/dialogues?channel=chat")) == ["c-ru-1", "e-en-1"]
    assert ids(client.get("/api/dialogues?q=EN-1")) == ["e-en-1"]
    assert client.get("/api/dialogues/nope").status_code == 404


# --------------------------------------------------------------- evaluation
def test_evaluate_scores_with_na_redistribution(client):
    res = client.post("/api/evaluate/c-ru-1")
    assert res.status_code == 200
    v = res.json()
    assert v["total"] == 77.69 and v["original_total"] == 77.69
    assert v["critical_error"] is False and v["cached"] is False
    assert v["applicable_weight"] == 65
    assert criterion(v, "needs")["applicable"] is False
    assert criterion(v, "consultation")["points"] == pytest.approx(23.08)  # 15/20 * 20 * 100/65
    assert criterion(v, "dialogue")["answer"] == 7
    assert v["checklist_version"] == 1
    assert {s["id"] for s in v["sections"]} == {"start", "safety", "flow", "gross"}


def test_evidence_links_to_message_and_na_explains_range(client):
    v = client.post("/api/evaluate/c-ru-1").json()
    ev = criterion(v, "contact")["evidence"][0]
    assert ev["message_index"] == 0 and ev["timestamp"] == "00:02" and ev["speaker"] == "operator"
    assert criterion(v, "consultation")["evidence"][0]["message_index"] == 2
    na = criterion(v, "needs")
    assert na["answer"] == "na" and na["explanation"] and na["checked_range"].startswith("весь диалог")
    skipped = criterion(v, "closing")
    assert skipped["answer"] == "no" and skipped["checked_range"]  # skipped action -> checked span


def test_call_evidence_points_into_a_call(client):
    v = client.post("/api/evaluate/v-kz-1").json()
    assert criterion(v, "contact")["evidence"][0]["message_index"] == 0
    assert v["language"] == "KZ"


def test_cache_force_refresh_and_history(client):
    client.post("/api/evaluate/c-ru-1")
    second = client.post("/api/evaluate/c-ru-1").json()
    assert second["cached"] is True and len(client.calls) == 1  # no second AI call
    forced = client.post("/api/evaluate/c-ru-1?force_refresh=true").json()
    assert forced["cached"] is False and len(client.calls) == 2
    assert forced["evaluation_id"] != second["evaluation_id"]  # history kept
    assert len(db.evaluation_history("DEV", "c-ru-1")) == 2
    assert client.get("/api/evaluation/c-ru-1").json()["evaluation_id"] == forced["evaluation_id"]


def test_get_evaluation_404_before_evaluating(client):
    assert client.get("/api/evaluation/c-ru-1").status_code == 404


def test_gross_error_is_flagged_not_scored(client):
    def gross(d, c):
        r = fake_ai_numeric(d, c)
        op = d["transcript"][2]
        r["critical_error"] = True
        r["critical_error_evidence"] = [_ev(op)]
        return r

    evaluation_service.set_evaluator(gross)
    v = client.post("/api/evaluate/c-ru-1").json()
    assert v["critical_error"] is True and v["total"] == 77.69  # flag does not change the total
    g = criterion(v, "gross_errors")
    assert g["violation"] is True and g["points"] is None and g["evidence"][0]["message_index"] == 2


def test_invalid_ai_answer_is_never_saved(client):
    def broken(d, c):
        r = fake_ai_numeric(d, c)
        r["criteria"].pop()  # criterion 07 missing
        return r

    evaluation_service.set_evaluator(broken)
    res = client.post("/api/evaluate/c-ru-1")
    assert res.status_code == 502 and any("closing" in p for p in res.json()["problems"])
    assert client.get("/api/evaluation/c-ru-1").status_code == 404

    def off_scale(d, c):
        r = fake_ai_numeric(d, c)
        r["criteria"][3]["score"] = 12  # not a valid step of criterion 04
        return r

    evaluation_service.set_evaluator(off_scale)
    assert client.post("/api/evaluate/c-ru-1").status_code == 502


def test_ai_crash_and_missing_ai_module(client, monkeypatch):
    def boom(d, c):
        raise RuntimeError("quota exceeded")

    evaluation_service.set_evaluator(boom)
    res = client.post("/api/evaluate/c-ru-1")
    assert res.status_code == 502 and "quota exceeded" in res.json()["detail"]

    evaluation_service.set_evaluator(None)
    monkeypatch.setitem(sys.modules, "ai.evaluator", None)  # simulate: package not importable
    assert client.post("/api/evaluate/c-ru-1").status_code == 503


def test_batch_reports_partial_failures_and_summary(client):
    def flaky(d, c):
        if d["id"] == "e-en-1":
            raise RuntimeError("timeout")
        return fake_ai_numeric(d, c)

    evaluation_service.set_evaluator(flaky)
    res = client.post("/api/evaluate/batch", json={"group_id": "DEV", "concurrency_limit": 2}).json()
    assert res["successful"] == 2 and res["failed_count"] == 1 and res["errors"][0]["conversation_id"] == "e-en-1"
    assert res["average_score"] == 77.69 and res["newly_evaluated"] == 2
    evaluation_service.set_evaluator(fake_ai_numeric)  # the AI recovers: only the failed one is re-run
    again = client.post("/api/evaluate/batch", json={"group_id": "DEV"}).json()
    assert again["cached_retrieved"] == 2 and again["newly_evaluated"] == 1  # only the failed one is re-run
    listing = client.get("/api/dialogues?status=evaluated").json()
    assert listing["total"] == 3 and listing["dialogues"][0]["evaluation"]["total"] == 77.69
    assert client.get("/api/dialogues?status=pending").json()["total"] == 0


# ------------------------------------------------------------------ appeals
def _appeal(client, cid="closing", comment="Оператор попрощался в последней реплике"):
    ev = client.post("/api/evaluate/c-ru-1").json()
    return ev, client.post("/api/appeal", json={"evaluation_id": ev["evaluation_id"], "criterion_id": cid,
                                                 "comment": comment, "author": "operator-1"})


def test_appeal_change_recalculates_and_keeps_history(client):
    ev, res = _appeal(client)
    assert res.status_code == 200
    appeal = res.json()
    assert appeal["status"] == "open" and appeal["criterion"]["answer"] == "no"

    done = client.post(f"/api/appeal/{appeal['id']}/decision", json={
        "decision": "change", "answer": "yes", "reviewer": "QA-1",
        "reason": "В последней реплике есть прощание"})
    assert done.status_code == 200
    out = done.json()
    v = out["evaluation"]
    assert v["total"] == 85.38 and v["original_total"] == 77.69 and v["total_delta"] == 7.69
    assert v["changed"] == ["closing"]
    c = criterion(v, "closing")
    assert (c["answer"], c["original_answer"], c["source"], c["reviewer"]) == ("yes", "no", "reviewer", "QA-1")
    assert c["appeal"]["status"] == "changed"
    assert out["appeal"]["status"] == "changed" and out["appeal"]["decision_answer"] == "yes"
    # the stored AI answer is untouched; the card still shows both numbers after a reload
    again = client.get("/api/evaluation/c-ru-1").json()
    assert again["total"] == 85.38 and again["original_total"] == 77.69
    assert client.get(f"/api/evaluations/{ev['evaluation_id']}").json()["total"] == 85.38


def test_appeal_confirm_keeps_total(client):
    ev, res = _appeal(client)
    out = client.post(f"/api/appeal/{res.json()['id']}/decision",
                      json={"decision": "confirm", "reason": "Прощания в чате нет"}).json()
    assert out["appeal"]["status"] == "confirmed"
    assert out["evaluation"]["total"] == 77.69 and out["evaluation"]["changed"] == []


def test_appeal_to_na_redistributes_weights(client):
    ev, res = _appeal(client, cid="closing")
    out = client.post(f"/api/appeal/{res.json()['id']}/decision",
                      json={"decision": "change", "answer": "na", "reason": "Чат оборвался"}).json()
    # applicable = 60, earned = 10 + 15 + 15 + 10.5 = 50.5 -> 84.17
    assert out["evaluation"]["total"] == 84.17


def test_appeal_validation(client):
    ev, first = _appeal(client)
    eid, aid = ev["evaluation_id"], first.json()["id"]
    post = lambda **b: client.post("/api/appeal", json={"evaluation_id": eid, "criterion_id": "closing",
                                                        "comment": "x", **b})
    assert post().status_code == 409  # already one open for this criterion
    assert post(criterion_id="nope").status_code == 422
    assert post(criterion_id="empathy", comment="  ").status_code == 422
    assert client.post("/api/appeal", json={"evaluation_id": 999, "criterion_id": "closing",
                                            "comment": "x"}).status_code == 404

    dec = lambda **b: client.post(f"/api/appeal/{aid}/decision", json=b)
    assert dec(decision="change", answer="yes", reason=" ").status_code == 422  # reason required
    assert dec(decision="change", reason="r").status_code == 422  # answer required
    assert dec(decision="change", answer="maybe", reason="r").status_code == 422  # invalid answer
    assert dec(decision="change", answer="no", reason="r").status_code == 422  # same as current
    assert dec(decision="confirm", reason="ok").status_code == 200
    assert dec(decision="confirm", reason="again").status_code == 409  # already decided


def test_appeal_on_scale_criterion_and_not_allowed_na(client):
    ev = client.post("/api/evaluate/c-ru-1").json()
    ap = client.post("/api/appeal", json={"evaluation_id": ev["evaluation_id"], "criterion_id": "dialogue",
                                          "comment": "Пауза была с предупреждением"}).json()
    bad = client.post(f"/api/appeal/{ap['id']}/decision", json={"decision": "change", "answer": "na", "reason": "r"})
    assert bad.status_code == 422  # criterion 06 has no N/A
    ok = client.post(f"/api/appeal/{ap['id']}/decision", json={"decision": "change", "answer": 10, "reason": "r"})
    # dialogue 7 -> 10: 10 + 15 + 15 + 15 + 0 = 55 of 65 -> 84.62
    assert ok.json()["evaluation"]["total"] == 84.62


def test_reviewer_queue_has_fragment_and_comment(client):
    ev = client.post("/api/evaluate/c-ru-1").json()
    client.post("/api/appeal", json={"evaluation_id": ev["evaluation_id"], "criterion_id": "consultation",
                                     "comment": "Комиссию назвали выше"})
    q = client.get("/api/appeals").json()
    assert q["total"] == 1
    a = q["appeals"][0]
    assert a["comment"] == "Комиссию назвали выше" and a["criterion"]["name"]
    assert [m["index"] for m in a["fragment"]] == [0, 1, 2]
    assert [m["is_evidence"] for m in a["fragment"]] == [False, False, True]
    assert client.get(f"/api/appeals/{ev['evaluation_id']}").json()["appeals"][0]["id"] == a["id"]
    assert client.get("/api/appeals?status=confirmed").json()["total"] == 0
    assert client.get("/api/dialogues").json()["dialogues"][0]["evaluation"]["appeals_open"] in (0, 1)


# ---------------------------------------------------------------- checklist
def test_checklist_swap_creates_version_and_reevaluates(client):
    v1 = client.post("/api/evaluate/c-ru-1").json()
    assert v1["checklist_version"] == 1 and v1["total"] == 77.69
    ap = client.post("/api/appeal", json={"evaluation_id": v1["evaluation_id"], "criterion_id": "closing",
                                          "comment": "c"}).json()
    client.post(f"/api/appeal/{ap['id']}/decision", json={"decision": "change", "answer": "yes", "reason": "r"})

    got = client.get("/api/checklist").json()
    assert got["version"] == 1 and len(got["checklist"]["criteria"]) == 8
    cl = got["checklist"]
    cl["criteria"] = [c for c in cl["criteria"] if c["id"] != "closing"] + [{
        "id": "compliance", "name": "Соблюдение скрипта", "type": "scale", "weight": 5, "section": "flow",
        "description": "Оператор следует скрипту.",
        "scale": [{"score": 5, "description": "полностью"}, {"score": 3, "description": "частично"},
                  {"score": 0, "description": "нет"}]}]
    cl["change_note"] = "replace closing with script compliance"
    saved = client.put("/api/checklist", json=cl)
    assert saved.status_code == 200 and saved.json()["version"] == 2 and saved.json()["warnings"] == []
    assert client.get("/api/checklist").json()["version"] == 2

    evaluation_service.set_evaluator(lambda d, c: fake_generic(d, c)
                                     if any(x["id"] == "compliance" for x in c["criteria"]) else fake_ai_numeric(d, c))
    batch = client.post("/api/evaluate/batch", json={"group_id": "DEV"}).json()
    assert batch["checklist_version"] == 2 and batch["newly_evaluated"] == 3 and batch["average_score"] == 100.0
    v2 = client.get("/api/evaluation/c-ru-1").json()
    assert v2["checklist_version"] == 2 and "compliance" in [c["id"] for c in v2["criteria"]]

    old = client.get("/api/evaluation/c-ru-1?checklist_version=1").json()  # history untouched
    assert old["checklist_version"] == 1 and old["total"] == 85.38 and old["original_total"] == 77.69
    assert [v["version"] for v in client.get("/api/checklist/versions").json()["versions"]] == [2, 1]
    assert client.get("/api/checklist/versions/1").json()["checklist"]["criteria"][-1]["id"] == "gross_errors"
    assert client.get("/api/checklist/versions/99").status_code == 404


def test_checklist_validation_and_weight_warning(client):
    cl = client.get("/api/checklist").json()["checklist"]
    dup = copy.deepcopy(cl)
    dup["criteria"][1]["id"] = dup["criteria"][0]["id"]
    assert client.put("/api/checklist", json=dup).status_code == 422
    zero = copy.deepcopy(cl)
    for c in zero["criteria"]:
        c["weight"] = 0
    assert client.put("/api/checklist", json=zero).status_code == 422
    heavy = copy.deepcopy(cl)
    heavy["criteria"][0]["weight"] = 30
    saved = client.put("/api/checklist", json=heavy).json()
    assert any("Weights sum to 120" in w for w in saved["warnings"])
    assert client.get("/api/checklist").json()["version"] == 2


# -------------------------------------------------------- helper unit tests
def test_locate_quote_is_tolerant_and_prefers_matching_timestamp():
    tr = [{"speaker": "operator", "timestamp": "00:02", "text": "Здравствуйте!  Меня зовут Айгерим."},
          {"speaker": "operator", "timestamp": "00:09", "text": "Спасибо."},
          {"speaker": "operator", "timestamp": "00:30", "text": "Спасибо."}]
    assert ds.locate_quote("здравствуйте меня зовут айгерим", tr) == 0
    assert ds.locate_quote("такого нет", tr) is None
    assert ds.locate_quote("Спасибо", tr) == 1
    assert ds.locate_quote("Спасибо", tr, timestamp="00:30") == 2
    assert ds.locate_quote("", tr) is None


def test_id_resolution_by_exact_id_or_position():
    import scoring_engine as se
    cl = se.default_checklist()
    r = evaluation_service._resolve_id
    assert r("contact", cl) == "contact" and r("01", cl) == "contact" and r("07", cl) == "closing"
    assert r("08", cl) == "gross_errors" and r("09", cl) is None and r("zzz", cl) is None
