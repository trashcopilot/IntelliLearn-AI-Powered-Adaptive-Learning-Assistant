import logging
import json
import os
import re
import threading
import time
from functools import lru_cache
from typing import Dict, List
from urllib import error as urllib_error
from urllib import request as urllib_request

logger = logging.getLogger(__name__)

DEFAULT_MODEL_NAME = os.getenv('GEMINI_MODEL', 'gemini-3-flash-preview')
GEMINI_MODEL_FALLBACKS = os.getenv(
	'GEMINI_MODEL_FALLBACKS',
	'gemini-2.5-flash',
)
LOCAL_FALLBACK_MODEL = os.getenv('LOCAL_FALLBACK_MODEL', 'phi3:mini')
LOCAL_FALLBACK_URL = os.getenv('LOCAL_FALLBACK_URL', 'http://localhost:11434/api/generate')
LOCAL_FALLBACK_TIMEOUT_SECONDS = int(os.getenv('LOCAL_FALLBACK_TIMEOUT_SECONDS', '60'))
LOCAL_FALLBACK_TOP_P = float(os.getenv('LOCAL_FALLBACK_TOP_P', '0.9'))
LOCAL_FALLBACK_REPEAT_PENALTY = float(os.getenv('LOCAL_FALLBACK_REPEAT_PENALTY', '1.1'))
LOCAL_FALLBACK_NUM_CTX = int(os.getenv('LOCAL_FALLBACK_NUM_CTX', '4096'))
_GEMINI_AUTH_DISABLED = False
_MAX_TRANSIENT_RETRIES = 2
_GEMINI_CALL_SEMAPHORE = threading.Semaphore(1)  # Max 1 concurrent Gemini API call
_LAST_SUMMARY_FAILURE_REASON = 'none'


def local_fallback_is_configured() -> bool:
	return bool(LOCAL_FALLBACK_MODEL.strip())


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


def _is_model_not_found_error(exc: Exception) -> bool:
	message = str(exc).lower()
	return '404' in message and ('not found' in message or 'supported for generatecontent' in message)


def _get_gemini_model_candidates() -> List[str]:
	raw_models = [DEFAULT_MODEL_NAME]
	raw_models.extend(item.strip() for item in GEMINI_MODEL_FALLBACKS.split(',') if item.strip())

	seen = set()
	result = []
	for model in raw_models:
		normalized = model.replace('models/', '').strip()
		if normalized and normalized not in seen:
			seen.add(normalized)
			result.append(normalized)
	return result


def _disable_gemini_for_process() -> None:
	global _GEMINI_AUTH_DISABLED
	_GEMINI_AUTH_DISABLED = True


def gemini_is_configured() -> bool:
	return bool(_get_gemini_api_key()) and not _GEMINI_AUTH_DISABLED


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


def _call_local_model(prompt: str, temperature: float = 0.2, max_output_tokens: int = 900) -> str:
	if not local_fallback_is_configured():
		return ''

	payload = json.dumps(
		{
			'model': LOCAL_FALLBACK_MODEL,
			'prompt': prompt,
			'stream': False,
			'options': {
				'temperature': temperature,
				'top_p': LOCAL_FALLBACK_TOP_P,
				'repeat_penalty': LOCAL_FALLBACK_REPEAT_PENALTY,
				'num_ctx': LOCAL_FALLBACK_NUM_CTX,
				'num_predict': max_output_tokens,
			},
		}
	).encode('utf-8')
	req = urllib_request.Request(
		LOCAL_FALLBACK_URL,
		data=payload,
		headers={'Content-Type': 'application/json'},
		method='POST',
	)
	try:
		with urllib_request.urlopen(req, timeout=LOCAL_FALLBACK_TIMEOUT_SECONDS) as response:
			body = response.read().decode('utf-8', errors='replace')
			data = json.loads(body)
			text = (data.get('response', '') or '').strip()
			if not text:
				message = data.get('message') or {}
				text = (message.get('content', '') or '').strip()
			return text
	except urllib_error.HTTPError as exc:
		body = ''
		try:
			body = exc.read().decode('utf-8', errors='replace')
		except Exception:
			body = ''

		body_lower = body.lower()
		if exc.code == 404 and 'model' in body_lower and 'not found' in body_lower:
			logger.warning(
				'Local fallback model "%s" was not found at %s. Pull the model first (for Ollama: "ollama pull %s").',
				LOCAL_FALLBACK_MODEL,
				LOCAL_FALLBACK_URL,
				LOCAL_FALLBACK_MODEL,
			)
		elif exc.code == 404:
			logger.warning('Local fallback endpoint returned 404. Check LOCAL_FALLBACK_URL=%s', LOCAL_FALLBACK_URL)
		else:
			logger.warning('Local fallback model failed: HTTP %s %s', exc.code, body or exc)
		return ''
	except (urllib_error.URLError, TimeoutError, ValueError) as exc:
		logger.warning('Local fallback model failed: %s', exc)
		return ''


