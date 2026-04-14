import logging
import os
import re
import threading
import time
from functools import lru_cache
from typing import List

logger = logging.getLogger(__name__)

DEFAULT_MODEL_NAME = os.getenv('GEMINI_MODEL', 'gemini-3-flash')
_GEMINI_AUTH_DISABLED = False
_MAX_TRANSIENT_RETRIES = 2
_GEMINI_CALL_SEMAPHORE = threading.Semaphore(1)  # Max 1 concurrent Gemini API call
_LAST_SUMMARY_FAILURE_REASON = 'none'

# T5 model caching
_T5_SUMMARIZER_CACHE = None
_T5_DEVICE_CACHE = None


def _set_last_summary_failure_reason(reason: str) -> None:
	global _LAST_SUMMARY_FAILURE_REASON
	_LAST_SUMMARY_FAILURE_REASON = reason


def get_last_summary_failure_reason() -> str:
	return _LAST_SUMMARY_FAILURE_REASON


def _get_gemini_api_key() -> str:
	for env_name in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'GOOGLE_GENAI_API_KEY'):
		api_key = os.getenv(env_name)
		if api_key:
			return api_key
	return ''


def _is_auth_error(exc: Exception) -> bool:
	message = str(exc).lower()
	return any(
		token in message
		for token in (
			'api_key_invalid',
			'api key invalid',
			'api key expired',
			'expired',
			'unauthorized',
			'permission denied',
		)
	)


def _is_transient_error(exc: Exception) -> bool:
	message = str(exc).lower()
	return any(
		token in message
		for token in (
			'503',
			'unavailable',
			'deadline exceeded',
			'timeout',
			'temporarily',
			'high demand',
			'rate limit',
			'resource exhausted',
		)
	)


def _disable_gemini_for_process() -> None:
	global _GEMINI_AUTH_DISABLED
	_GEMINI_AUTH_DISABLED = True


def gemini_is_configured() -> bool:
	return bool(_get_gemini_api_key()) and not _GEMINI_AUTH_DISABLED


def t5_is_available() -> bool:
	"""Check if T5 model can be loaded."""
	try:
		import torch
		from transformers import pipeline
		return True
	except ImportError:
		return False


@lru_cache(maxsize=1)
def _get_new_sdk_client():
	api_key = _get_gemini_api_key()
	if not api_key:
		return None

	try:
		from google import genai
	except Exception as exc:
		logger.info('google-genai package is unavailable: %s', exc)
		return None

	try:
		client = genai.Client(api_key=api_key)
		return client
	except Exception as exc:
		logger.warning('Gemini new SDK client initialization failed: %s', exc)
		if _is_auth_error(exc):
			_disable_gemini_for_process()
		return None


def _get_t5_summarizer():
	"""Lazy-load T5 summarizer on first use."""
	global _T5_SUMMARIZER_CACHE
	if _T5_SUMMARIZER_CACHE is not None:
		return _T5_SUMMARIZER_CACHE

	try:
		from transformers import pipeline
		import torch

		device = 0 if torch.cuda.is_available() else -1
		_T5_SUMMARIZER_CACHE = pipeline('summarization', model='t5-base', device=device)
		logger.info('T5 summarizer loaded successfully')
		return _T5_SUMMARIZER_CACHE
	except Exception as exc:
		logger.warning('T5 summarizer failed to load: %s', exc)
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


def _split_text_chunks(text: str, chunk_size: int = 5500, overlap: int = 450, max_chunks: int = 6) -> List[str]:
	cleaned = (text or '').strip()
	if not cleaned:
		return []
	if len(cleaned) <= chunk_size:
		return [cleaned]

	chunks = []
	step = max(chunk_size - overlap, 1)
	start = 0
	text_len = len(cleaned)

	while start < text_len and len(chunks) < max_chunks:
		end = min(start + chunk_size, text_len)
		if end < text_len:
			paragraph_end = cleaned.rfind('\n\n', start + int(chunk_size * 0.55), end)
			sentence_end = cleaned.rfind('.', start + int(chunk_size * 0.55), end)
			if paragraph_end != -1:
				end = paragraph_end + 2
			elif sentence_end != -1:
				end = sentence_end + 1

		chunk = cleaned[start:end].strip()
		if chunk:
			chunks.append(chunk)

		if end >= text_len:
			break
		start = max(end - overlap, start + step)

	return chunks


