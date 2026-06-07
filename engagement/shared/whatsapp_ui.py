"""WhatsApp message formatting helpers (quick-reply style prompts)."""
from __future__ import annotations

from . import i18n


def eligibility_question(lang: str, question_key: str, step: int, total: int) -> str:
    """One compact question with Yes/No tap hints (works in Twilio session window)."""
    question = i18n.t(question_key, lang)
    return i18n.t("ELIG_QUESTION_BUTTONS", lang, step=step, total=total, question=question)
