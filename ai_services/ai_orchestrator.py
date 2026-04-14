import logging
import os
import time
from typing import List

from .ai_models import (
	generate_gemini_micro_lesson,
	generate_gemini_questions,
	generate_gemini_retry_question,
	generate_gemini_summary,
	generate_t5_micro_lesson,
	generate_t5_questions,
	generate_t5_retry_question,
	generate_t5_summary,
	gemini_is_configured,
	t5_is_available,
)

logger = logging.getLogger(__name__)

_VALID_SUMMARY_MODES = {'brief', 'standard', 'detailed'}
_GEMINI_PRIMARY_ATTEMPTS = max(1, int(os.getenv('GEMINI_PRIMARY_ATTEMPTS', '2')))
_GEMINI_PRIMARY_RETRY_DELAY = float(os.getenv('GEMINI_PRIMARY_RETRY_DELAY', '0.8'))
_T5_FALLBACK_ATTEMPTS = max(1, int(os.getenv('T5_FALLBACK_ATTEMPTS', '1')))
_T5_FALLBACK_RETRY_DELAY = float(os.getenv('T5_FALLBACK_RETRY_DELAY', '0.5'))


def _trace_ai(message: str) -> None:
	logger.info(message)
	print(message, flush=True)


def _run_gemini_primary(label: str, call):
	if not gemini_is_configured():
		return None

	for attempt in range(1, _GEMINI_PRIMARY_ATTEMPTS + 1):
		try:
			result = call()
			if result:
				return result
			if attempt < _GEMINI_PRIMARY_ATTEMPTS:
				_trace_ai(
					f'⚠️ {label} Gemini retry: empty response (attempt {attempt}/{_GEMINI_PRIMARY_ATTEMPTS}). '
					f'Retrying in {_GEMINI_PRIMARY_RETRY_DELAY:.1f}s.'
				)
				time.sleep(_GEMINI_PRIMARY_RETRY_DELAY)
		except Exception as exc:
			logger.warning('%s Gemini call failed (attempt %s/%s): %s', label, attempt, _GEMINI_PRIMARY_ATTEMPTS, exc)
			if attempt < _GEMINI_PRIMARY_ATTEMPTS:
				_trace_ai(
					f'⚠️ {label} Gemini retry: {exc} (attempt {attempt}/{_GEMINI_PRIMARY_ATTEMPTS}). '
					f'Retrying in {_GEMINI_PRIMARY_RETRY_DELAY:.1f}s.'
				)
				time.sleep(_GEMINI_PRIMARY_RETRY_DELAY)

	return None


def _run_t5_fallback(label: str, call):
	"""Try T5 as fallback when Gemini fails."""
	if not t5_is_available():
		return None

	for attempt in range(1, _T5_FALLBACK_ATTEMPTS + 1):
		try:
			result = call()
			if result:
				_trace_ai(f'✅ {label} T5 fallback succeeded')
				return result
			if attempt < _T5_FALLBACK_ATTEMPTS:
				_trace_ai(
					f'⚠️ {label} T5 retry: empty response (attempt {attempt}/{_T5_FALLBACK_ATTEMPTS}). '
					f'Retrying in {_T5_FALLBACK_RETRY_DELAY:.1f}s.'
				)
				time.sleep(_T5_FALLBACK_RETRY_DELAY)
		except Exception as exc:
			logger.warning('%s T5 call failed (attempt %s/%s): %s', label, attempt, _T5_FALLBACK_ATTEMPTS, exc)
			if attempt < _T5_FALLBACK_ATTEMPTS:
				_trace_ai(
					f'⚠️ {label} T5 retry: {exc} (attempt {attempt}/{_T5_FALLBACK_ATTEMPTS}). '
					f'Retrying in {_T5_FALLBACK_RETRY_DELAY:.1f}s.'
				)
				time.sleep(_T5_FALLBACK_RETRY_DELAY)

	return None


def _normalize_summary_mode(summary_mode: str) -> str:
	mode = (summary_mode or '').strip().lower()
	if mode not in _VALID_SUMMARY_MODES:
		return 'detailed'
	return mode


def _sentences(text: str) -> List[str]:
	raw = [s.strip() for s in (text or '').replace('\n', ' ').split('.')]
	return [s for s in raw if s]


def _local_summary_fallback(text: str, mode: str) -> str:
	cleaned = (text or '').strip()
	if not cleaned:
		return 'No text available for summarization.'

	sentences = _sentences(cleaned)
	if not sentences:
		return cleaned[:700] + ('...' if len(cleaned) > 700 else '')

	if mode == 'brief':
		abstract = ' '.join(sentences[:2])
		bullets = sentences[2:5] or sentences[:3]
		return (
			f'Executive Abstract\n{abstract}\n\n'
			'Key Points\n' + '\n'.join(f'- {item}.' if not item.endswith('.') else f'- {item}' for item in bullets)
		)

	if mode == 'standard':
		context = sentences[:2]
		findings = sentences[2:7] or sentences[:4]
		return (
			'Core Context\n' + '\n'.join(f'- {item}.' if not item.endswith('.') else f'- {item}' for item in context) + '\n\n'
			'Key Findings\n' + '\n'.join(f'- {item}.' if not item.endswith('.') else f'- {item}' for item in findings)
		)

	abstract = ' '.join(sentences[:2])
	findings = sentences[2:10] or sentences[:6]
	return (
		f'Executive Abstract\n{abstract}\n\n'
		'Key Findings\n' + '\n'.join(f'- {item}.' if not item.endswith('.') else f'- {item}' for item in findings)
	)