def _generate_text(prompt: str, temperature: float = 0.2, max_output_tokens: int = 900) -> str:
	client = _get_new_sdk_client()
	if client is not None:
		for attempt in range(_MAX_TRANSIENT_RETRIES + 1):
			try:
				with _GEMINI_CALL_SEMAPHORE:
					response = client.models.generate_content(
						model=DEFAULT_MODEL_NAME,
						contents=prompt,
						config={
							'temperature': temperature,
							'max_output_tokens': max_output_tokens,
						},
					)
				return (getattr(response, 'text', '') or '').strip()
			except Exception as exc:
				if _is_auth_error(exc):
					logger.warning('Gemini new SDK auth failed: %s', exc)
					_disable_gemini_for_process()
					return ''

				if _is_transient_error(exc) and attempt < _MAX_TRANSIENT_RETRIES:
					backoff_seconds = attempt + 1
					logger.warning(
						'Gemini temporarily unavailable (attempt %s/%s): %s. Retrying in %ss.',
						attempt + 1,
						_MAX_TRANSIENT_RETRIES + 1,
						exc,
						backoff_seconds,
					)
					time.sleep(backoff_seconds)
					continue

				logger.warning('Gemini new SDK generation failed: %s', exc)
				return ''

	return ''


def _normalize_similarity_key(text: str) -> str:
	lowered = (text or '').strip().lower()
	lowered = re.sub(r'[^a-z0-9\s]', '', lowered)
	lowered = re.sub(r'\s+', ' ', lowered).strip()
	return lowered


def _dedupe_bullets(summary_text: str) -> str:
	lines = (summary_text or '').splitlines()
	seen = set()
	rendered = []

	for raw_line in lines:
		line = raw_line.rstrip()
		stripped = line.strip()
		if not stripped:
			rendered.append('')
			continue

		if stripped.startswith(('-', '*')):
			item = stripped[1:].strip()
			key = _normalize_similarity_key(item)
			if not key or key in seen:
				continue
			seen.add(key)
			rendered.append(f'- {item}')
			continue

		rendered.append(line)

	compact = '\n'.join(rendered)
	compact = re.sub(r'\n{3,}', '\n\n', compact)
	return compact.strip()


def _is_valid_summary_structure(summary_text: str, mode: str) -> bool:
	text = (summary_text or '').strip()
	if not text:
		return False

	lines = [line.strip() for line in text.splitlines() if line.strip()]
	bullet_lines = [line for line in lines if line.startswith(('-', '*'))]

	# Keep a minimum quality floor: at least 100 chars and some substance
	if len(text) < 100:
		return False

	# Just check: has some content, has some bullets, has multiple lines
	# Don't be too strict about section names or layout
	has_bullets = len(bullet_lines) >= 1
	has_multiple_lines = len(lines) >= 3
	minimum_length = len(text) >= 100

	return has_bullets and has_multiple_lines and minimum_length


