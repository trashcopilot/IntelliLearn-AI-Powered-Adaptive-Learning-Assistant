import logging
import os
from functools import lru_cache
from typing import List

logger = logging.getLogger(__name__)

DEFAULT_MODEL_NAME = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')


def gemini_is_configured() -> bool:
    return bool(os.getenv('GEMINI_API_KEY'))


@lru_cache(maxsize=1)
def _get_new_sdk_client():
    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        return None

    try:
        from google import genai
    except Exception as exc:
        logger.info('google-genai package is unavailable, trying legacy SDK: %s', exc)
        return None

    try:
        client = genai.Client(api_key=api_key)
        return client
    except Exception as exc:
        logger.warning('Gemini new SDK client initialization failed: %s', exc)
        return None


@lru_cache(maxsize=1)
def _get_legacy_sdk_model():
    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        return None

    try:
        import google.generativeai as genai
    except Exception as exc:
        logger.info('google-generativeai package is unavailable: %s', exc)
        return None

    try:
        genai.configure(api_key=api_key)
        return genai.GenerativeModel(DEFAULT_MODEL_NAME)
    except Exception as exc:
        logger.warning('Gemini legacy SDK model initialization failed: %s', exc)
        return None


def _extract_nonempty_lines(text: str) -> List[str]:
    lines = []
    for raw_line in (text or '').splitlines():
        line = raw_line.strip().lstrip('-*0123456789. ').strip()
        if line:
            lines.append(line)
    return lines


def _summarization_context(text: str, max_chars: int) -> str:
    cleaned = (text or '').strip()
    if len(cleaned) <= max_chars:
        return cleaned

    # Preserve both early and late content so long materials are represented more fairly.
    head_len = int(max_chars * 0.65)
    tail_len = max_chars - head_len
    head = cleaned[:head_len].rstrip()
    tail = cleaned[-tail_len:].lstrip()
    return f'{head}\n\n[...content omitted for length...]\n\n{tail}'


def _generate_text(prompt: str) -> str:
    client = _get_new_sdk_client()
    if client is not None:
        try:
            response = client.models.generate_content(
                model=DEFAULT_MODEL_NAME,
                contents=prompt,
                config={
                    'temperature': 0.2,
                    'max_output_tokens': 900,
                },
            )
            return (getattr(response, 'text', '') or '').strip()
        except Exception as exc:
            logger.warning('Gemini new SDK generation failed: %s', exc)

    legacy_model = _get_legacy_sdk_model()
    if legacy_model is not None:
        try:
            response = legacy_model.generate_content(
                prompt,
                generation_config={
                    'temperature': 0.2,
                    'max_output_tokens': 900,
                },
            )
            return (getattr(response, 'text', '') or '').strip()
        except Exception as exc:
            logger.warning('Gemini legacy SDK generation failed: %s', exc)

    return ''


def generate_gemini_summary(text: str, local_summary: str = '', summary_mode: str = 'detailed') -> str:
    brief_context = _summarization_context(text, 10000)
    standard_context = _summarization_context(text, 13000)
    detailed_context = _summarization_context(text, 15000)

    mode = (summary_mode or '').strip().lower()
    if mode == 'brief':
        prompt = (
            'Create a brief study summary as exactly 3 bullet points. '
            'Each bullet should be one concise sentence. Keep only the most central ideas and do not add new facts. '
            'Do not add a title or extra commentary.\n\n'
            f'Lecture notes:\n{brief_context}\n\n'
            f'Local draft summary:\n{local_summary or "N/A"}'
        )
    elif mode == 'standard':
        prompt = (
            'Create a clear study summary with a short paragraph followed by a "Key Points" list of 3 to 5 bullets. '
            'Cover main concepts and key supporting details without adding new facts. '
            'Do not add a title or extra commentary.\n\n'
            f'Lecture notes:\n{standard_context}\n\n'
            f'Local draft summary:\n{local_summary or "N/A"}'
        )
    else:
        prompt = (
            'Create a detailed study summary with these sections exactly: Overview, Key Concepts, Important Details, and Takeaways. '
            'Use short paragraphs or bullets under each section. Preserve factual accuracy and do not add new information. '
            'Cover major concepts, important definitions, process steps, critical numbers/formulas, and notable limitations or caveats when present. '
            'Do not add a title or extra commentary.\n\n'
            f'Lecture notes:\n{detailed_context}\n\n'
            f'Local draft summary:\n{local_summary or "N/A"}'
        )

    refined = _generate_text(prompt)
    if refined:
        return refined

    # Retry with a lighter prompt if full-context refinement fails.
    if local_summary:
        if mode == 'brief':
            retry_prompt = (
                'Rewrite the summary below as exactly 3 bullet points while preserving key facts. '
                'Keep each bullet concise and do not add a title.\n\n'
                f'Summary draft:\n{local_summary}'
            )
        elif mode == 'standard':
            retry_prompt = (
                'Rewrite the summary below into a short paragraph followed by a "Key Points" list of 3 to 5 bullets. '
                'Do not add new facts or a title.\n\n'
                f'Summary draft:\n{local_summary}'
            )
        else:
            retry_prompt = (
                'Rewrite the summary below using the sections Overview, Key Concepts, Important Details, and Takeaways. '
                'Improve clarity while preserving all key factual points and not adding new facts. Do not add a title.\n\n'
                f'Summary draft:\n{local_summary}'
            )
        refined = _generate_text(retry_prompt)
        if refined:
            return refined

    return local_summary


def generate_gemini_questions(text: str) -> List[str]:
    prompt = (
        'Create 5 short study questions from the lecture notes. '
        'Return one question per line, with no numbering and no extra commentary.\n\n'
        f'Lecture notes:\n{text[:12000]}'
    )
    return _extract_nonempty_lines(_generate_text(prompt))


def generate_gemini_retry_question(question_text: str, concept_name: str = '') -> str:
    concept_clause = f' for the concept {concept_name}' if concept_name else ''
    prompt = (
        'Rewrite the quiz question as a similar but not identical retry question. '
        'Return only the rewritten question.\n\n'
        f'Original question:{concept_clause}\n{question_text}'
    )
    return _generate_text(prompt)