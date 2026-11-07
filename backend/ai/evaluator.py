"""evaluate_dialogue(): the single entry point Backend calls.

    from ai.evaluator import evaluate_dialogue
    result = evaluate_dialogue(dialogue, checklist)

dialogue  = {"id": "001", "type": "chat|call", "transcript": [{"speaker","timestamp","text"}, ...]}
checklist = see prompt.py. Returns a validated dict (no total score; Backend computes it).
Raises EvaluationFailed if the model never produced a valid answer.
"""
from . import llm_client
from .language import detect_language, transcript_text
from .prompt import build_system_prompt, build_user_prompt
from .validator import ValidationError, validate


class EvaluationFailed(Exception):
    pass


def evaluate_dialogue(dialogue: dict, checklist: dict, llm=None, max_attempts: int = 3) -> dict:
    llm = llm or llm_client.complete
    if not dialogue.get("transcript"):
        raise EvaluationFailed("empty transcript")

    lang = detect_language(transcript_text(dialogue["transcript"]))
    system = build_system_prompt(checklist, lang)
    user = build_user_prompt(dialogue)

    last_err = None
    for _ in range(max_attempts):
        prompt = user
        if last_err:
            prompt += (f"\n\nYour previous answer was rejected: {last_err}. "
                       "Return corrected JSON only.")
        try:
            raw = llm(system, prompt)
            result = validate(raw, checklist, dialogue)
            result["language"] = result.get("language") or lang
            result["detected_language"] = lang
            return result
        except (ValidationError, llm_client.LLMError) as e:
            last_err = str(e)
    raise EvaluationFailed(f"no valid answer after {max_attempts} attempts: {last_err}")