def _build_summary_repair_prompt(mode: str, broken_summary: str, notes_context: str, local_summary: str) -> str:
	if mode == 'brief':
		format_rules = (
			'Use clear headings. Use one paragraph for abstract and bullet points for details. '
			'Headings can be flexible; just keep sections clearly separated. '
			'No title, no preface, no outro.'
		)
	elif mode == 'standard':
		format_rules = (
			'Use clear headings (any naming works). Use bullet points for key information. '
			'Keep structure simple and readable.'
		)
	else:
		format_rules = (
			'Use clear headings (names can vary). Structure with bullet points. '
			'Simple, readable organization.'
		)

	compact_notes = _summarization_context(notes_context, 10000)
	return (
		'Repair the summary so it is systematic, concise, and well-structured.\n'
		f'Formatting rules: {format_rules}\n'
		'Content rules: keep only high-signal, source-supported facts; remove filler, repetition, and unnecessary detail.\n'
		'Do not add any title or commentary.\n\n'
		f'Source notes:\n{compact_notes}\n\n'
		f'Broken summary:\n{broken_summary or "N/A"}\n\n'
		f'Local draft summary:\n{local_summary or "N/A"}'
	)


def _build_structured_fallback(summary_text: str, mode: str) -> str:
	lines = _extract_nonempty_lines(summary_text)
	if not lines:
		lines = ['No key points could be extracted from the source notes.']

	if mode == 'brief':
		abstract = ' '.join(lines[:2]) if len(lines) > 1 else lines[0]
		clusters = lines[2:5] or lines[:3]
		anchors = lines[5:7] or lines[3:5]
		return (
			f'Executive Abstract\n{abstract}\n\n'
			f'Thematic Clusters\n' + '\n'.join(f'- {item}' for item in clusters[:3]) + '\n\n'
			f'Technical Anchors\n' + ('\n'.join(f'- {item}' for item in anchors[:2]) or '- None')
		)

	if mode == 'standard':
		context = lines[:2] or lines[:1]
		findings = lines[2:7] or lines[:4]
		implications = lines[7:10] or lines[4:7] or ['Review key concepts and apply them in practice.']
		return (
			'Core Context\n' + '\n'.join(f'- {item}' for item in context[:2]) + '\n\n'
			'Key Findings\n' + '\n'.join(f'- {item}' for item in findings[:5]) + '\n\n'
			'Next Steps/Implications\n' + '\n'.join(f'- {item}' for item in implications[:3])
		)

	abstract = ' '.join(lines[:2]) if len(lines) > 1 else lines[0]
	clusters = lines[2:6] or lines[:4]
	anchors = lines[6:9] or lines[4:7]
	context = lines[9:12] or lines[7:10]
	findings = lines[12:17] or lines[10:15] or lines[:5]
	implications = lines[17:21] or lines[15:19] or ['Use the findings to guide applied practice or further study.']
	return (
		f'Executive Abstract\n{abstract}\n\n'
		'Thematic Clusters\n' + '\n'.join(f'- {item}' for item in clusters[:4]) + '\n\n'
		'Technical Anchors\n' + ('\n'.join(f'- {item}' for item in anchors[:3]) or '- None') + '\n\n'
		'Core Context\n' + ('\n'.join(f'- {item}' for item in context[:3]) or '- None') + '\n\n'
		'Key Findings\n' + '\n'.join(f'- {item}' for item in findings[:5]) + '\n\n'
		'Next Steps/Implications\n' + '\n'.join(f'- {item}' for item in implications[:4])
	)


# ===== GEMINI API WRAPPER =====


