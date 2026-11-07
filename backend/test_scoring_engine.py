"""Run from backend/:  pytest -q

Expected numbers are calculated by hand from the checklist PDF (see comments),
not copied from the engine's output.
"""
import pytest

import scoring_engine as se

CL = se.default_checklist()


def answers(**kw):
    """Build a full answer list; defaults = perfect chat. Override per criterion id."""
    base = dict(
        contact="yes", needs="yes", identification="yes", consultation=20,
        empathy="yes", dialogue=10, closing="yes", gross_errors="no",
    )
    base.update(kw)
    return [{"criterion_id": k, "answer": v} for k, v in base.items()]


def result_by_id(res):
    return {c.id: c for c in res.criteria}


# ---------------------------------------------------------------- basic math
def test_default_weights_sum_to_100():
    assert CL.weights_total == 100


def test_perfect_chat_is_100():
    r = se.score(CL, answers())
    assert r.total == 100.0
    assert r.critical_error is False


def test_all_no_and_zero_is_0():
    r = se.score(CL, answers(contact="no", needs="no", identification="no", consultation=0,
                             empathy="no", dialogue=0, closing="no"))
    assert r.total == 0.0


def test_mixed_scores_no_na():
    # contact 10 + needs 0 + ident 20 + consult 15/20*20=15 + empathy 15 + dialogue 7/10*15=10.5 + closing 0
    # = 70.5 of 100
    r = se.score(CL, answers(needs="no", consultation=15, dialogue=7, closing="no"))
    assert r.total == 70.5
    c = result_by_id(r)
    assert c["consultation"].points == 15.0
    assert c["dialogue"].points == 10.5
    assert c["dialogue"].fraction == 0.7


def test_top_scale_step_gives_full_weight():
    c = result_by_id(se.score(CL, answers()))
    assert c["consultation"].points == 20.0 and c["dialogue"].points == 15.0


# ---------------------------------------------------------------- N/A
def test_na_redistributes_weight():
    # needs(15) + identification(20) + closing(5) are N/A -> applicable weight = 10+20+15+15 = 60
    r = se.score(CL, answers(needs="na", identification="na", closing="na"))
    assert r.applicable_weight == 60
    assert r.total == 100.0
    c = result_by_id(r)
    assert c["needs"].applicable is False and c["needs"].points is None
    assert c["contact"].effective_weight == 16.67  # 10 * 100/60


def test_na_with_partial_score():
    # applicable: contact 10, consultation 20, empathy 15, dialogue 15 = 60
    # earned: 10 + 15 + 15 + 15 = 55  -> 55/60*100 = 91.666.. -> 91.67
    r = se.score(CL, answers(needs="na", identification="na", closing="na", consultation=15))
    assert r.total == 91.67


def test_na_not_allowed_is_rejected():
    with pytest.raises(se.ScoringError) as e:
        se.score(CL, answers(contact="na"))
    assert "N/A is not allowed" in str(e.value)


def test_no_applicable_weighted_criteria_gives_no_total():
    cl = se.Checklist(criteria=[
        se.Criterion(id="a", name="A", weight=10, allow_na=True),
        se.Criterion(id="flag", name="F", type="flag"),
    ])
    r = se.score(cl, [{"criterion_id": "a", "answer": "na"}, {"criterion_id": "flag", "answer": "no"}])
    assert r.total is None
    assert "not calculated" in r.total_note


# ---------------------------------------------------------------- rounding
def test_round_half_up_not_bankers():
    # weights 1 and 19999, "yes" on the first: 100*1/20000 = 0.005 exactly -> half-up -> 0.01
    cl = se.Checklist(criteria=[
        se.Criterion(id="a", name="A", weight=1),
        se.Criterion(id="b", name="B", weight=19999),
    ])
    r = se.score(cl, [{"criterion_id": "a", "answer": "yes"}, {"criterion_id": "b", "answer": "no"}])
    assert r.total == 0.01


def test_weights_not_summing_to_100_are_normalised():
    cl = se.Checklist(criteria=[
        se.Criterion(id="a", name="A", weight=30),
        se.Criterion(id="b", name="B", weight=10),
    ])
    r = se.score(cl, [{"criterion_id": "a", "answer": "yes"}, {"criterion_id": "b", "answer": "no"}])
    assert r.total == 75.0  # 30 of 40


# ---------------------------------------------------------------- gross errors
def test_gross_error_flags_but_does_not_change_total():
    clean = se.score(CL, answers(consultation=15))
    flagged = se.score(CL, answers(consultation=15, gross_errors="yes"))
    assert flagged.critical_error is True
    assert flagged.total == clean.total == 95.0
    g = result_by_id(flagged)["gross_errors"]
    assert g.violation is True and g.points is None


# ---------------------------------------------------------------- sections
def test_section_results_add_up_to_total():
    r = se.score(CL, answers(needs="na", identification="na", closing="na", consultation=15))
    s = {x.id: x for x in r.sections}
    # start: only contact applicable -> 16.67 / 16.67
    assert (s["start"].points, s["start"].max_points, s["start"].percent) == (16.67, 16.67, 100.0)
    # safety: only consultation -> 15/20 of 33.33 = 25.00 / 33.33
    assert (s["safety"].points, s["safety"].max_points, s["safety"].percent) == (25.0, 33.33, 75.0)
    # flow: empathy + dialogue -> 50 / 50
    assert (s["flow"].points, s["flow"].max_points) == (50.0, 50.0)
    # gross section has no weighted criteria
    assert s["gross"].points is None
    assert abs(sum(x.points for x in r.sections if x.points is not None) - r.total) <= 0.01