def summarize_text(text: str, summary_mode: str = 'detailed') -> str:
	if not text:
		_trace_ai('ℹ️ AI Summary Skipped: no text available.')
		return 'No text available for summarization.'

	mode = _normalize_summary_mode(summary_mode)
	_trace_ai(f'🚀 AI Summary Started: mode={mode}, chars={len(text)}')

	# Tier 1: Try Gemini
	refined = _run_gemini_primary('AI Summary', lambda: generate_gemini_summary(text, summary_mode=mode))
	if refined and refined.strip():
		_trace_ai(f'✅ AI Summary Success: Gemini used for mode={mode}')
		return refined.strip()

	# Tier 2: Try T5 local model
	_trace_ai(f'⚠️ AI Summary Gemini exhausted, trying T5 fallback for mode={mode}')
	t5_result = _run_t5_fallback('AI Summary', lambda: generate_t5_summary(text, summary_mode=mode))
	if t5_result and t5_result.strip():
		_trace_ai(f'✅ AI Summary Success: T5 used for mode={mode}')
		return t5_result.strip()

	# Tier 3: Local deterministic fallback
	_trace_ai(f'⚠️ AI Summary T5 unavailable/exhausted, using local heuristic fallback for mode={mode}')
	fallback = _local_summary_fallback(text, mode)
	_trace_ai(f'✅ AI Summary Success: local fallback used for mode={mode}')
	return fallback


def generate_questions(text: str) -> List[str]:
	if not text:
		_trace_ai('ℹ️ AI Question Generation Skipped: no text available.')
		return ['What is the main idea of this lecture?']

	# Tier 1: Try Gemini
	questions = _run_gemini_primary('AI Question Generation', lambda: generate_gemini_questions(text))
	if questions:
		_trace_ai(f'✅ AI Question Generation Success: Gemini produced {len(questions[:10])} questions')
		return questions[:10]

	# Tier 2: Try T5 local model
	_trace_ai('⚠️ AI Question Generation Gemini exhausted, trying T5 fallback')
	t5_questions = _run_t5_fallback('AI Question Generation', lambda: generate_t5_questions(text))
	if t5_questions:
		_trace_ai(f'✅ AI Question Generation Success: T5 produced {len(t5_questions[:10])} questions')
		return t5_questions[:10]

	# Tier 3: Local deterministic fallback
	_trace_ai('⚠️ AI Question Generation T5 unavailable/exhausted, using local fallback')
	sentences = [s.strip() for s in text.split('.') if len(s.split()) > 6]
	local = [f'Explain: {sentence}?' for sentence in sentences[:10]]
	result = local or ['What is the main idea of this lecture?']
	_trace_ai(f'✅ AI Question Generation Success: local fallback produced {len(result)} questions')
	return result


def generate_similar_question(question_text: str, concept_name: str = '') -> str:
	base = (question_text or '').strip().rstrip('?')
	if not base:
		_trace_ai('ℹ️ AI Retry Question Skipped: empty base question.')
		return f'Explain one core idea from {concept_name or "this concept"} in your own words.'

	# Tier 1: Try Gemini
	gemini_text = _run_gemini_primary(
		'AI Retry Question',
		lambda: generate_gemini_retry_question(base, concept_name),
	)
	if gemini_text:
		_trace_ai('✅ AI Retry Question Success: Gemini used')
		return gemini_text.strip()

	# Tier 2: Try T5 local model
	_trace_ai('⚠️ AI Retry Question Gemini exhausted, trying T5 fallback')
	t5_text = _run_t5_fallback(
		'AI Retry Question',
		lambda: generate_t5_retry_question(base, concept_name),
	)
	if t5_text:
		_trace_ai('✅ AI Retry Question Success: T5 used')
		return t5_text.strip()

	# Tier 3: Local deterministic fallback
	_trace_ai('⚠️ AI Retry Question T5 unavailable/exhausted, using local fallback')
	if concept_name:
		return f'Retry: {base} with reference to {concept_name}?'
	return f'Retry: {base}?'


def generate_micro_lesson(
	question_text: str,
	student_answer: str,
	correct_answer: str,
	concept_name: str = '',
	fallback_text: str = '',
) -> str:
	# Tier 1: Try Gemini
	lesson = _run_gemini_primary(
		'AI Micro-Lesson',
		lambda: generate_gemini_micro_lesson(
			question_text=question_text,
			student_answer=student_answer,
			correct_answer=correct_answer,
			concept_name=concept_name,
		),
	)
	if lesson and lesson.strip():
		_trace_ai('✅ AI Micro-Lesson Success: Gemini used')
		return lesson.strip()

	# Tier 2: Try T5 local model
	_trace_ai('⚠️ AI Micro-Lesson Gemini exhausted, trying T5 fallback')
	t5_lesson = _run_t5_fallback(
		'AI Micro-Lesson',
		lambda: generate_t5_micro_lesson(
			question_text=question_text,
			student_answer=student_answer,
			correct_answer=correct_answer,
			concept_name=concept_name,
		),
	)
	if t5_lesson and t5_lesson.strip():
		_trace_ai('✅ AI Micro-Lesson Success: T5 used')
		return t5_lesson.strip()

	# Tier 3a: Concept fallback
	_trace_ai('⚠️ AI Micro-Lesson T5 unavailable/exhausted')
	if fallback_text and fallback_text.strip():
		_trace_ai('✅ AI Micro-Lesson Success: concept fallback used')
		return fallback_text.strip()

	# Tier 3b: Local deterministic fallback
	concept_hint = concept_name or 'this concept'
	_trace_ai('✅ AI Micro-Lesson Success: deterministic fallback used')
	return (
		f'You missed the key idea in {concept_hint}. '
		f'The correct answer was: {correct_answer}. '
		'Focus on the defining terms and apply them directly to the question before answering again.'
	)