def generate_gemini_summary(text: str, local_summary: str = '', summary_mode: str = 'detailed') -> str:
	mode = (summary_mode or '').strip().lower()
	if mode not in {'brief', 'standard', 'detailed'}:
		mode = 'detailed'

	chunks = _split_text_chunks(text, max_chunks=3)
	if not chunks:
		_set_last_summary_failure_reason('empty_input')
		return local_summary

	extracted_notes: List[str] = []
	# Keep the map pass only for very long notes to reduce API call pressure.
	if len(text) > 18000 and len(chunks) > 1:
		for index, chunk in enumerate(chunks, start=1):
			map_prompt = (
				f'You are an expert academic curator extracting high-signal content from lecture notes chunk {index}/{len(chunks)}.\n'
				'Prioritize contextual relationships (cause-effect, dependencies, hierarchy), not isolated keyword lists.\n'
				'Output strictly in this format:\n'
				'Structural Markers:\n- ...\n'
				'Thematic Clusters:\n- ...\n'
				'Technical Anchors:\n- ...\n'
				'Implications:\n- ...\n'
				'Rules:\n'
				'- Keep only information directly supported by this chunk.\n'
				'- Explain how concepts connect when possible; do not only list topics.\n'
				'- Omit examples, anecdotes, repetition, and filler text unless essential for understanding.\n'
				'- If a section has no valid content, write "- None".\n'
				'- Do not add a title or any commentary outside the required sections.\n\n'
				f'Lecture notes chunk:\n{chunk}'
			)
			mapped = _generate_text(map_prompt, temperature=0.1, max_output_tokens=420)
			if mapped:
				extracted_notes.append(mapped)

	notes_context = '\n\n'.join(extracted_notes).strip()
	if not notes_context:
		max_chars_by_mode = {'brief': 10000, 'standard': 13000, 'detailed': 15000}
		notes_context = _summarization_context(text, max_chars_by_mode[mode])

	if mode == 'brief':
		prompt = (
			"Summarize the following educational material into a brief, high-density format.\n\n"
			"Output structure (use bullet points and clear sections):\n"
			"- Core idea: 1-2 sentences of main thesis\n"
			"- Key concepts: 3-5 bullet points of main topics\n\n"
			"Guidelines:\n"
			"1. Be concise. Aim for 150-200 words total.\n"
			"2. Use only facts from the source notes.\n"
			"3. Use simple, clear language.\n"
			"4. No introductions or conclusions needed.\n\n"
			f"Source notes:\n{notes_context}"
		)
	elif mode == 'standard':
		prompt = (
			"Summarize the following educational material into a standard study format.\n\n"
			"Output structure (use bullet points and clear sections):\n"
			"- Context: 2-3 sentences explaining what this is about\n"
			"- Key points: 5-7 bullet points of important facts\n"
			"- Application: 2-3 sentences on how this matters or applies\n\n"
			"Guidelines:\n"
			"1. Aim for 200-300 words total.\n"
			"2. Use only facts from the source notes.\n"
			"3. Each bullet point should be one clear fact.\n"
			"4. Use simple language.\n\n"
			f"Source notes:\n{notes_context}"
		)
	else:
		prompt = (
			"Create a detailed summary of the following educational material using clear sections.\n\n"
			"Output structure (use bullet points and clear sections):\n"
			"- Overview: 2-3 sentences of main focus\n"
			"- Main concepts: 5-8 bullet points of core ideas\n"
			"- Technical details: 3-5 bullet points of key terms, formulas, or facts\n"
			"- Practical application: 2-3 sentences on why this matters\n"
			"- Next steps: 1-2 sentences on logical next concepts\n\n"
			"Guidelines:\n"
			"1. Aim for 300-400 words total.\n"
			"2. Use only facts from the source notes.\n"
			"3. Be complete but concise.\n"
			"4. Simple, clear language.\n\n"
			f"Source notes:\n{notes_context}"
		)

	refined = _generate_text(prompt, temperature=0.25, max_output_tokens=600)
	refined = _dedupe_bullets(refined)
	if _is_valid_summary_structure(refined, mode):
		_set_last_summary_failure_reason('none')
		return refined

	if refined:
		repair_prompt = _build_summary_repair_prompt(mode, refined, notes_context, local_summary)
		repaired = _generate_text(repair_prompt, temperature=0.2, max_output_tokens=600)
		repaired = _dedupe_bullets(repaired)
		if _is_valid_summary_structure(repaired, mode):
			_set_last_summary_failure_reason('none')
			return repaired

		if repaired:
			_set_last_summary_failure_reason('structure_validation_failed')
		else:
			_set_last_summary_failure_reason('repair_empty_response')
		return ''

	_set_last_summary_failure_reason('empty_model_response')

	return ''


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


