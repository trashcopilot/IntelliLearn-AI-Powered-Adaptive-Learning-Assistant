import logging
from typing import List

from .gemini_model import (
    generate_gemini_questions,
    generate_gemini_retry_question,
    generate_gemini_summary,
    gemini_is_configured,
)

logger = logging.getLogger(__name__)

_SUMMARIZER = None
_QG_PIPELINE = None


def _get_summarizer():
    global _SUMMARIZER
    if _SUMMARIZER is None:
        try:
            from transformers import pipeline

            _SUMMARIZER = pipeline('summarization', model='google/flan-t5-base')
        except Exception:
            _SUMMARIZER = False
    return _SUMMARIZER


def _run_local_summary(text: str) -> str:
    summarizer = _get_summarizer()
    if not summarizer:
        return text[:500] + ('...' if len(text) > 500 else '')

    snippet = text[:2000]
    result = summarizer(snippet, max_length=180, min_length=50, do_sample=False)
    return result[0]['summary_text']


def summarize_text(text: str) -> str:
    if not text:
        return 'No text available for summarization.'

    local_summary = _run_local_summary(text)
    if not gemini_is_configured():
        logger.info('Gemini is not configured. Returning local T5 summary.')
        return local_summary

    try:
        refined = generate_gemini_summary(text, local_summary)
    except Exception as exc:
        logger.warning('Gemini summary generation failed: %s', exc)
        return local_summary

    if refined == local_summary:
        logger.warning('Gemini refinement unavailable. Using local T5 summary fallback.')

    return refined or local_summary


def generate_questions(text: str) -> List[str]:
    if not text:
        return ['What is the main idea of this lecture?']

    if gemini_is_configured():
        try:
            questions = generate_gemini_questions(text)
            if questions:
                return questions[:10]
        except Exception as exc:
            logger.warning('Gemini question generation failed: %s', exc)

    sentences = [s.strip() for s in text.split('.') if len(s.split()) > 6]
    questions = []
    for sentence in sentences[:10]:
        questions.append(f'Explain: {sentence}?')

    return questions or ['What is the main idea of this lecture?']


def generate_similar_question(question_text: str, concept_name: str = '') -> str:
    base = (question_text or '').strip().rstrip('?')
    if not base:
        return f'Explain one core idea from {concept_name or "this concept"} in your own words.'

    if gemini_is_configured():
        try:
            gemini_text = generate_gemini_retry_question(base, concept_name)
            if gemini_text:
                return gemini_text
        except Exception as exc:
            logger.warning('Gemini retry-question generation failed: %s', exc)

    if concept_name:
        return f'Retry: {base} with reference to {concept_name}?'
    return f'Retry: {base}?'
