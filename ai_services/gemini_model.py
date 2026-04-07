import logging
import os
from functools import lru_cache
from typing import List

logger = logging.getLogger(__name__)

DEFAULT_MODEL_NAME = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')


def gemini_is_configured() -> bool:
    return bool(os.getenv('GEMINI_API_KEY'))


@lru_cache(maxsize=1)
def _get_model():
    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        return None

    try:
        import google.generativeai as genai
    except Exception as exc:
        logger.warning('google-generativeai package is unavailable: %s', exc)
        return None

    genai.configure(api_key=api_key)
    return genai.GenerativeModel(DEFAULT_MODEL_NAME)


def _extract_nonempty_lines(text: str) -> List[str]:
    lines = []
    for raw_line in (text or '').splitlines():
        line = raw_line.strip().lstrip('-*0123456789. ').strip()
        if line:
            lines.append(line)
    return lines


def _generate_text(prompt: str) -> str:
    model = _get_model()
    if model is None:
        return ''

    try:
        response = model.generate_content(
            prompt,
            generation_config={
                'temperature': 0.2,
                'max_output_tokens': 800,
            },
        )
    except Exception as exc:
        logger.warning('Gemini content generation failed: %s', exc)
        return ''

    return (getattr(response, 'text', '') or '').strip()


def generate_gemini_summary(text: str, local_summary: str = '', summary_mode: str = 'detailed') -> str:
    mode = (summary_mode or '').strip().lower()
    if mode == 'brief':
        prompt = (
            'Create a brief study summary as exactly 3 bullet points. '
            'Each bullet should be one concise sentence. Keep only the most central ideas and do not add new facts. '
            'Do not add a title or extra commentary.\n\n'
            f'Lecture notes:\n{text[:10000]}\n\n'
            f'Local draft summary:\n{local_summary or "N/A"}'
        )
    elif mode == 'standard':
        prompt = (
            'Create a clear study summary with a short paragraph followed by a "Key Points" list of 3 to 5 bullets. '
            'Cover main concepts and key supporting details without adding new facts. '
            'Do not add a title or extra commentary.\n\n'
            f'Lecture notes:\n{text[:13000]}\n\n'
            f'Local draft summary:\n{local_summary or "N/A"}'
        )
    else:
        prompt = (
            'Create a detailed study summary with these sections exactly: Overview, Key Concepts, Important Details, and Takeaways. '
            'Use short paragraphs or bullets under each section. Preserve factual accuracy and do not add new information. '
            'Cover major concepts, important definitions, process steps, critical numbers/formulas, and notable limitations or caveats when present. '
            'Do not add a title or extra commentary.\n\n'
            f'Lecture notes:\n{text[:15000]}\n\n'
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