def generate_gemini_micro_lesson(
	question_text: str,
	student_answer: str,
	correct_answer: str,
	concept_name: str = '',
) -> str:
	concept_clause = f' for the concept "{concept_name}"' if concept_name else ''
	prompt = (
		'You are a supportive tutor. Create a concise micro-lesson after a wrong quiz answer.\n'
		'Output plain text only with no markdown heading.\n'
		'Use this structure in short sentences:\n'
		'1) Explain what was misunderstood.\n'
		'2) Clarify the concept with 2 to 3 short teaching sentences.\n'
		'3) Give one practical action tip for the next attempt.\n'
		'Keep it under 120 words and use simple language.\n\n'
		f'Question{concept_clause}: {question_text}\n'
		f'Student answer: {student_answer or "(empty)"}\n'
		f'Correct answer: {correct_answer}'
	)
	return _generate_text(prompt, temperature=0.2, max_output_tokens=220)


# ===== T5 LOCAL MODEL WRAPPER =====


def generate_t5_summary(text: str, summary_mode: str = 'detailed') -> str:
	"""Generate summary using local T5 model."""
	if not text or len(text.strip()) < 50:
		return ''

	summarizer = _get_t5_summarizer()
	if not summarizer:
		logger.warning('T5 summarizer unavailable')
		return ''

	try:
		# T5 requires min 50 words and max token length
		words = text.split()
		if len(words) < 50:
			return ''

		# Truncate to avoid T5 token limit (512 tokens)
		truncated = ' '.join(words[:300])  # ~300 words ~ 400 tokens

		# Adjust max_length based on mode
		if summary_mode == 'brief':
			max_length = 60
			min_length = 30
		elif summary_mode == 'standard':
			max_length = 150
			min_length = 80
		else:
			max_length = 250
			min_length = 150

		summary_list = summarizer(truncated, max_length=max_length, min_length=min_length, do_sample=False)
		if summary_list and len(summary_list) > 0:
			summary_text = summary_list[0].get('summary_text', '').strip()
			if len(summary_text) >= 50:
				return summary_text
	except Exception as exc:
		logger.warning('T5 summary generation failed: %s', exc)

	return ''


def generate_t5_questions(text: str) -> List[str]:
	"""Generate questions using T5 (question generation pipeline not standard, fallback to sentence extraction)."""
	try:
		from transformers import pipeline

		# T5 doesn't have a standard question-generation pipeline like Hugging Face
		# Instead, we'll extract key sentences and convert them to questions
		sentences = [s.strip() + '?' for s in text.split('.') if len(s.split()) > 6]
		return sentences[:10] if sentences else ['What are the main topics covered?']
	except Exception as exc:
		logger.warning('T5 question generation failed: %s', exc)
		return ['What are the main topics covered?']


def generate_t5_retry_question(question_text: str, concept_name: str = '') -> str:
	"""Generate retry question using T5 (uses simple rephrasing)."""
	try:
		# T5 can do paraphrasing, but we'll use a simpler approach
		base = (question_text or '').strip().rstrip('?')
		if not base:
			return ''

		if concept_name:
			return f'Explain how {concept_name} relates to: {base}?'
		return f'Can you rephrase your answer to: {base}?'
	except Exception as exc:
		logger.warning('T5 retry question generation failed: %s', exc)
		return ''


def generate_t5_micro_lesson(
	question_text: str,
	student_answer: str,
	correct_answer: str,
	concept_name: str = '',
) -> str:
	"""Generate micro-lesson using T5 (uses template-based approach)."""
	try:
		concept_hint = concept_name or 'this concept'
		return (
			f'Your answer missed the key aspect of {concept_hint}. '
			f'The correct answer was: {correct_answer}. '
			'Think about the core definitions and how they apply to the question. '
			'Try again with this understanding.'
		)
	except Exception as exc:
		logger.warning('T5 micro-lesson generation failed: %s', exc)
		return ''
