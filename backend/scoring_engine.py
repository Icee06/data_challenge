"""scoring_engine.py: deterministic scoring for the CQA checklist.

The LLM only produces *answers* (yes / no / na / a scale step) plus explanation
and evidence. Everything numeric lives here, so the same answers always give
the same score, and the checklist can be edited from the UI without code changes.

Rules implemented (from the "Chat Quality Checklist" PDF):
  * "yes" = full weight, "no" = 0, scale: score / max_step * weight.
  * The top scale step always gives the full weight.
  * N/A criteria are excluded and their weight is redistributed proportionally
    over the applicable criteria, so the maximum stays 100.
  * Total is rounded to 2 decimals (half-up). If no weighted criterion is
    applicable, the total is not calculated (None).
  * Gross errors (type "flag") carry no points; they only set `critical_error`.

Math is done with exact Fractions and rounded once at the end, so values like
x.xx5 round the way a human with a calculator expects. Per-criterion points are
rounded for display only; the total is computed from the exact values, so the
rounded parts may differ from the total by 0.01.
"""
import math
import sys
from fractions import Fraction
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple, Union

from pydantic import BaseModel, Field, model_validator

MAX_TOTAL = Fraction(100)
NA = "na"
_YES = {"yes", "y", "true", "да"}
_NO = {"no", "n", "false", "нет"}
_NA = {"na", "n/a", "n.a.", "н/а"}

CriterionType = Literal["yes_no", "scale", "flag"]
Score = Union[str, int, float]  # "yes" | "no" | "na" | a scale step


