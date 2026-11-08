"""Tests for export_results.py (results.json, JSON 1.0). Run from backend/:  pytest -q
Reuses the fixtures of test_api.py: fake AI, 3 dialogues (c-ru-1 chat, v-kz-1 call, e-en-1 chat)."""
import copy
import json

import pytest

import evaluation_service
import export_results as ex
from test_api import client, fake_ai_numeric, fake_generic, _ev  # noqa: F401  (client is a fixture)

TEAM = dict(team_name="Team X", mode="external_api", model_name="test-model")


def payload(client, **kw):
    return ex.build_payload(client, "DEV", **{**TEAM, **kw})


def rec(p, cid):
    return next(r for r in p["results"] if r["conversation_id"] == cid)


def val(r, code):
    return next(c for c in r["criteria"] if c["criterion_id"] == code)


def test_record_matches_the_appendix_format(client):
    client.post("/api/evaluate/c-ru-1")
    r = rec(payload(client), "c-ru-1")
    assert r["status"] == "ok" and [c["criterion_id"] for c in r["criteria"]] == ex.CODES
    # values: true/false/null for yes/no/N/A, the scale step for scale criteria
    assert [c["value"] for c in r["criteria"]] == [True, None, None, 15, True, 7, False, False]
    assert r["score"] == {"final_score": 77.69, "critical_flag": False}
    # evidence_turns are 1-based over the ORIGINAL transcript array
    assert val(r, "contact")["evidence_turns"] == [1]
    assert val(r, "consultation")["evidence_turns"] == [3]
    # no quoted evidence -> operator turns that were checked (messages 1 and 3 are the operator's)
    assert val(r, "empathy")["evidence_turns"] == [1, 3] and val(r, "critical")["evidence_turns"] == [1, 3]
    assert all(c["rationale"].strip() for c in r["criteria"])
    assert ex.validate_payload(payload(client)) == []


def test_not_evaluated_dialogue_is_an_error_record_in_api_order(client):
    client.post("/api/evaluate/c-ru-1")
    p = payload(client)
    assert [r["conversation_id"] for r in p["results"]] == ["c-ru-1", "v-kz-1", "e-en-1"]  # same order as the API
    assert rec(p, "e-en-1") == {"conversation_id": "e-en-1", "status": "error", "criteria": [], "score": None,
                                "error": ex.NOT_EVALUATED}
    assert ex.validate_payload(p) == []  # an error record is valid; it just must not be hidden


def test_reviewer_decision_is_in_the_exported_result(client):
    ev = client.post("/api/evaluate/c-ru-1").json()
    ap = client.post("/api/appeal", json={"evaluation_id": ev["evaluation_id"], "criterion_id": "closing",
                                          "comment": "c"}).json()
    client.post(f"/api/appeal/{ap['id']}/decision", json={"decision": "change", "answer": "yes", "reason": "Попрощался"})
    r = rec(payload(client), "c-ru-1")
    assert val(r, "closure")["value"] is True and r["score"]["final_score"] == 85.38
    assert val(r, "closure")["rationale"] == "Попрощался"


def test_gross_error_sets_critical_flag_but_not_the_score(client):
    def gross(d, c):
        r = fake_ai_numeric(d, c)
        r["critical_error"], r["critical_error_evidence"] = True, [_ev(d["transcript"][2])]
        return r

    evaluation_service.set_evaluator(gross)
    client.post("/api/evaluate/c-ru-1")
    r = rec(payload(client), "c-ru-1")
    assert val(r, "critical")["value"] is True and val(r, "critical")["evidence_turns"] == [3]
    assert r["score"] == {"final_score": 77.69, "critical_flag": True}


def test_swapped_checklist_is_refused_with_a_hint(client):
    client.post("/api/evaluate/c-ru-1")  # under version 1
    cl = client.get("/api/checklist").json()["checklist"]
    cl["criteria"] = [c for c in cl["criteria"] if c["id"] != "closing"] + [{
        "id": "compliance", "name": "Скрипт", "type": "scale", "weight": 5, "section": "flow",
        "scale": [{"score": 5}, {"score": 3}, {"score": 0}]}]
    client.put("/api/checklist", json=cl)
    evaluation_service.set_evaluator(fake_generic)
    client.post("/api/evaluate/c-ru-1")  # under version 2
    with pytest.raises(ex.ExportError, match="checklist-version"):
        payload(client)
    assert rec(payload(client, checklist_version=1), "c-ru-1")["score"]["final_score"] == 77.69


