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
                'max_output_tokens': 400,
            },
        )
    except Exception as exc:
        logger.warning('Gemini content generation failed: %s', exc)
        return ''

    return (getattr(response, 'text', '') or '').strip()


def generate_gemini_summary(text: str, local_summary: str = '') -> str:
    prompt = (
        'Summarize the lecture notes in 4 to 6 concise sentences. '
        'Keep the meaning accurate and avoid adding facts that are not present.\n\n'
        f'Lecture notes:\n{text[:12000]}\n\n'
        f'Local draft summary:\n{local_summary or "N/A"}'
    )
    refined = _generate_text(prompt)
    if refined:
        return refined

    # Retry with a lighter prompt if full-context refinement fails.
    if local_summary:
        retry_prompt = (
            'Polish the summary below in 4 to 6 concise sentences. '
            'Improve clarity and flow without adding new facts.\n\n'
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