class ScoringError(ValueError):
    """Answers do not match the checklist. `.problems` lists every issue found."""

    def __init__(self, problems: List[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


# --------------------------------------------------------------------------- #
# Checklist configuration (editable from the UI; also usable as request body)
# --------------------------------------------------------------------------- #
class ScaleStep(BaseModel):
    score: float
    description: str = ""


class Criterion(BaseModel):
    id: str = Field(min_length=1)
    name: str
    type: CriterionType = "yes_no"
    weight: float = Field(default=0, ge=0)
    allow_na: bool = False
    scale: List[ScaleStep] = []  # only for type == "scale"
    section: Optional[str] = None
    description: str = ""  # what "yes" / the top step means; used in the LLM prompt
    na_condition: str = ""  # when N/A applies; used in the LLM prompt

    @property
    def scale_scores(self) -> List[Fraction]:
        return [_frac(s.score) for s in self.scale]

    @model_validator(mode="after")
    def _validate(self):
        if self.type == "scale":
            scores = self.scale_scores
            if len(scores) < 2:
                raise ValueError(f"criterion '{self.id}': a scale needs at least 2 steps")
            if len(set(scores)) != len(scores):
                raise ValueError(f"criterion '{self.id}': duplicate scale steps")
            if min(scores) < 0 or max(scores) <= 0:
                raise ValueError(f"criterion '{self.id}': scale steps must be >= 0 with a positive maximum")
        elif self.type == "flag":
            self.weight = 0  # gross errors never carry points
            self.allow_na = False
        return self


class Section(BaseModel):
    id: str
    name: str


class Checklist(BaseModel):
    id: str = "default"
    name: str = "Chat quality checklist"
    criteria: List[Criterion] = Field(min_length=1)
    sections: List[Section] = []
    notes: List[str] = []  # global rules for the LLM prompt

    @property
    def weights_total(self) -> float:
        """Sum of configured weights. Not required to be 100: the engine normalises."""
        return float(sum(_frac(c.weight) for c in self.criteria))

    @model_validator(mode="after")
    def _validate(self):
        ids = [c.id for c in self.criteria]
        dups = sorted({i for i in ids if ids.count(i) > 1})
        if dups:
            raise ValueError(f"duplicate criterion ids: {dups}")
        sec_ids = [s.id for s in self.sections]
        if len(set(sec_ids)) != len(sec_ids):
            raise ValueError("duplicate section ids")
        for c in self.criteria:
            if c.section is not None and c.section not in sec_ids:
                raise ValueError(f"criterion '{c.id}' references unknown section '{c.section}'")
        return self


# --------------------------------------------------------------------------- #
# Inputs: what the LLM (or the reviewer) provides
# --------------------------------------------------------------------------- #
class Evidence(BaseModel):
    message_index: Optional[int] = None  # position in transcript (for scroll-to-message)
    timestamp: Optional[str] = None  # original label, "00:02" or ISO 8601
    speaker: Optional[str] = None
    quote: str = ""


class Answer(BaseModel):
    criterion_id: str
    answer: Any  # "yes" | "no" | "na" | scale step
    explanation: str = ""
    evidence: List[Evidence] = []
    checked_range: Optional[str] = None  # for a skipped action: the part of the chat that was checked


class Override(BaseModel):
    """Reviewer decision on an appealed criterion."""

    criterion_id: str
    answer: Any
    reason: str
    reviewer: Optional[str] = None

    @model_validator(mode="after")
    def _reason_required(self):
        if not self.reason.strip():
            raise ValueError("a reviewer decision needs a reason")
        return self


# --------------------------------------------------------------------------- #
# Outputs
# --------------------------------------------------------------------------- #
class CriterionResult(BaseModel):
    id: str
    name: str
    type: CriterionType
    weight: float  # configured weight
    answer: Score
    applicable: bool  # False for N/A
    scale_max: Optional[Union[int, float]] = None
    fraction: Optional[float] = None  # share of the weight earned, 0..1
    effective_weight: Optional[float] = None  # weight after N/A redistribution
    points: Optional[float] = None  # points earned towards the 100
    violation: Optional[bool] = None  # flag criteria only
    explanation: str = ""
    evidence: List[Evidence] = []
    checked_range: Optional[str] = None
    source: Literal["ai", "reviewer"] = "ai"
    original_answer: Optional[Score] = None  # AI answer, when a reviewer changed it
    override_reason: Optional[str] = None
    reviewer: Optional[str] = None


class SectionResult(BaseModel):
    id: str
    name: str
    criterion_ids: List[str]
    points: Optional[float] = None
    max_points: Optional[float] = None
    percent: Optional[float] = None


class ScoreResult(BaseModel):
    checklist_id: str
    total: Optional[float]  # None when not calculable
    max_total: float = 100
    total_note: Optional[str] = None
    applicable_weight: float  # sum of weights that were actually scored
    critical_error: bool
    criteria: List[CriterionResult]
    sections: List[SectionResult]


class ScoreOutcome(BaseModel):
    original: ScoreResult  # AI answers only
    final: ScoreResult  # with reviewer overrides applied (== original if none)
    total_delta: Optional[float] = None
    changed: List[str] = []  # criterion ids whose answer differs


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _frac(value: Any) -> Fraction:
    return Fraction(str(value).strip().replace(",", "."))


def _r2(x: Fraction) -> float:
    """Round half-up to 2 decimals (inputs are never negative)."""
    return float(Fraction(math.floor(x * 100 + Fraction(1, 2)), 100))


def _num(x: Fraction) -> Union[int, float]:
    return int(x) if x.denominator == 1 else float(x)


def _normalize(c: Criterion, raw: Any) -> Union[str, Fraction]:
    """-> 'yes' | 'no' | 'na' | Fraction (scale step). Raises ValueError with a readable message."""
    key = raw.strip().casefold() if isinstance(raw, str) else None
    if key in _NA:
        if not c.allow_na:
            raise ValueError("N/A is not allowed for this criterion")
        return NA

    if c.type in ("yes_no", "flag"):
        if isinstance(raw, bool):
            return "yes" if raw else "no"
        if key in _YES:
            return "yes"
        if key in _NO:
            return "no"
        allowed = "yes/no/na" if c.allow_na else "yes/no"
        raise ValueError(f"expected {allowed}, got {raw!r}")

    steps = [_num(s) for s in sorted(c.scale_scores, reverse=True)]
    if raw is None or isinstance(raw, bool):
        raise ValueError(f"expected one of {steps}, got {raw!r}")
    try:
        value = _frac(raw)
    except (ValueError, ZeroDivisionError):
        raise ValueError(f"expected one of {steps}, got {raw!r}")
    if value not in c.scale_scores:
        raise ValueError(f"{raw!r} is not a valid step; expected one of {steps}")
    return value


def _display(c: Criterion, norm: Union[str, Fraction]) -> Score:
    return norm if isinstance(norm, str) else _num(norm)


# --------------------------------------------------------------------------- #
# Core
# --------------------------------------------------------------------------- #
def score(
    checklist: Checklist,
    answers: Sequence[Union[Answer, dict]],
    overrides: Sequence[Union[Override, dict]] = (),
) -> ScoreResult:
    """Score one dialogue. Raises ScoringError (listing all problems) on invalid input."""
    problems: List[str] = []
    by_id = {c.id: c for c in checklist.criteria}

    given: Dict[str, Answer] = {}
    for raw in answers:
        try:
            a = Answer.model_validate(raw)
        except ValueError as exc:
            problems.append(f"malformed answer: {exc}")
            continue
        if a.criterion_id not in by_id:
            problems.append(f"unknown criterion '{a.criterion_id}'")
        elif a.criterion_id in given:
            problems.append(f"duplicate answer for '{a.criterion_id}'")
        else:
            given[a.criterion_id] = a

    ovr: Dict[str, Override] = {}
    for raw in overrides:
        try:
            o = Override.model_validate(raw)
        except ValueError as exc:
            problems.append(f"malformed override: {exc}")
            continue
        if o.criterion_id not in by_id:
            problems.append(f"override for unknown criterion '{o.criterion_id}'")
        elif o.criterion_id in ovr:
            problems.append(f"duplicate override for '{o.criterion_id}'")
        else:
            ovr[o.criterion_id] = o

    # normalise every criterion (AI answer, then reviewer override on top)
    state: List[Tuple[Criterion, Answer, Union[str, Fraction], Optional[Union[str, Fraction]]]] = []
    for c in checklist.criteria:
        a = given.get(c.id)
        if a is None:
            problems.append(f"missing answer for '{c.id}'")
            continue
        try:
            ai_norm = _normalize(c, a.answer)
        except ValueError as exc:
            problems.append(f"'{c.id}': {exc}")
            continue
        final_norm, original_norm = ai_norm, None
        if c.id in ovr:
            try:
                final_norm = _normalize(c, ovr[c.id].answer)
                original_norm = ai_norm
            except ValueError as exc:
                problems.append(f"override '{c.id}': {exc}")
                continue
        state.append((c, a, final_norm, original_norm))

    if problems:
        raise ScoringError(problems)

    # weights actually in play
    scored = [(c, n) for c, _, n, _ in state if c.type != "flag" and n != NA]
    applicable_w = sum((_frac(c.weight) for c, _ in scored), Fraction(0))

    exact_points: Dict[str, Fraction] = {}
    exact_weight: Dict[str, Fraction] = {}
    results: List[CriterionResult] = []

    for c, a, norm, original_norm in state:
        o = ovr.get(c.id)
        common = dict(
            id=c.id,
            name=c.name,
            type=c.type,
            weight=c.weight,
            answer=_display(c, norm),
            explanation=o.reason if o else a.explanation,
            evidence=a.evidence,
            checked_range=a.checked_range,
            source="reviewer" if o else "ai",
            original_answer=_display(c, original_norm) if original_norm is not None else None,
            override_reason=o.reason if o else None,
            reviewer=o.reviewer if o else None,
        )

        if c.type == "flag":
            results.append(CriterionResult(applicable=True, violation=(norm == "yes"), **common))
            continue
        if norm == NA:
            results.append(CriterionResult(applicable=False, **common))
            continue

        if c.type == "scale":
            top = max(c.scale_scores)
            fraction = norm / top
            scale_max: Optional[Union[int, float]] = _num(top)
        else:
            fraction = Fraction(1) if norm == "yes" else Fraction(0)
            scale_max = None

        if applicable_w > 0:
            eff_w = _frac(c.weight) * MAX_TOTAL / applicable_w
            exact_weight[c.id] = eff_w
            exact_points[c.id] = eff_w * fraction
        results.append(
            CriterionResult(
                applicable=True,
                scale_max=scale_max,
                fraction=round(float(fraction), 4),
                effective_weight=_r2(exact_weight[c.id]) if c.id in exact_weight else None,
                points=_r2(exact_points[c.id]) if c.id in exact_points else None,
                **common,
            )
        )

    if applicable_w > 0:
        total: Optional[float] = _r2(sum(exact_points.values(), Fraction(0)))
        note = None
    else:
        total = None
        note = "No applicable criteria with weight: total is not calculated."

    sections: List[SectionResult] = []
    for s in checklist.sections:
        ids = [c.id for c in checklist.criteria if c.section == s.id]
        max_pts = sum((exact_weight[i] for i in ids if i in exact_weight), Fraction(0))
        pts = sum((exact_points[i] for i in ids if i in exact_points), Fraction(0))
        sections.append(
            SectionResult(
                id=s.id,
                name=s.name,
                criterion_ids=ids,
                points=_r2(pts) if max_pts > 0 else None,
                max_points=_r2(max_pts) if max_pts > 0 else None,
                percent=_r2(pts / max_pts * 100) if max_pts > 0 else None,
            )
        )

    return ScoreResult(
        checklist_id=checklist.id,
        total=total,
        total_note=note,
        applicable_weight=float(applicable_w),
        critical_error=any(r.violation for r in results),
        criteria=results,
        sections=sections,
    )


def score_with_overrides(
    checklist: Checklist,
    answers: Sequence[Union[Answer, dict]],
    overrides: Sequence[Union[Override, dict]] = (),
) -> ScoreOutcome:
    """Original (AI) result vs final result after reviewer decisions: the appeal card."""
    original = score(checklist, answers)
    final = score(checklist, answers, overrides) if overrides else original
    delta = None
    if original.total is not None and final.total is not None:
        delta = round(final.total - original.total, 2)
    changed = [
        f.id for o, f in zip(original.criteria, final.criteria) if o.answer != f.answer
    ]
    return ScoreOutcome(original=original, final=final, total_delta=delta, changed=changed)


# --------------------------------------------------------------------------- #
# Default checklist (the 8 criteria from the hackathon PDF)
# --------------------------------------------------------------------------- #
def default_checklist() -> Checklist:
    return Checklist.model_validate(
        {
            "id": "default",
            "name": "Чек-лист качества чатов",
            "sections": [
                {"id": "start", "name": "Начало и понимание запроса"},
                {"id": "safety", "name": "Безопасность и решение"},
                {"id": "flow", "name": "Ведение и завершение диалога"},
                {"id": "gross", "name": "Грубые нарушения"},
            ],
            "notes": [
                "Реплики бота и системные события служат контекстом; оценивается работа оператора.",
                "Порог первой реакции в 3 минуты, теги и вложения в оценку не входят.",
            ],
            "criteria": [
                {
                    "id": "contact",
                    "name": "Установление контакта",
                    "type": "yes_no",
                    "weight": 10,
                    "section": "start",
                    "description": "Да — оператор поздоровался на языке клиента и представился в первом ответе.",
                },
                {
                    "id": "needs",
                    "name": "Выявление потребности",
                    "type": "yes_no",
                    "weight": 15,
                    "allow_na": True,
                    "section": "start",
                    "description": "Да — оператор учёл известные детали и уточнил недостающее до ответа.",
                    "na_condition": "N/A — данных для ответа хватало с самого начала.",
                },
                {
                    "id": "identification",
                    "name": "Идентификация",
                    "type": "yes_no",
                    "weight": 20,
                    "allow_na": True,
                    "section": "safety",
                    "description": (
                        "Да — проверка личности подтверждена до раскрытия данных или защищённого действия. "
                        "Если проверка была нужна и в чате не подтверждена — «Нет»."
                    ),
                    "na_condition": "N/A — запрос не требовал идентификации (например, общий вопрос о тарифах).",
                },
                {
                    "id": "consultation",
                    "name": "Корректная и полная консультация",
                    "type": "scale",
                    "weight": 20,
                    "allow_na": True,
                    "section": "safety",
                    "description": "Оцените ответ или следующий шаг по шкале.",
                    "na_condition": "N/A — чат закончился до того, как клиент сформулировал запрос.",
                    "scale": [
                        {"score": 20, "description": "Все существенные вопросы закрыты или дан корректный следующий шаг."},
                        {"score": 15, "description": "Основной вопрос решён, пропущена второстепенная деталь."},
                        {"score": 10, "description": "Часть запроса закрыта, существенный вопрос остался открытым."},
                        {"score": 5, "description": "Есть общая подсказка, но клиенту всё ещё непонятно, как действовать."},
                        {"score": 0, "description": "Ответа по существу нет или ключевая рекомендация ошибочна."},
                    ],
                },
                {
                    "id": "empathy",
                    "name": "Эмпатия и клиентоориентированность",
                    "type": "yes_no",
                    "weight": 15,
                    "section": "flow",
                    "description": (
                        "Да — оператор вежлив и, если клиент выразил недовольство или описал неудобство, "
                        "отреагировал на конкретную проблему."
                    ),
                },
                {
                    "id": "dialogue",
                    "name": "Управление диалогом, скорость и грамотность",
                    "type": "scale",
                    "weight": 15,
                    "section": "flow",
                    "description": "Оцените ясность, порядок ответов и предупреждение об ожидании по шкале.",
                    "scale": [
                        {"score": 10, "description": "Ответы ясные и связные. Если было явное ожидание, оператор предупредил."},
                        {"score": 7, "description": "Один заметный сбой, но клиент может понять ответ и продолжить."},
                        {"score": 4, "description": "Клиенту приходится переспрашивать из-за формулировок или порядка ответов."},
                        {"score": 0, "description": "Из-за бессвязных или противоречивых реплик ход диалога непонятен."},
                    ],
                },
                {
                    "id": "closing",
                    "name": "Завершение",
                    "type": "yes_no",
                    "weight": 5,
                    "allow_na": True,
                    "section": "flow",
                    "description": "Да — оператор подвёл итог или предложил дальнейшую помощь и попрощался.",
                    "na_condition": "N/A — чат оборвался до возможности его завершить.",
                },
                {
                    "id": "gross_errors",
                    "name": "Грубые ошибки",
                    "type": "flag",
                    "section": "gross",
                    "description": (
                        "Да — выявлено оскорбление клиента, предложение платной услуги в обход компании "
                        "или раскрытие конфиденциальной информации. Нет — ни одно из трёх не выявлено. "
                        "Предложение услуги в обход компании: оператор предлагает перевести деньги ему лично "
                        "для инвестирования или просит личный номер клиента ради платной консультации в частном "
                        "порядке; достаточно самого предложения, согласие клиента или перевод не требуются."
                    ),
                },
            ],
        }
    )


# --------------------------------------------------------------------------- #
# Demo:  python scoring_engine.py
# --------------------------------------------------------------------------- #
def _demo() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Cyrillic on Windows consoles
    except (AttributeError, ValueError):
        pass

    cl = default_checklist()
    answers = [
        {"criterion_id": "contact", "answer": "yes"},
        {"criterion_id": "needs", "answer": "na"},
        {"criterion_id": "identification", "answer": "na"},
        {"criterion_id": "consultation", "answer": 15},
        {"criterion_id": "empathy", "answer": "yes"},
        {"criterion_id": "dialogue", "answer": 7},
        {"criterion_id": "closing", "answer": "no"},
        {"criterion_id": "gross_errors", "answer": "no"},
    ]
    overrides = [{"criterion_id": "closing", "answer": "yes", "reason": "Оператор попрощался в последней реплике."}]
    out = score_with_overrides(cl, answers, overrides)

    def show(title: str, r: ScoreResult) -> None:
        print(f"\n== {title}: total = {r.total} / 100   critical_error = {r.critical_error}"
              f"   (applicable weight {r.applicable_weight:g})")
        for c in r.criteria:
            pts = "-" if c.points is None else f"{c.points:6.2f} / {c.effective_weight:.2f}"
            print(f"  {c.id:<15} {str(c.answer):>4}  weight {c.weight:>4g}  points {pts}")
        for s in r.sections:
            print(f"  [section] {s.name}: {s.points} / {s.max_points}")

    show("ORIGINAL (AI)", out.original)
    show("FINAL (after reviewer)", out.final)
    print(f"\ndelta = {out.total_delta}, changed = {out.changed}")


if __name__ == "__main__":
    _demo()
