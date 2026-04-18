import logging
import os
import time
from typing import Dict, List

from .ai_models import (
	generate_gemini_constructed_questions,
	generate_gemini_micro_lesson,
	generate_gemini_mcq_questions,
	generate_gemini_questions,
	generate_gemini_retry_question,
	generate_gemini_summary,
	gemini_is_configured,
	generate_local_constructed_questions,
	generate_local_micro_lesson,
	generate_local_mcq_questions,
	generate_local_questions,
	generate_local_retry_question,
	generate_local_summary,
	local_fallback_is_configured,
)

logger = logging.getLogger(__name__)

_VALID_SUMMARY_MODES = {'brief', 'standard', 'detailed'}
_GEMINI_PRIMARY_ATTEMPTS = max(1, int(os.getenv('GEMINI_PRIMARY_ATTEMPTS', '2')))
_GEMINI_PRIMARY_RETRY_DELAY = float(os.getenv('GEMINI_PRIMARY_RETRY_DELAY', '0.8'))
_LOCAL_FALLBACK_ATTEMPTS = max(1, int(os.getenv('LOCAL_FALLBACK_ATTEMPTS', '1')))
_LOCAL_FALLBACK_RETRY_DELAY = float(os.getenv('LOCAL_FALLBACK_RETRY_DELAY', '0.5'))


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


def _run_local_fallback(label: str, call):
	if not local_fallback_is_configured():
		return None

	for attempt in range(1, _LOCAL_FALLBACK_ATTEMPTS + 1):
		try:
			result = call()
			if result:
				return result
			if attempt < _LOCAL_FALLBACK_ATTEMPTS:
				_trace_ai(
					f'⚠️ {label} local retry: empty response (attempt {attempt}/{_LOCAL_FALLBACK_ATTEMPTS}). '
					f'Retrying in {_LOCAL_FALLBACK_RETRY_DELAY:.1f}s.'
				)
				time.sleep(_LOCAL_FALLBACK_RETRY_DELAY)
		except Exception as exc:
			logger.warning('%s local call failed (attempt %s/%s): %s', label, attempt, _LOCAL_FALLBACK_ATTEMPTS, exc)
			if attempt < _LOCAL_FALLBACK_ATTEMPTS:
				_trace_ai(
					f'⚠️ {label} local retry: {exc} (attempt {attempt}/{_LOCAL_FALLBACK_ATTEMPTS}). '
					f'Retrying in {_LOCAL_FALLBACK_RETRY_DELAY:.1f}s.'
				)
				time.sleep(_LOCAL_FALLBACK_RETRY_DELAY)

	return None


def _normalize_summary_mode(summary_mode: str) -> str:
	mode = (summary_mode or '').strip().lower()
	if mode not in _VALID_SUMMARY_MODES:
		return 'detailed'
	return mode


def summarize_text(text: str, summary_mode: str = 'detailed') -> str:
	if not text:
		_trace_ai('ℹ️ AI Summary Skipped: no text available.')
		return ''

	mode = _normalize_summary_mode(summary_mode)
	_trace_ai(f'🚀 AI Summary Started: mode={mode}, chars={len(text)}')

	refined = _run_gemini_primary('AI Summary', lambda: generate_gemini_summary(text, summary_mode=mode))
	if refined and refined.strip():
		_trace_ai(f'✅ AI Summary Success: Gemini used for mode={mode}')
		return refined.strip()

	_trace_ai(f'⚠️ AI Summary Gemini exhausted, trying local fallback for mode={mode}')
	local_result = _run_local_fallback('AI Summary', lambda: generate_local_summary(text, summary_mode=mode))
	if local_result and local_result.strip():
		_trace_ai(f'✅ AI Summary Success: local fallback used for mode={mode}')
		return local_result.strip()

	_trace_ai(f'⚠️ AI Summary local fallback exhausted for mode={mode}')
	return ''


