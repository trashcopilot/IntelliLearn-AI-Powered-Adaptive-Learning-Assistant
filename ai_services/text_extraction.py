import logging
import os
import re
import tempfile

logger = logging.getLogger(__name__)


def _extract_pdf(path: str) -> str:
    from pypdf import PdfReader
    reader = PdfReader(path)
    return '\n'.join((page.extract_text() or '') for page in reader.pages)


def _extract_docx(path: str) -> str:
    from docx import Document
    doc = Document(path)
    return '\n'.join(para.text for para in doc.paragraphs)


def _extract_txt(path: str) -> str:
    with open(path, 'r', encoding='utf-8', errors='ignore') as handle:
        return handle.read()


def _extract_audio(path: str) -> str:
    try:
        import speech_recognition as sr
    except Exception:
        logger.warning('speech_recognition package is unavailable for audio transcription.')
        return ''

    recognizer = sr.Recognizer()
    with sr.AudioFile(path) as source:
        audio_data = recognizer.record(source)

    try:
        return recognizer.recognize_google(audio_data)
    except Exception as exc:
        logger.warning('Audio transcription failed for %s: %s', path, exc)
        return ''


def _extract_video(path: str) -> str:
    try:
        from moviepy import VideoFileClip
    except Exception:
        logger.warning('moviepy package is unavailable for video transcription.')
        return ''

    temp_audio = None
    clip = None
    try:
        clip = VideoFileClip(path)
        if clip.audio is None:
            return ''
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
            temp_audio = temp_file.name
        clip.audio.write_audiofile(temp_audio, logger=None)
        return _extract_audio(temp_audio)
    except Exception as exc:
        logger.warning('Video transcription failed for %s: %s', path, exc)
        return ''
    finally:
        if clip is not None:
            clip.close()
        if temp_audio and os.path.exists(temp_audio):
            os.remove(temp_audio)


def _clean_extracted_text(text: str) -> str:
    """Remove formatting artifacts, placeholders, and symbols from extracted text."""
    if not text:
        return text

    placeholder_patterns = (
        r'You can enter a subtitle here.*?(?=$)',
        r'click to edit.*?(?=$)',
        r'add title.*?(?=$)',
    )

    cleaned_lines = []
    for raw_line in text.splitlines():
        line = (raw_line or '').strip()
        if not line:
            continue

        # Remove common slide placeholder lines.
        skip_line = False
        for pattern in placeholder_patterns:
            if re.search(pattern, line, flags=re.IGNORECASE):
                skip_line = True
                break
        if skip_line:
            continue

        line = re.sub(r'[→↓↑←↔]', '', line)

        # Remove pure page numbers like "12" or "12/44".
        if re.fullmatch(r'\d{1,3}(?:\s*/\s*\d{1,3})?', line):
            continue

        # Normalize bullet markers but keep list structure.
        line = re.sub(r'^\s*[•\-\*o]\s*', '- ', line)
        line = re.sub(r'^\s*(\d{1,3})[\.)]\s*', r'\1. ', line)

        # Collapse duplicate neighboring words while preserving sentence structure.
        line = re.sub(r'\b(\w+)\s+(?=\1\b)', '', line, flags=re.IGNORECASE)
        line = re.sub(r'\s+', ' ', line).strip()

        if line:
            cleaned_lines.append(line)

    if not cleaned_lines:
        return ''

    # Preserve line boundaries so downstream summarization can detect headings/lists.
    return '\n'.join(cleaned_lines)


def extract_text_from_file(file_path: str) -> str:
    """Extract text from supported document, audio, and video files."""
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == '.pdf':
            text = _extract_pdf(file_path)
        elif ext in ('.docx', '.doc'):
            text = _extract_docx(file_path)
        elif ext == '.txt':
            text = _extract_txt(file_path)
        elif ext in ('.mp3', '.wav', '.m4a'):
            text = _extract_audio(file_path)
        elif ext in ('.mp4', '.mov'):
            text = _extract_video(file_path)
        else:
            logger.warning('Unsupported file type: %s', ext)
            text = ''
    except Exception as exc:
        logger.error('Text extraction failed for %s: %s', file_path, exc)
        text = ''

    cleaned = _clean_extracted_text(text)
    return cleaned or 'No extractable text found in the uploaded file.'


def extract_text_from_bytes(file_name: str, file_data: bytes) -> str:
    """Extract text from in-memory file bytes by using a temporary file."""
    if not file_data:
        return 'No extractable text found in the uploaded file.'

    ext = os.path.splitext(file_name or '')[1].lower() or '.bin'
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as temp_file:
            temp_file.write(file_data)
            temp_path = temp_file.name
        return extract_text_from_file(temp_path)
    except Exception as exc:
        logger.error('Text extraction failed for in-memory file %s: %s', file_name, exc)
        return 'No extractable text found in the uploaded file.'
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


def extract_text_from_pdf(file_path: str) -> str:
    """Backward compatible alias for previous function name."""
    return extract_text_from_file(file_path)