def _local_instruction_prefix() -> str:
	return (
		'You are a precise educational assistant. '
		'Follow formatting rules exactly. '
		'Do not include analysis, explanations about formatting, or extra commentary.\n\n'
	)


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
		for model_name in _get_gemini_model_candidates():
			for attempt in range(_MAX_TRANSIENT_RETRIES + 1):
				try:
					with _GEMINI_CALL_SEMAPHORE:
						response = client.models.generate_content(
							model=model_name,
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

					if _is_model_not_found_error(exc):
						logger.warning('Gemini model "%s" unavailable: %s. Trying next model candidate.', model_name, exc)
						break

					if _is_transient_error(exc) and attempt < _MAX_TRANSIENT_RETRIES:
						backoff_seconds = attempt + 1
						logger.warning(
							'Gemini temporarily unavailable on model "%s" (attempt %s/%s): %s. Retrying in %ss.',
							model_name,
							attempt + 1,
							_MAX_TRANSIENT_RETRIES + 1,
							exc,
							backoff_seconds,
						)
						time.sleep(backoff_seconds)
						continue

					logger.warning('Gemini new SDK generation failed on model "%s": %s', model_name, exc)
					break

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


def _line_looks_incomplete(line: str) -> bool:
	text = (line or '').strip()
	if not text:
		return False

	# Drop obvious hard cuts such as dangling markdown markers or open punctuation.
	if text.endswith(('**', '__', '(', '[', '{', ':', ';', ',', '-', '/')):
		return True

	# If the last token is a connector/article, the line is likely truncated.
	last_token = re.sub(r'[^a-zA-Z]', '', text.split()[-1]).lower() if text.split() else ''
	if last_token in {
		'the', 'a', 'an', 'and', 'or', 'to', 'of', 'in', 'on', 'for', 'with', 'by',
		'from', 'as', 'at', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'that',
	}:
		return True

	# Bullet lines should usually end with sentence punctuation or be reasonably complete.
	if text.startswith(('-', '*')):
		if text.endswith(('.', '!', '?')):
			return False
		words = re.findall(r"[A-Za-z0-9']+", text)
		return len(words) <= 6

	return False


def _finalize_summary_text(summary_text: str) -> str:
	text = (summary_text or '').strip()
	if not text:
		return ''

	lines = [line.rstrip() for line in text.splitlines()]
	while lines:
		last = (lines[-1] or '').strip()
		if not last:
			lines.pop()
			continue
		if _line_looks_incomplete(last):
			lines.pop()
			continue
		break

	cleaned = '\n'.join(lines).strip()
	cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
	return cleaned


def _expected_summary_headings(mode: str) -> List[str]:
	if mode == 'brief':
		return ['Quick Snapshot', 'Must-Know Facts', 'Immediate Takeaway']
	if mode == 'standard':
		return ['Overview', 'Key Concepts', 'Causal Links', 'Practical Applications']
	return [
		'Synthesis',
		'Concept Architecture',
		'Mechanisms and Dependencies',
		'Critical Nuances',
		'Applied Implications',
		'Revision Checklist',
	]


def _split_inline_headings(summary_text: str, mode: str) -> str:
	text = summary_text or ''
	# Split any inline markdown heading marker into a new heading block.
	text = re.sub(r'\s+#{1,6}\s*([A-Za-z][A-Za-z0-9/&\-\s]{2,80})', r'\n\n\1', text)
	headings = _expected_summary_headings(mode)
	for heading in headings:
		# Break inline markdown headings into proper heading lines.
		inline_md = re.compile(rf'\s+#{1,6}\s*{re.escape(heading)}\b', re.IGNORECASE)
		text = inline_md.sub(f'\n\n{heading}', text)
	return text


def _polish_summary_text(summary_text: str, mode: str) -> str:
	text = _split_inline_headings(summary_text or '', mode)
	if not text.strip():
		return ''

	headings = _expected_summary_headings(mode)
	heading_map = {item.lower(): item for item in headings}
	alt = '|'.join(re.escape(item) for item in sorted(headings, key=len, reverse=True))
	combined_heading = re.compile(rf'^({alt})(?:\s*[:\-]\s*|\s+)(.+)$', re.IGNORECASE)
	strict_heading = re.compile(rf'^({alt})\s*$', re.IGNORECASE)

	lines: List[str] = []
	seen_heading_key = None

	for raw in text.splitlines():
		line = (raw or '').strip()
		if not line:
			if lines and lines[-1] != '':
				lines.append('')
			continue

		line = re.sub(r'^#{1,6}\s*', '', line)
		line = re.sub(r'#{1,6}\s*', '', line)
		line = line.replace('**', '').replace('__', '')
		line = line.replace('•', '-')
		line = re.sub(r'\s{2,}', ' ', line).strip()

		if re.match(r'^[-*+]\s*', line):
			line = '- ' + re.sub(r'^[-*+]\s*', '', line)
		line = re.sub(r'^\d+[\.)]\s+', '- ', line)

		strict = strict_heading.match(line)
		if strict:
			heading = heading_map.get(strict.group(1).lower(), strict.group(1))
			if lines and lines[-1] != '':
				lines.append('')
			if seen_heading_key != heading.lower():
				lines.append(heading)
				seen_heading_key = heading.lower()
			continue

		combined = combined_heading.match(line)
		if combined:
			heading = heading_map.get(combined.group(1).lower(), combined.group(1))
			remainder = combined.group(2).strip()
			if lines and lines[-1] != '':
				lines.append('')
			if seen_heading_key != heading.lower():
				lines.append(heading)
				seen_heading_key = heading.lower()
			if remainder:
				lines.append(remainder)
			continue

		lines.append(line)

	cleaned = '\n'.join(lines)
	cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
	return cleaned


def _content_lines(summary_text: str) -> List[str]:
	lines = []
	for raw in (summary_text or '').splitlines():
		line = (raw or '').strip()
		if not line:
			continue
		if not line.startswith(('-', '*')) and len(line.split()) <= 4:
			continue
		if line.startswith(('-', '*')):
			line = line[1:].strip()
		if line:
			lines.append(line)
	return lines


def _needs_summary_enrichment(summary_text: str, mode: str) -> bool:
	text = (summary_text or '').strip()
	if not text:
		return True

	content = _content_lines(text)
	normalized = [_normalize_similarity_key(line) for line in content if _normalize_similarity_key(line)]
	unique_count = len(set(normalized))

	# Detect over-compressed outputs where sections repeat one idea.
	if unique_count <= 2:
		return True

	min_unique_by_mode = {
		'brief': 3,
		'standard': 6,
		'detailed': 9,
	}
	return unique_count < min_unique_by_mode.get((mode or '').lower(), 5)


def _build_summary_enrichment_prompt(mode: str, current_summary: str, notes_context: str) -> str:
	if mode == 'brief':
		shape = (
			'Use these sections exactly: Quick Snapshot, Must-Know Facts, Immediate Takeaway. '
			'Include 1 compact snapshot paragraph, 3-4 fact bullets, and 1 takeaway bullet.'
		)
	elif mode == 'standard':
		shape = (
			'Use these sections exactly: Overview, Key Concepts, Causal Links, Practical Applications. '
			'Include 1 short overview paragraph, 5-7 concept bullets, 2-3 causal-link bullets, and 2-3 application bullets.'
		)
	else:
		shape = (
			'Use these sections exactly: Synthesis, Concept Architecture, Mechanisms and Dependencies, Critical Nuances, '
			'Applied Implications, Revision Checklist. '
			'Provide specific, non-overlapping bullets with enough technical depth.'
		)

	return (
		'Improve this summary for broader source coverage and less repetition.\n'
		'Rules:\n'
		'- Keep only source-supported facts.\n'
		'- Remove repeated ideas across sections.\n'
		'- Prefer diverse key points over restating one sentence.\n'
		'- Keep it concise and readable.\n'
		'- Do not add commentary outside the summary.\n'
		f'- Structure: {shape}\n\n'
		f'Source notes:\n{_summarization_context(notes_context, 12000)}\n\n'
		f'Current summary:\n{current_summary}'
	)


def _enrich_summary_coverage(summary_text: str, notes_context: str, mode: str) -> str:
	if not _needs_summary_enrichment(summary_text, mode):
		return _polish_summary_text(summary_text, mode)

	prompt = _build_summary_enrichment_prompt(mode, summary_text, notes_context)
	enrich_tokens_by_mode = {
		'brief': 600,
		'standard': 900,
		'detailed': 1200,
	}
	enriched = _generate_text(prompt, temperature=0.15, max_output_tokens=enrich_tokens_by_mode.get(mode, 900))
	enriched = _dedupe_bullets(enriched)
	enriched = _finalize_summary_text(enriched)
	enriched = _polish_summary_text(enriched, mode)
	if _is_valid_summary_structure(enriched, mode):
		return enriched
	return _polish_summary_text(summary_text, mode)


def _is_valid_summary_structure(summary_text: str, mode: str) -> bool:
	text = (summary_text or '').strip()
	if not text:
		return False

	lines = [line.strip() for line in text.splitlines() if line.strip()]
	bullet_lines = [line for line in lines if line.startswith(('-', '*'))]
	word_count = len(re.findall(r"[A-Za-z0-9']+", text))
	heading_lines = [line for line in lines if not line.startswith(('-', '*')) and len(line.split()) <= 6]

	# Mode-specific quality floors to keep outputs clearly differentiated.
	min_chars_by_mode = {
		'brief': 160,
		'standard': 380,
		'detailed': 700,
	}
	min_chars = min_chars_by_mode.get((mode or '').lower(), 400)
	if len(text) < min_chars:
		return False

	mode_rules = {
		'brief': {'min_words': 70, 'min_bullets': 3, 'min_headings': 2},
		'standard': {'min_words': 180, 'min_bullets': 8, 'min_headings': 3},
		'detailed': {'min_words': 320, 'min_bullets': 12, 'min_headings': 5},
	}
	rules = mode_rules.get((mode or '').lower(), mode_rules['standard'])
	if word_count < rules['min_words']:
		return False
	if len(bullet_lines) < rules['min_bullets']:
		return False
	if len(heading_lines) < rules['min_headings']:
		return False

	# Just check: has some content, has some bullets, has multiple lines
	# Don't be too strict about section names or layout
	has_bullets = len(bullet_lines) >= 1
	has_multiple_lines = len(lines) >= 3
	minimum_length = len(text) >= min_chars

	return has_bullets and has_multiple_lines and minimum_length


def _build_summary_repair_prompt(mode: str, broken_summary: str, notes_context: str, local_summary: str) -> str:
	if mode == 'brief':
		format_rules = (
			'Use exactly these headings: Quick Snapshot, Must-Know Facts, Immediate Takeaway. '
			'Keep total length short and high-signal.'
		)
	elif mode == 'standard':
		format_rules = (
			'Use exactly these headings: Overview, Key Concepts, Causal Links, Practical Applications. '
			'Balance clarity and depth for study use.'
		)
	else:
		format_rules = (
			'Use exactly these headings: Synthesis, Concept Architecture, Mechanisms and Dependencies, '
			'Critical Nuances, Applied Implications, Revision Checklist. '
			'Provide technically rich, non-redundant points.'
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
		snapshot = ' '.join(lines[:2]) if len(lines) > 1 else lines[0]
		facts = lines[2:6] or lines[:4]
		takeaways = lines[6:8] or lines[4:6]
		return (
			f'Quick Snapshot\n{snapshot}\n\n'
			'Must-Know Facts\n' + '\n'.join(f'- {item}' for item in facts[:4]) + '\n\n'
			'Immediate Takeaway\n' + ('\n'.join(f'- {item}' for item in takeaways[:1]) or '- Review core terms and one practical implication.')
		)

	if mode == 'standard':
		overview = ' '.join(lines[:2]) if len(lines) > 1 else lines[0]
		concepts = lines[2:8] or lines[:6]
		links = lines[8:11] or lines[5:8] or ['Cause-and-effect links were limited in the source.']
		applications = lines[11:14] or lines[7:10] or ['Apply these concepts to worked examples and case scenarios.']
		return (
			f'Overview\n{overview}\n\n'
			'Key Concepts\n' + '\n'.join(f'- {item}' for item in concepts[:6]) + '\n\n'
			'Causal Links\n' + '\n'.join(f'- {item}' for item in links[:3]) + '\n\n'
			'Practical Applications\n' + '\n'.join(f'- {item}' for item in applications[:3])
		)

	synthesis = ' '.join(lines[:3]) if len(lines) > 2 else ' '.join(lines[:2])
	architecture = lines[3:10] or lines[:7]
	mechanisms = lines[10:15] or lines[7:12]
	nuances = lines[15:19] or lines[12:16]
	applied = lines[19:23] or lines[16:20]
	checklist = lines[23:28] or lines[20:25] or lines[:5]
	return (
		f'Synthesis\n{synthesis}\n\n'
		'Concept Architecture\n' + '\n'.join(f'- {item}' for item in architecture[:7]) + '\n\n'
		'Mechanisms and Dependencies\n' + ('\n'.join(f'- {item}' for item in mechanisms[:5]) or '- None identified.') + '\n\n'
		'Critical Nuances\n' + ('\n'.join(f'- {item}' for item in nuances[:4]) or '- None identified.') + '\n\n'
		'Applied Implications\n' + ('\n'.join(f'- {item}' for item in applied[:4]) or '- Use these insights in practice-based tasks.') + '\n\n'
		'Revision Checklist\n' + '\n'.join(f'- {item}' for item in checklist[:5])
	)


# ===== GEMINI API WRAPPER =====


def generate_gemini_summary(text: str, local_summary: str = '', summary_mode: str = 'detailed') -> str:
	mode = (summary_mode or '').strip().lower()
	if mode not in {'brief', 'standard', 'detailed'}:
		mode = 'detailed'

	chunks = _split_text_chunks(text, max_chunks=5)
	if not chunks:
		_set_last_summary_failure_reason('empty_input')
		return local_summary

	extracted_notes: List[str] = []
	# Use a map pass for medium/long notes to improve source coverage.
	if len(text) > 9000 and len(chunks) > 1:
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
			"Create a BRIEF revision summary from the source notes.\n\n"
			"Use these headings exactly:\n"
			"Quick Snapshot\nMust-Know Facts\nImmediate Takeaway\n\n"
			"Rules:\n"
			"- Total 90-160 words.\n"
			"- Snapshot: 1-2 sentences only.\n"
			"- Must-Know Facts: 3-4 concise bullets.\n"
			"- Immediate Takeaway: exactly 1 bullet.\n"
			"- Keep only source-supported information.\n\n"
			"- Plain text only; do not use markdown symbols like #, **, or __.\n"
			"- Put each heading on its own line.\n\n"
			f"Source notes:\n{notes_context}"
		)
	elif mode == 'standard':
		prompt = (
			"Create a STANDARD study summary from the source notes.\n\n"
			"Use these headings exactly:\n"
			"Overview\nKey Concepts\nCausal Links\nPractical Applications\n\n"
			"Rules:\n"
			"- Total 220-330 words.\n"
			"- Overview: 2-3 sentences.\n"
			"- Key Concepts: 5-7 bullets with distinct ideas.\n"
			"- Causal Links: 2-3 bullets showing relationships or dependencies.\n"
			"- Practical Applications: 2-3 bullets for use in real tasks/study.\n"
			"- Keep only source-supported information.\n\n"
			"- Plain text only; do not use markdown symbols like #, **, or __.\n"
			"- Put each heading on its own line.\n\n"
			f"Source notes:\n{notes_context}"
		)
	else:
		prompt = (
			"Create a DETAILED analytic summary from the source notes.\n\n"
			"Use these headings exactly:\n"
			"Synthesis\nConcept Architecture\nMechanisms and Dependencies\nCritical Nuances\nApplied Implications\nRevision Checklist\n\n"
			"Rules:\n"
			"- Total 380-520 words.\n"
			"- Synthesis: 3-4 sentences integrating the whole topic.\n"
			"- Concept Architecture: 6-8 bullets organizing the main idea hierarchy.\n"
			"- Mechanisms and Dependencies: 4-5 bullets explaining how elements affect each other.\n"
			"- Critical Nuances: 3-4 bullets of caveats, boundaries, or assumptions.\n"
			"- Applied Implications: 3-4 bullets of high-value practical interpretation.\n"
			"- Revision Checklist: 4-5 concise bullets for exam/study review.\n"
			"- Keep only source-supported information.\n\n"
			"- Plain text only; do not use markdown symbols like #, **, or __.\n"
			"- Put each heading on its own line.\n\n"
			f"Source notes:\n{notes_context}"
		)

	tokens_by_mode = {
		'brief': 520,
		'standard': 900,
		'detailed': 1300,
	}
	refined = _generate_text(prompt, temperature=0.2 if mode == 'brief' else 0.22, max_output_tokens=tokens_by_mode.get(mode, 900))
	refined = _dedupe_bullets(refined)
	refined = _finalize_summary_text(refined)
	refined = _polish_summary_text(refined, mode)
	refined = _enrich_summary_coverage(refined, notes_context, mode)
	if _is_valid_summary_structure(refined, mode):
		_set_last_summary_failure_reason('none')
		return refined

	if refined:
		repair_prompt = _build_summary_repair_prompt(mode, refined, notes_context, local_summary)
		repair_tokens = 520 if mode == 'brief' else (900 if mode == 'standard' else 1300)
		repaired = _generate_text(repair_prompt, temperature=0.2, max_output_tokens=repair_tokens)
		repaired = _dedupe_bullets(repaired)
		repaired = _finalize_summary_text(repaired)
		repaired = _polish_summary_text(repaired, mode)
		repaired = _enrich_summary_coverage(repaired, notes_context, mode)
		if _is_valid_summary_structure(repaired, mode):
			_set_last_summary_failure_reason('none')
			return repaired

		# If Gemini returned content but structure checks failed, salvage it instead of
		# returning empty and triggering unnecessary retries.
		salvage_source = repaired or refined
		salvaged = _build_structured_fallback(salvage_source, mode)
		salvaged = _dedupe_bullets(salvaged)
		salvaged = _finalize_summary_text(salvaged)
		salvaged = _polish_summary_text(salvaged, mode)
		salvaged = _enrich_summary_coverage(salvaged, notes_context, mode)
		if salvaged.strip():
			_set_last_summary_failure_reason('structure_salvaged')
			return salvaged.strip()

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


def generate_gemini_constructed_questions(text: str, count: int = 6) -> List[str]:
	prompt = (
		f'Create {count} constructed-response study questions from the lecture notes.\n'
		'Return one question per line, no numbering, no bullets, no answer key, no extra commentary.\n'
		'Each question must be open-ended and require explanation, not yes/no.\n\n'
		f'Lecture notes:\n{text[:12000]}'
	)
	questions = _extract_nonempty_lines(_generate_text(prompt, temperature=0.2, max_output_tokens=420))
	return questions[:count]


def generate_gemini_mcq_questions(text: str, count: int = 6) -> List[Dict[str, str]]:
	prompt = (
		f'Create {count} multiple-choice questions from the lecture notes.\n'
		'Each question must include exactly 4 options and one correct answer.\n'
		'Return in this exact plain-text block format for each item:\n'
		'Q: <question>\n'
		'A) <option A>\n'
		'B) <option B>\n'
		'C) <option C>\n'
		'D) <option D>\n'
		'ANSWER: <A|B|C|D>\n\n'
		f'Lecture notes:\n{text[:12000]}'
	)

	raw = _generate_text(prompt, temperature=0.2, max_output_tokens=900)
	if not raw:
		return []

	lines = [line.strip() for line in raw.splitlines() if line.strip()]
	items: List[Dict[str, str]] = []
	current: Dict[str, str] = {}

	def _commit_current() -> None:
		if {'stem', 'a', 'b', 'c', 'd', 'answer'} <= set(current.keys()):
			answer_key = current['answer'].upper().strip()
			if answer_key not in {'A', 'B', 'C', 'D'}:
				return
			question_text = (
				f"{current['stem']}\n"
				f"A) {current['a']}\n"
				f"B) {current['b']}\n"
				f"C) {current['c']}\n"
				f"D) {current['d']}"
			)
			items.append({'question_text': question_text, 'correct_answer': answer_key})

	for line in lines:
		upper = line.upper()
		if line.startswith('Q:'):
			if current:
				_commit_current()
				current = {}
			current['stem'] = line[2:].strip()
		elif upper.startswith('A)'):
			current['a'] = line[2:].strip()
		elif upper.startswith('B)'):
			current['b'] = line[2:].strip()
		elif upper.startswith('C)'):
			current['c'] = line[2:].strip()
		elif upper.startswith('D)'):
			current['d'] = line[2:].strip()
		elif upper.startswith('ANSWER:'):
			current['answer'] = line.split(':', 1)[-1].strip()[:1]

	if current:
		_commit_current()

	return items[:count]


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


# ===== LOCAL INSTRUCTION MODEL WRAPPER =====


def _parse_mcq_response(raw: str, count: int) -> List[Dict[str, str]]:
	lines = [line.strip() for line in (raw or '').splitlines() if line.strip()]
	items: List[Dict[str, str]] = []
	current: Dict[str, str] = {}

	def _commit_current() -> None:
		if {'stem', 'a', 'b', 'c', 'd', 'answer'} <= set(current.keys()):
			answer_key = current['answer'].upper().strip()
			if answer_key not in {'A', 'B', 'C', 'D'}:
				return
			question_text = (
				f"{current['stem']}\n"
				f"A) {current['a']}\n"
				f"B) {current['b']}\n"
				f"C) {current['c']}\n"
				f"D) {current['d']}"
			)
			items.append({'question_text': question_text, 'correct_answer': answer_key})

	for line in lines:
		upper = line.upper()
		if line.startswith('Q:'):
			if current:
				_commit_current()
				current = {}
			current['stem'] = line[2:].strip()
		elif upper.startswith('A)'):
			current['a'] = line[2:].strip()
		elif upper.startswith('B)'):
			current['b'] = line[2:].strip()
		elif upper.startswith('C)'):
			current['c'] = line[2:].strip()
		elif upper.startswith('D)'):
			current['d'] = line[2:].strip()
		elif upper.startswith('ANSWER:'):
			current['answer'] = line.split(':', 1)[-1].strip()[:1]

	if current:
		_commit_current()

	return items[:count]


def generate_local_summary(text: str, summary_mode: str = 'detailed') -> str:
	mode = (summary_mode or '').strip().lower()
	if mode not in {'brief', 'standard', 'detailed'}:
		mode = 'detailed'
	if not text:
		return ''

	prompt = _local_instruction_prefix() + (
		f'Create a {mode} educational summary from the notes below.\n'
		'Use clear headings and concise bullet points in plain text only.\n'
		'Do not use markdown symbols like #, **, or __.\n'
		'Put each heading on its own line.\n'
		'Keep it factual and avoid commentary.\n\n'
		f'Notes:\n{text[:12000]}'
	)
	raw = _call_local_model(prompt, temperature=0.2, max_output_tokens=700)
	raw = _dedupe_bullets(raw)
	raw = _finalize_summary_text(raw)
	return _polish_summary_text(raw, mode)


def generate_local_questions(text: str, count: int = 5) -> List[str]:
	if not text:
		return []
	prompt = _local_instruction_prefix() + (
		f'Create {count} short study questions from the notes below.\n'
		f'Return exactly {count} questions, one per line. '
		'No numbering, no bullets, no extra text.\n\n'
		f'Notes:\n{text[:12000]}'
	)
	raw = _call_local_model(prompt, temperature=0.2, max_output_tokens=450)
	return _extract_nonempty_lines(raw)[:count]


def generate_local_constructed_questions(text: str, count: int = 6) -> List[str]:
	if not text:
		return []
	prompt = _local_instruction_prefix() + (
		f'Create {count} open-ended constructed-response questions from the notes below.\n'
		f'Each question should require explanation or reasoning. Return exactly {count} questions, one per line only.\n\n'
		f'Notes:\n{text[:12000]}'
	)
	raw = _call_local_model(prompt, temperature=0.25, max_output_tokens=500)
	return _extract_nonempty_lines(raw)[:count]


def generate_local_mcq_questions(text: str, count: int = 6) -> List[Dict[str, str]]:
	if not text:
		return []
	prompt = _local_instruction_prefix() + (
		f'Create {count} multiple-choice questions from the notes below.\n'
		f'Return exactly {count} blocks.\n'
		'Use this exact format for each question:\n'
		'Q: <question>\n'
		'A) <option A>\n'
		'B) <option B>\n'
		'C) <option C>\n'
		'D) <option D>\n'
		'ANSWER: <A|B|C|D>\n\n'
		f'Notes:\n{text[:12000]}'
	)
	raw = _call_local_model(prompt, temperature=0.2, max_output_tokens=900)
	return _parse_mcq_response(raw, count)


def generate_local_retry_question(question_text: str, concept_name: str = '') -> str:
	base = (question_text or '').strip().rstrip('?')
	if not base:
		return ''
	prompt = _local_instruction_prefix() + (
		'Rewrite this quiz question as a similar but not identical retry question. Return only the question.\n\n'
		f'Concept: {concept_name or "this concept"}\n'
		f'Original question: {base}'
	)
	return _call_local_model(prompt, temperature=0.2, max_output_tokens=180)


def generate_local_micro_lesson(
	question_text: str,
	student_answer: str,
	correct_answer: str,
	concept_name: str = '',
) -> str:
	prompt = _local_instruction_prefix() + (
		'Create a concise micro-lesson for a student who answered incorrectly.\n'
		'Use plain text only. Explain the misunderstanding, clarify the concept, and give one action tip.\n\n'
		f'Concept: {concept_name or "this concept"}\n'
		f'Question: {question_text}\n'
		f'Student answer: {student_answer or "(empty)"}\n'
		f'Correct answer: {correct_answer}'
	)
	return _call_local_model(prompt, temperature=0.25, max_output_tokens=240)
