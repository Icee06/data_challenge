"""Request models, the AI answer contract and the one error type shared by the service layer.

Services raise ServiceError; main.py turns it into a JSON response. Services never
import FastAPI, so they stay easy to unit-test.
"""
from typing import Any, List, Literal, Optional, Union

from pydantic import BaseModel

import scoring_engine as se


class ServiceError(Exception):
    def __init__(self, status_code: int, message: str, problems: Optional[List[str]] = None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.problems = problems or []


class BatchEvaluateRequest(BaseModel):
    group_id: str = "DEV"
    conversation_ids: Optional[List[str]] = None  # None = every dialogue of the group
    force_refresh: bool = False  # True = ignore the cache and call the AI again
    checklist_version: Optional[int] = None  # None = the active checklist
    concurrency_limit: int = 3  # parallel AI calls; keep low on free API tiers


class AppealCreate(BaseModel):
    evaluation_id: int
    criterion_id: str
    comment: str
    author: Optional[str] = None


class AppealDecision(BaseModel):
    decision: Literal["confirm", "change"]
    reason: str
    answer: Any = None  # required when decision == "change": yes / no / na / a scale step
    reviewer: Optional[str] = None


class ChecklistSave(se.Checklist):
    """PUT /api/checklist body: a full checklist plus an optional note for the version list."""

    change_note: str = ""


# --------------------------------------------------------------------------- #
# AI contract: what ai.evaluator.evaluate_dialogue(dialogue, checklist) returns.
# Agreed between Backend and AI. evaluation_service.ai_result_to_answers() reads it.
# The AI returns answers and evidence only: never totals, weights or points.
# --------------------------------------------------------------------------- #
class AIEvidence(BaseModel):
    quote: str  # exact text from the transcript (the backend checks it exists)
    message_index: Optional[int] = None  # position in the transcript; found from the quote if missing
    speaker: Optional[str] = None
    timestamp: Optional[str] = None  # the original label, "00:02" or ISO 8601


class AICriterionResult(BaseModel):
    criterion_id: str  # EXACTLY the id from the checklist the AI was given ("contact", "needs", ...)
    status: Literal["PASS", "FAIL", "NA"]  # NA only where the criterion has allow_na
    score: Optional[float] = None  # scale criteria only: one of the allowed scale steps
    evidence: Union[AIEvidence, List[AIEvidence], None] = None
    reason: str = ""  # short explanation for the card
    checked_range: Optional[str] = None  # skipped action or N/A: which part of the dialogue was checked


class AIResult(BaseModel):
    criteria: List[AICriterionResult]  # one result per criterion, except the "flag" (gross errors) one
    critical_error: bool = False  # the gross-error criterion: True = a violation WAS found
    critical_error_evidence: List[AIEvidence] = []  # required when critical_error is True
    summary: str = ""
    detected_language: Optional[str] = None  # RU / KZ / EN / MIXED
    validation_flags: List[str] = []  # problems the AI validator fixed on its own

class ChecklistSave(se.Checklist):
    """PUT /api/checklist body: a full checklist plus an optional note for the version list."""

    change_note: str = ""
