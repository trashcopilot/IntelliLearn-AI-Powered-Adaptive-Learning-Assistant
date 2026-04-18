import re
from typing import Dict


def _safe_lines(text: str):
    return [line.strip() for line in (text or '').splitlines() if line.strip()]


def _normalize_text(text: str) -> str:
    normalized = (text or '').lower()
    normalized = re.sub(r'[^a-z0-9\s]', ' ', normalized)
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized


def _count_words(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", text or ''))


def _count_headings(lines) -> int:
    return len([line for line in lines if not line.startswith(('-', '*')) and len(line.split()) <= 8])


def _range_score(value: float, low: float, high: float) -> float:
    if low <= value <= high:
        return 1.0
    if value < low:
        gap = low - value
        return max(0.0, 1 - (gap / max(low, 1)))
    gap = value - high
    return max(0.0, 1 - (gap / max(high, 1)))


def evaluate_summary_quality(summary_text: str, source_text: str, mode: str = 'detailed') -> Dict[str, object]:
    """Return a lightweight quality score and metrics for a generated summary."""
    summary = (summary_text or '').strip()
    source = (source_text or '').strip()
    normalized_summary = _normalize_text(summary)
    normalized_source = _normalize_text(source)

    lines = _safe_lines(summary)
    bullet_lines = [line for line in lines if line.startswith(('-', '*'))]
    unique_bullets = {line[1:].strip().lower() for line in bullet_lines if len(line) > 2}
    heading_count = _count_headings(lines)

    summary_words = normalized_summary.split()
    source_words = set(normalized_source.split())
    if summary_words:
        grounded_ratio = sum(1 for word in summary_words if word in source_words) / len(summary_words)
    else:
        grounded_ratio = 0.0

    if bullet_lines:
        redundancy_ratio = 1 - (len(unique_bullets) / max(len(bullet_lines), 1))
    else:
        redundancy_ratio = 0.0

    mode_key = (mode or 'detailed').lower()
    word_count = _count_words(summary)

    mode_targets = {
        'brief': {
            'word_low': 80,
            'word_high': 190,
            'bullet_low': 3,
            'bullet_high': 6,
            'heading_low': 2,
            'heading_high': 4,
        },
        'standard': {
            'word_low': 200,
            'word_high': 360,
            'bullet_low': 8,
            'bullet_high': 14,
            'heading_low': 3,
            'heading_high': 5,
        },
        'detailed': {
            'word_low': 340,
            'word_high': 620,
            'bullet_low': 14,
            'bullet_high': 26,
            'heading_low': 5,
            'heading_high': 8,
        },
    }
    target = mode_targets.get(mode_key, mode_targets['standard'])

    word_fit = _range_score(word_count, target['word_low'], target['word_high'])
    bullet_fit = _range_score(len(bullet_lines), target['bullet_low'], target['bullet_high'])
    heading_fit = _range_score(heading_count, target['heading_low'], target['heading_high'])
    structure_score = (word_fit * 0.45) + (bullet_fit * 0.4) + (heading_fit * 0.15)

    # Conciseness is mode-aware so brief is not over-penalized for being short.
    conciseness_score = word_fit

    source_vocab = {token for token in normalized_source.split() if len(token) > 3}
    summary_vocab = {token for token in normalized_summary.split() if len(token) > 3}
    if source_vocab:
        coverage_ratio = len(source_vocab & summary_vocab) / len(source_vocab)
    else:
        coverage_ratio = 0.0

    score = (
        structure_score * 0.35
        + grounded_ratio * 0.3
        + conciseness_score * 0.15
        + (1 - redundancy_ratio) * 0.1
        + min(1.0, coverage_ratio * 4.0) * 0.1
    )
    score = round(max(0.0, min(1.0, score)) * 100, 1)

    if score >= 85:
        status = 'high'
    elif score >= 70:
        status = 'medium'
    else:
        status = 'low'

    return {
        'score': score,
        'status': status,
        'metrics': {
            'structure': round(structure_score * 100, 1),
            'grounding': round(grounded_ratio * 100, 1),
            'conciseness': round(conciseness_score * 100, 1),
            'non_redundancy': round((1 - redundancy_ratio) * 100, 1),
            'coverage': round(min(1.0, coverage_ratio * 4.0) * 100, 1),
            'word_count': word_count,
            'bullet_count': len(bullet_lines),
            'heading_count': heading_count,
        },
    }
