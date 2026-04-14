import re
from typing import Dict


def _safe_lines(text: str):
    return [line.strip() for line in (text or '').splitlines() if line.strip()]


def _normalize_text(text: str) -> str:
    normalized = (text or '').lower()
    normalized = re.sub(r'[^a-z0-9\s]', ' ', normalized)
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized


def evaluate_summary_quality(summary_text: str, source_text: str, mode: str = 'detailed') -> Dict[str, object]:
    """Return a lightweight quality score and metrics for a generated summary."""
    summary = (summary_text or '').strip()
    source = (source_text or '').strip()
    normalized_summary = _normalize_text(summary)
    normalized_source = _normalize_text(source)

    lines = _safe_lines(summary)
    bullet_lines = [line for line in lines if line.startswith(('-', '*'))]
    unique_bullets = {line[1:].strip().lower() for line in bullet_lines if len(line) > 2}

    lowered_summary = summary.lower()
    brief_sections = ['executive abstract', 'thematic clusters', 'technical anchors']
    standard_sections = ['core context', 'key findings', 'next steps/implications']
    detailed_sections = brief_sections + standard_sections
    present_sections = [section for section in detailed_sections if section in lowered_summary]

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
    structure_score = 0.0
    if mode_key == 'brief':
        has_sections = all(section in lowered_summary for section in brief_sections)
        in_range = 3 <= len(bullet_lines) <= 6
        structure_score = 1.0 if has_sections and in_range else 0.5
    elif mode_key == 'standard':
        has_sections = all(section in lowered_summary for section in standard_sections)
        in_range = 4 <= len(bullet_lines) <= 10
        structure_score = 1.0 if has_sections and in_range else 0.5
    else:
        structure_score = 1.0 if len(present_sections) == len(detailed_sections) and len(bullet_lines) >= 8 else 0.55

    density_words = len(summary_words)
    if density_words < 40:
        conciseness_score = 0.6
    elif density_words <= 320:
        conciseness_score = 1.0
    else:
        conciseness_score = max(0.55, 1 - ((density_words - 320) / 500))

    score = (
        structure_score * 0.4
        + grounded_ratio * 0.35
        + conciseness_score * 0.15
        + (1 - redundancy_ratio) * 0.1
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
            'bullet_count': len(bullet_lines),
            'sections_present': present_sections,
        },
    }