def generate_questions(text: str) -> List[str]:
	if not text:
		_trace_ai('ℹ️ AI Question Generation Skipped: no text available.')
		return []

	questions = _run_gemini_primary('AI Question Generation', lambda: generate_gemini_questions(text))
	if questions:
		_trace_ai(f'✅ AI Question Generation Success: Gemini produced {len(questions[:10])} questions')
		return questions[:10]

	_trace_ai('⚠️ AI Question Generation Gemini exhausted, trying local fallback')
	local_questions = _run_local_fallback('AI Question Generation', lambda: generate_local_questions(text))
	if local_questions:
		_trace_ai(f'✅ AI Question Generation Success: local fallback produced {len(local_questions[:10])} questions')
		return local_questions[:10]

	_trace_ai('⚠️ AI Question Generation local fallback exhausted')
	return []


def generate_constructed_questions(text: str, count: int = 6) -> List[str]:
	if not text:
		_trace_ai('ℹ️ AI Constructed Question Generation Skipped: no text available.')
		return []

	questions = _run_gemini_primary(
		'AI Constructed Question Generation',
		lambda: generate_gemini_constructed_questions(text, count=count),
	)
	if questions:
		_trace_ai(f'✅ AI Constructed Question Generation Success: Gemini produced {len(questions[:count])} questions')
		return questions[:count]

	_trace_ai('⚠️ AI Constructed Question Generation Gemini exhausted, trying local fallback')
	local_questions = _run_local_fallback('AI Constructed Question Generation', lambda: generate_local_constructed_questions(text, count=count))
	if local_questions:
		_trace_ai(f'✅ AI Constructed Question Generation Success: local fallback produced {len(local_questions[:count])} questions')
		return local_questions[:count]

	_trace_ai('⚠️ AI Constructed Question Generation local fallback exhausted')
	return []


def generate_mcq_questions(text: str, count: int = 6) -> List[Dict[str, str]]:
	if not text:
		_trace_ai('ℹ️ AI MCQ Generation Skipped: no text available.')
		return []

	mcq_items = _run_gemini_primary(
		'AI MCQ Generation',
		lambda: generate_gemini_mcq_questions(text, count=count),
	)
	if mcq_items:
		_trace_ai(f'✅ AI MCQ Generation Success: Gemini produced {len(mcq_items[:count])} questions')
		return mcq_items[:count]

	_trace_ai('⚠️ AI MCQ Generation Gemini exhausted, trying local fallback')
	local_mcq_items = _run_local_fallback('AI MCQ Generation', lambda: generate_local_mcq_questions(text, count=count))
	if local_mcq_items:
		_trace_ai(f'✅ AI MCQ Generation Success: local fallback produced {len(local_mcq_items[:count])} questions')
		return local_mcq_items[:count]

	_trace_ai('⚠️ AI MCQ Generation local fallback exhausted')
	return []


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

	_trace_ai('⚠️ AI Retry Question Gemini exhausted, trying local fallback')
	local_retry = _run_local_fallback('AI Retry Question', lambda: generate_local_retry_question(base, concept_name))
	if local_retry and local_retry.strip():
		_trace_ai('✅ AI Retry Question Success: local fallback used')
		return local_retry.strip()

	_trace_ai('⚠️ AI Retry Question local fallback exhausted')
	return ''


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

	_trace_ai('⚠️ AI Micro-Lesson Gemini exhausted, trying local fallback')
	local_lesson = _run_local_fallback(
		'AI Micro-Lesson',
		lambda: generate_local_micro_lesson(
			question_text=question_text,
			student_answer=student_answer,
			correct_answer=correct_answer,
			concept_name=concept_name,
		),
	)
	if local_lesson and local_lesson.strip():
		_trace_ai('✅ AI Micro-Lesson Success: local fallback used')
		return local_lesson.strip()

	_trace_ai('⚠️ AI Micro-Lesson local fallback exhausted')
	return ''
