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
_CHUNK_SIZE = 1800
_CHUNK_OVERLAP = 200
_VALID_SUMMARY_MODES = {'brief', 'standard', 'detailed'}


def _get_summarizer():
    global _SUMMARIZER
    if _SUMMARIZER is None:
        try:
            from transformers import pipeline

            _SUMMARIZER = pipeline('summarization', model='google/flan-t5-base')
        except Exception:
            _SUMMARIZER = False
    return _SUMMARIZER


def _split_text_chunks(text: str, chunk_size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> List[str]:
    text = (text or '').strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    step = max(chunk_size - overlap, 1)
    start = 0
    text_len = len(text)

    while start < text_len:
        end = min(start + chunk_size, text_len)
        if end < text_len:
            sentence_end = text.rfind('.', start + int(chunk_size * 0.6), end)
            if sentence_end != -1:
                end = sentence_end + 1

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= text_len:
            break
        start = max(end - overlap, start + 1)

    return chunks


def _run_local_summary(text: str, max_length: int = 180, min_length: int = 50) -> str:
    summarizer = _get_summarizer()
    if not summarizer:
        return text[:700] + ('...' if len(text) > 700 else '')

    snippet = text[:2200]
    try:
        result = summarizer(snippet, max_length=max_length, min_length=min_length, do_sample=False)
        return result[0]['summary_text']
    except Exception as exc:
        logger.warning('Local summary generation failed: %s', exc)
        return snippet[:700] + ('...' if len(snippet) > 700 else '')


def _run_chunked_local_summary(text: str, chunk_size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> str:
    chunks = _split_text_chunks(text, chunk_size=chunk_size, overlap=overlap)
    if not chunks:
        return 'No text available for summarization.'

    if len(chunks) == 1:
        return _run_local_summary(chunks[0], max_length=220, min_length=60)

    chunk_summaries = []
    for chunk in chunks:
        words = len(chunk.split())
        min_len = 40 if words < 180 else 60
        max_len = 140 if words < 180 else 220
        chunk_summaries.append(_run_local_summary(chunk, max_length=max_len, min_length=min_len))

    return '\n'.join(chunk_summaries)


def _normalize_summary_mode(summary_mode: str) -> str:
    mode = (summary_mode or '').strip().lower()
    if mode not in _VALID_SUMMARY_MODES:
        return 'detailed'
    return mode


def _split_sentences(text: str) -> List[str]:
    raw_sentences = [sentence.strip() for sentence in text.replace('\n', ' ').split('.')]
    return [sentence for sentence in raw_sentences if sentence]


def _format_local_fallback(local_summary: str, mode: str) -> str:
    cleaned = (local_summary or '').strip()
    if not cleaned:
        return 'No text available for summarization.'

    sentences = _split_sentences(cleaned)
    if mode == 'brief':
        bullets = sentences[:3] or [cleaned]
        return '\n'.join(f'- {bullet.rstrip(".") }.' if not bullet.endswith('.') else f'- {bullet}' for bullet in bullets)

    if mode == 'standard':
        lead = ' '.join(sentences[:2]) if sentences else cleaned
        bullet_points = sentences[2:6] or sentences[:4]
        bullet_text = '\n'.join(f'- {point.rstrip(".") }.' if not point.endswith('.') else f'- {point}' for point in bullet_points)
        return f'{lead}\n\nKey Points\n{bullet_text}' if bullet_text else lead

    overview = sentences[:2]
    key_concepts = sentences[2:5]
    important_details = sentences[5:9]
    takeaways = sentences[9:12]

    sections = [
        ('Overview', overview),
        ('Key Concepts', key_concepts),
        ('Important Details', important_details),
        ('Takeaways', takeaways),
    ]
    rendered = []
    for title, items in sections:
        if not items:
            continue
        rendered.append(f'{title}\n' + '\n'.join(f'- {item.rstrip(".") }.' if not item.endswith('.') else f'- {item}' for item in items))
    return '\n\n'.join(rendered) if rendered else cleaned


def summarize_text(text: str, summary_mode: str = 'detailed') -> str:
    if not text:
        return 'No text available for summarization.'

    mode = _normalize_summary_mode(summary_mode)
    if mode == 'brief':
        local_summary = _run_local_summary(text, max_length=120, min_length=35)
    elif mode == 'standard':
        local_draft = _run_chunked_local_summary(text, chunk_size=1700, overlap=180)
        local_summary = _run_local_summary(local_draft, max_length=180, min_length=50)
    else:
        local_summary = _run_chunked_local_summary(text)

    if not gemini_is_configured():
        logger.info('Gemini is not configured. Returning local T5 summary.')
        return _format_local_fallback(local_summary, mode)

    try:
        refined = generate_gemini_summary(text, local_summary, summary_mode=mode)
    except Exception as exc:
        logger.warning('Gemini summary generation failed: %s', exc)
        return _format_local_fallback(local_summary, mode)

    if refined == local_summary:
        logger.warning('Gemini refinement unavailable. Using local T5 summary fallback.')

    return refined or _format_local_fallback(local_summary, mode)


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