def test_validator_catches_every_rule(client):
    client.post("/api/evaluate/c-ru-1")
    client.post("/api/evaluate/v-kz-1")
    good = payload(client)
    expected = ["c-ru-1", "v-kz-1", "e-en-1"]
    assert ex.validate_payload(good, expected_ids=expected) == []

    def broken(mutate):
        p = copy.deepcopy(good)
        mutate(p)
        return ex.validate_payload(p, expected_ids=expected)

    assert any("duplicate" in x for x in broken(lambda p: p["results"].append(copy.deepcopy(p["results"][0]))))
    assert any("missing" in x for x in broken(lambda p: p["results"].pop()))
    assert any("exactly" in x for x in broken(lambda p: p["results"][0]["criteria"].pop()))
    assert any("invalid value" in x for x in broken(lambda p: val(p["results"][0], "consultation").update(value=12)))
    assert any("invalid value" in x for x in broken(lambda p: val(p["results"][0], "contact").update(value=None)))
    assert any("invalid value" in x for x in broken(lambda p: val(p["results"][0], "dialogue").update(value=None)))
    assert any("invalid value" in x for x in broken(lambda p: val(p["results"][0], "empathy").update(value=1)))
    assert any("critical_flag" in x for x in broken(lambda p: p["results"][0]["score"].update(critical_flag=True)))
    assert any("final_score" in x for x in broken(lambda p: p["results"][0]["score"].update(final_score=100.001)))
    assert any("rationale" in x for x in broken(lambda p: val(p["results"][0], "contact").update(rationale=" ")))
    assert any("evidence_turns" in x for x in broken(lambda p: val(p["results"][0], "contact").update(evidence_turns=[0])))
    assert any("error record" in x for x in broken(lambda p: p["results"][2].update(score={"final_score": 0})))
    assert any("exactly 10" in x for x in ex.validate_payload(good, expected_count=10))
    assert any("model.mode" in x for x in broken(lambda p: p["model"].update(mode="cloud")))


def test_cli_writes_a_valid_utf8_file_and_checks_prod_count(client, tmp_path, capsys):
    client.post("/api/evaluate/batch", json={"group_id": "DEV"})
    out = tmp_path / "dev" / "results.json"
    args = ["DEV", "--team-name", "Команда", "--model-name", "m", "--mode", "local", "--out", str(out)]
    assert ex.main(args, client=client) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["team_name"] == "Команда" and len(data["results"]) == 3 and data["model"] == {"mode": "local", "name": "m"}
    assert "Команда" in out.read_text(encoding="utf-8")  # real UTF-8, not \u escapes
    assert not out.read_bytes().startswith(b"\xef\xbb\xbf")  # no BOM

    bad = tmp_path / "prod" / "results.json"  # 3 dialogues are not the required 10
    assert ex.main(["DEV", "--folder", "prod", "--team-name", "T", "--model-name", "m", "--mode", "local",
                    "--out", str(bad)], client=client) == 1
    assert not bad.exists() and "exactly 10" in capsys.readouterr().out


def test_cli_guards(client, tmp_path, monkeypatch):
    monkeypatch.delenv("TEAM_NAME", raising=False)
    assert ex.main(["Swap", "--team-name", "T"], client=client) == 2  # SWAP must not be exported by accident
    assert ex.main(["DEV"], client=client) == 2  # no team name
    assert ex.main(["Other", "--team-name", "T"], client=client) == 2  # unknown group needs --folder
    assert ex.main(["DEV", "--team-name", "  "], client=client) == 2


def test_mode_inference_and_missing_model(client):
    assert ex.infer_mode("ollama") == "local" and ex.infer_mode("Gemini") == "external_api"
    assert ex.infer_mode(None) is None
    client.post("/api/evaluate/c-ru-1")
    with pytest.raises(ex.ExportError, match="--mode"):
        ex.build_payload(client, "DEV", "T", model_name="m")  # LLM_PROVIDER not set in tests
