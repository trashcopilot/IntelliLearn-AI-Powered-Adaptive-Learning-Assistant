from typing import List

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


def summarize_text(text: str) -> str:
    if not text:
        return 'No text available for summarization.'

    summarizer = _get_summarizer()
    if not summarizer:
        return text[:500] + ('...' if len(text) > 500 else '')

    snippet = text[:2000]
    result = summarizer(snippet, max_length=180, min_length=50, do_sample=False)
    return result[0]['summary_text']


def generate_questions(text: str) -> List[str]:
    """Question generation placeholder using simple heuristics until QG model is integrated."""
    if not text:
        return ['What is the main idea of this lecture?']

    sentences = [s.strip() for s in text.split('.') if len(s.split()) > 6]
    questions = []
    for sentence in sentences[:10]:
        questions.append(f'Explain: {sentence}?')

    return questions or ['What is the main idea of this lecture?']


def generate_similar_question(question_text: str, concept_name: str = '') -> str:
    """Return a retry question variant for the same concept."""
    base = (question_text or '').strip().rstrip('?')
    if not base:
        return f'Explain one core idea from {concept_name or "this concept"} in your own words.'
    if concept_name:
        return f'Retry: {base} with reference to {concept_name}?'
    return f'Retry: {base}?'