# ---------------------------------------------------------------- input handling
@pytest.mark.parametrize("raw,expected", [("Да", "yes"), ("НЕТ", "no"), ("N/A", "na"), ("н/а", "na"), (True, "yes")])
def test_answer_aliases(raw, expected):
    r = se.score(CL, answers(needs=raw))
    assert result_by_id(r)["needs"].answer == expected


def test_scale_accepts_numeric_strings():
    r = se.score(CL, answers(consultation="15", dialogue="7,0"))
    assert result_by_id(r)["consultation"].points == 15.0


def test_invalid_scale_step_missing_unknown_and_duplicate_are_all_reported():
    bad = answers(consultation=12)
    bad = [a for a in bad if a["criterion_id"] != "closing"]  # missing
    bad.append({"criterion_id": "ghost", "answer": "yes"})  # unknown
    bad.append({"criterion_id": "contact", "answer": "yes"})  # duplicate
    with pytest.raises(se.ScoringError) as e:
        se.score(CL, bad)
    text = " | ".join(e.value.problems)
    assert "not a valid step" in text
    assert "missing answer for 'closing'" in text
    assert "unknown criterion 'ghost'" in text
    assert "duplicate answer for 'contact'" in text


def test_bool_is_not_a_scale_value():
    with pytest.raises(se.ScoringError):
        se.score(CL, answers(consultation=True))


# ---------------------------------------------------------------- checklist config
def test_custom_criterion_replacement_works():
    """Control task: replace one criterion, save, re-score."""
    data = CL.model_dump()
    data["criteria"] = [c for c in data["criteria"] if c["id"] != "closing"]
    data["criteria"].append({
        "id": "compliance", "name": "Соблюдение скрипта", "type": "scale", "weight": 5, "section": "flow",
        "scale": [{"score": 5}, {"score": 3}, {"score": 0}],
    })
    new = se.Checklist.model_validate(data)
    a = [x for x in answers() if x["criterion_id"] != "closing"] + [{"criterion_id": "compliance", "answer": 3}]
    r = se.score(new, a)
    assert r.total == 98.0  # 95 from the other criteria + 3/5*5 = 3


def test_checklist_validation():
    with pytest.raises(ValueError):
        se.Checklist(criteria=[se.Criterion(id="a", name="A"), se.Criterion(id="a", name="B")])
    with pytest.raises(ValueError):
        se.Criterion(id="s", name="S", type="scale", weight=5, scale=[se.ScaleStep(score=5)])
    with pytest.raises(ValueError):
        se.Checklist(criteria=[se.Criterion(id="a", name="A", section="nope")])


# ---------------------------------------------------------------- appeals
def test_override_recalculates_and_keeps_history():
    ans = answers(needs="no", consultation=15, dialogue=7, closing="no")  # 70.5
    out = se.score_with_overrides(CL, ans, [{"criterion_id": "closing", "answer": "yes", "reason": "Попрощался"}])
    assert out.original.total == 70.5
    assert out.final.total == 75.5  # closing worth 5
    assert out.total_delta == 5.0
    assert out.changed == ["closing"]
    c = result_by_id(out.final)["closing"]
    assert (c.source, c.original_answer, c.override_reason) == ("reviewer", "no", "Попрощался")
    assert result_by_id(out.original)["closing"].source == "ai"


def test_override_to_na_redistributes():
    ans = answers(needs="no", consultation=15, dialogue=7, closing="no")
    out = se.score_with_overrides(CL, ans, [{"criterion_id": "needs", "answer": "na", "reason": "Данных хватало"}])
    # applicable weight 85, earned 70.5 -> 82.941.. -> 82.94
    assert out.final.total == 82.94


def test_override_needs_reason_and_valid_answer():
    with pytest.raises(ValueError):
        se.Override(criterion_id="closing", answer="yes", reason="  ")
    with pytest.raises(se.ScoringError):
        se.score(CL, answers(), [{"criterion_id": "contact", "answer": "na", "reason": "x"}])


def test_confirmed_appeal_without_override_is_unchanged():
    out = se.score_with_overrides(CL, answers(consultation=15), [])
    assert out.final.total == out.original.total
    assert out.total_delta == 0
    assert out.changed == []


# ---------------------------------------------------------------- HTTP layer
def test_api_default_checklist_and_score():
    from fastapi.testclient import TestClient
    import main

    client = TestClient(main.app)
    cl = client.get("/api/checklist/default").json()
    assert len(cl["criteria"]) == 8

    body = {"answers": answers(needs="no", consultation=15, dialogue=7, closing="no")}
    res = client.post("/api/score", json=body)
    assert res.status_code == 200
    assert res.json()["final"]["total"] == 70.5

    bad = client.post("/api/score", json={"answers": [{"criterion_id": "contact", "answer": "maybe"}]})
    assert bad.status_code == 422
    assert bad.json()["detail"]["problems"]
