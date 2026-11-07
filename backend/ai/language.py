"""Language detection for RU / KZ / EN and mixed dialogues."""
import re

KZ_LETTERS = set("әіңғүұқөһӘІҢҒҮҰҚӨҺ")
CYR = re.compile(r"[а-яёА-ЯЁ" + "".join(KZ_LETTERS) + "]")
LAT = re.compile(r"[A-Za-z]")


def detect_language(text: str) -> str:
    """Return RU, KZ, EN or MIXED (a rough but cheap heuristic)."""
    cyr = len(CYR.findall(text))
    lat = len(LAT.findall(text))
    kz = sum(1 for ch in text if ch in KZ_LETTERS)
    total = cyr + lat
    if total == 0:
        return "RU"
    if lat / total > 0.85:
        return "EN"
    if lat / total > 0.25:
        return "MIXED"
    # Kazakh-only letters appear in a clear share of Cyrillic text
    if kz / max(cyr, 1) > 0.03:
        return "KZ"
    return "RU"


def transcript_text(transcript) -> str:
    return "\n".join(str(t.get("text", "")) for t in transcript)


def language_instruction(lang: str) -> str:
    base = ("Quotes must be copied character for character from the transcript in its original "
            "language. Never translate, fix spelling or shorten inside a quote. "
            "The 'reason' and 'summary' fields are always written in English.")
    extra = {
        "KZ": "The dialogue is mainly in Kazakh; Russian words may appear. Judge meaning in Kazakh.",
        "RU": "The dialogue is mainly in Russian.",
        "EN": "The dialogue is mainly in English.",
        "MIXED": "The dialogue mixes languages (code-switching is normal). Judge the meaning "
                 "across all languages; a language switch is not itself an error.",
    }
    return extra.get(lang, extra["MIXED"]) + " " + base
