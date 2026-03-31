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
    
    # Remove placeholder text patterns
    text = re.sub(r'You can enter a subtitle here.*?(?=\n|$)', '', text, flags=re.IGNORECASE)
    text = re.sub(r'click to edit.*?(?=\n|$)', '', text, flags=re.IGNORECASE)
    text = re.sub(r'add title.*?(?=\n|$)', '', text, flags=re.IGNORECASE)
    
    # Remove slide numbers and formatting symbols at line starts
    text = re.sub(r'^\s*\d{1,3}\s*[•\-\*o]\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d{1,3}\s+', '', text, flags=re.MULTILINE)  # Just numbered lines
    
    # Remove standalone bullet/formatting symbols
    text = re.sub(r'[•\-\*]\s+', ' ', text)
    text = re.sub(r'\s+[o]\s+', ' ', text)  # 'o' as bullet point
    text = re.sub(r'^\s*[o]\s+', '', text, flags=re.MULTILINE)
    
    # Remove excessive punctuation/symbols
    text = re.sub(r'[→↓↑←↔]', '', text)
    
    # Collapse repeated words (like "Impact Analysis Impact Analysis")
    text = re.sub(r'\b(\w+)\s+(?=\1\b)', '', text, flags=re.IGNORECASE)
    
    # Clean up whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    
    # Remove leading/trailing punctuation from lines
    lines = text.split('. ')
    lines = [line.strip().strip('•-*o ') for line in lines if line.strip()]
    text = '. '.join(lines)
    
    return text


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


def extract_text_from_pdf(file_path: str) -> str:
    """Backward compatible alias for previous function name."""
    return extract_text_from_file(file_path)
