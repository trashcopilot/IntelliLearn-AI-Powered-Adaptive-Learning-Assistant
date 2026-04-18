import re

from django import template


register = template.Library()


@register.filter(name='clean_summary_preview')
def clean_summary_preview(value: str) -> str:
    """Normalize markdown-like AI output into clean preview text."""
    raw_text = (value or '').replace('\r\n', '\n').replace('\r', '\n')
    lines = raw_text.split('\n')
    cleaned_lines = []
    previous_blank = False

    for line in lines:
        text = line.strip()

        if not text:
            if not previous_blank:
                cleaned_lines.append('')
            previous_blank = True
            continue

        previous_blank = False

        # Remove markdown heading markers.
        text = re.sub(r'^#{1,6}\s*', '', text)

        # Remove bold/italic markdown markers.
        text = text.replace('**', '').replace('__', '').replace('*', '')

        # Convert bullet markers to a cleaner preview symbol.
        text = re.sub(r'^[-+]\s+', '• ', text)

        # Normalize spaces around punctuation.
        text = re.sub(r'\s{2,}', ' ', text)
        text = re.sub(r'\s+([,.;:!?])', r'\1', text)

        cleaned_lines.append(text)

    result = '\n'.join(cleaned_lines).strip()
    return result
