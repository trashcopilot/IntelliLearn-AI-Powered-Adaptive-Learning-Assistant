from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import JsonResponse
from django.http import HttpResponse
from django.http import HttpResponseForbidden
from django.utils import timezone
from django.template.loader import render_to_string
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from datetime import timedelta
import os
import tempfile

from ai_services.tasks import run_background
from ai_services.summary_quality import evaluate_summary_quality
from ai_services.ai_orchestrator import (
    generate_constructed_answer_keys,
    generate_constructed_questions,
    generate_mcq_questions,
    summarize_text,
)
from ai_services.text_extraction import extract_text_from_bytes, extract_text_from_file
from learning_app.forms import CreateClassroomForm
from learning_app.models import Classroom, ClassroomEnrollment, Concept, Question

from .forms import LectureUploadForm, QuestionEditForm, SummaryEditForm
from .models import LectureMaterial, Summary, SummaryValidation


ARCHIVE_RETENTION_DAYS = 30
PUBLISH_MODES = {'mcq', 'constructed', 'both'}
MIN_QUESTION_SOURCE_SUMMARY_CHARS = max(120, int(os.getenv('QUIZ_SOURCE_MIN_SUMMARY_CHARS', '240')))
MIN_QUESTION_SOURCE_QUALITY_SCORE = max(0.0, min(100.0, float(os.getenv('QUIZ_SOURCE_MIN_QUALITY_SCORE', '60'))))
AI_PENDING_WINDOW_MINUTES = max(5, int(os.getenv('AI_PENDING_WINDOW_MINUTES', '180')))
AI_PROCESSING_TIMEOUT_MINUTES = max(10, int(os.getenv('AI_PROCESSING_TIMEOUT_MINUTES', '30')))

QUIZ_GENERATION_TARGETS = {
    'mcq': {Question.TYPE_MCQ: 10},
    'constructed': {Question.TYPE_CONSTRUCTED: 10},
    'both': {Question.TYPE_MCQ: 5, Question.TYPE_CONSTRUCTED: 5},
}
DIFFICULTY_MIX_PATTERN = ['Easy', 'Easy', 'Easy', 'Medium', 'Medium', 'Medium', 'Medium', 'Hard', 'Hard', 'Hard']
QUIZ_GENERATION_MAX_ATTEMPTS = max(1, int(os.getenv('QUIZ_GENERATION_MAX_ATTEMPTS', '3')))


def _trace_ai(message: str) -> None:
    print(message, flush=True)


def _resolve_question_generation_source(lecture):
    summary_text = ''
    quality_score = 0.0

    lecture_summary = getattr(lecture, 'summary', None)
    if lecture_summary is not None and not lecture_summary.IsArchived:
        summary_text = (lecture_summary.SummaryText or '').strip()
        validation = getattr(lecture_summary, 'validation', None)
        if validation is not None:
            try:
                quality_score = float(validation.QualityScore or 0)
            except (TypeError, ValueError):
                quality_score = 0.0
    elif lecture_summary is not None and lecture_summary.IsArchived:
        _trace_ai(
            f'⏭️ Quiz Source: Skipping archived summary for lecture_id={lecture.pk}; '
            'falling back to raw material text.'
        )

    has_summary_signal = (
        len(summary_text) >= MIN_QUESTION_SOURCE_SUMMARY_CHARS
        and quality_score >= MIN_QUESTION_SOURCE_QUALITY_SCORE
    )
    if has_summary_signal:
        _trace_ai(
            f'🧠 Quiz Source Selected: lecture_id={lecture.pk}, source=summary, '
            f'chars={len(summary_text)}, quality={quality_score:.1f}'
        )
        return summary_text, 'summary'

    material_text = _extract_material_text(lecture)
    _trace_ai(
        f'📄 Quiz Source Selected: lecture_id={lecture.pk}, source=material, '
        f'summary_chars={len(summary_text)}, summary_quality={quality_score:.1f}, material_chars={len(material_text)}'
    )
    return material_text, 'material'


def _difficulty_for_index(index: int) -> str:
    return DIFFICULTY_MIX_PATTERN[index % len(DIFFICULTY_MIX_PATTERN)]


def _extract_material_text(material):
    source_file = getattr(material, 'SourceFile', None)
    if source_file and getattr(source_file, 'name', ''):
        temp_path = None
        try:
            file_name = os.path.basename(source_file.name)
            ext = os.path.splitext(file_name)[1].lower() or '.bin'
            with source_file.storage.open(source_file.name, 'rb') as handle:
                with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as temp_file:
                    temp_path = temp_file.name
                    for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                        temp_file.write(chunk)
            return extract_text_from_file(temp_path)
        except Exception as exc:
            _trace_ai(f'⚠️ File storage extraction failed: material_id={material.pk}, error={exc}')
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

    return extract_text_from_bytes(material.OriginalFileName, material.FileData)


def _purge_expired_archived_summaries(user):
    cutoff = timezone.now() - timedelta(days=ARCHIVE_RETENTION_DAYS)
    Summary.objects.filter(
        Lecture__UploadedBy=user,
        IsArchived=True,
        ArchivedAt__lt=cutoff,
    ).delete()


def _get_recent_pending_materials(user, classroom):
    cutoff = timezone.now() - timedelta(minutes=AI_PENDING_WINDOW_MINUTES)
    return LectureMaterial.objects.filter(
        UploadedBy=user,
        Classroom=classroom,
        summary__isnull=True,
        UploadedAt__gte=cutoff,
    )


def _persist_failed_summary(material, error_text: str) -> None:
    # Never overwrite a summary the educator has explicitly deleted.
    user_deleted = SummaryValidation.objects.filter(
        Lecture=material,
        QualityStatus='user_deleted',
    ).exists()
    if user_deleted:
        _trace_ai(
            f'⏭️ AI Failure Persistence Skipped: material_id={material.pk} '
            'was explicitly deleted by the educator — will not write failure record.'
        )
        return

    # Never overwrite a summary the educator has already archived.
    existing = Summary.objects.filter(Lecture=material).first()
    if existing is not None and existing.IsArchived:
        _trace_ai(
            f'⏭️ AI Failure Persistence Skipped: material_id={material.pk} '
            'has an archived summary — will not overwrite with failure record.'
        )
        return

    failure_text = (
        'AI processing failed for this lecture. '
        'Please edit the summary manually or re-upload to retry generation.'
    )
    summary, _ = Summary.objects.update_or_create(
        Lecture=material,
        defaults={'SummaryText': failure_text, 'IsVerified': False},
    )
    SummaryValidation.objects.update_or_create(
        Summary=summary,
        defaults={
            'Lecture': material,
            'SummaryTextSnapshot': failure_text,
            'IsVerified': False,
            'QualityScore': 0,
            'QualityStatus': 'failed',
            'QualityMetrics': {'error': error_text},
            'VerifiedBy': None,
        },
    )


def _mark_stale_pending_materials_failed(user, classroom):
    stale_cutoff = timezone.now() - timedelta(minutes=AI_PROCESSING_TIMEOUT_MINUTES)
    stale_qs = LectureMaterial.objects.filter(
        UploadedBy=user,
        Classroom=classroom,
        summary__isnull=True,
        UploadedAt__lt=stale_cutoff,
    )
    stale_materials = list(stale_qs)
    for material in stale_materials:
        _persist_failed_summary(material, 'processing_timeout')

    if stale_materials:
        _trace_ai(
            f'⚠️ Stale AI Jobs Reconciled: classroom_id={classroom.pk}, count={len(stale_materials)}, '
            f'timeout_minutes={AI_PROCESSING_TIMEOUT_MINUTES}'
        )


def _get_selected_educator_classroom(request):
    classroom_id = request.session.get('educator_active_classroom_id')
    if not classroom_id:
        return None

    return Classroom.objects.filter(
        ClassroomID=classroom_id,
        CreatedBy=request.user,
        IsActive=True,
    ).first()


def _process_material_ai(material_pk, educator_pk, summary_mode='detailed', upload_index=None, upload_total=None):
    try:
        material = LectureMaterial.objects.get(pk=material_pk)

        # If the educator has explicitly deleted the summary for this lecture,
        # a SummaryValidation tombstone with QualityStatus='user_deleted' will
        # exist (written by delete_archived_summary before the physical delete).
        # Honour that intent and skip summary creation entirely so a stale
        # background job cannot resurrect a summary the user chose to remove.
        user_deleted = SummaryValidation.objects.filter(
            Lecture=material,
            QualityStatus='user_deleted',
        ).exists()
        if user_deleted:
            _trace_ai(
                f'\u23ed\ufe0f AI Analysis Skipped: material_id={material_pk} was explicitly deleted by the '
                'educator \u2014 will not recreate summary to respect user\u2019s delete action.'
            )
            return

        # If the educator has already archived (or had archived) a summary for this
        # lecture, do not regenerate it — respect the user's explicit action.
        existing_summary = Summary.objects.filter(Lecture=material).first()
        if existing_summary is not None and existing_summary.IsArchived:
            _trace_ai(
                f'\u23ed\ufe0f AI Analysis Skipped: material_id={material_pk} already has an archived summary '
                '\u2014 will not regenerate to respect educator\'s archive action.'
            )
            return

        _trace_ai(f'🚀 AI Analysis Started: material_id={material_pk}, title="{material.Title}", mode={summary_mode}')
        raw_text = _extract_material_text(material)
        _trace_ai(f'📄 AI Text Extracted: material_id={material_pk}, chars={len(raw_text)}')

        source_context_parts = [
            f'Title: {material.Title}',
            f'Original filename: {material.OriginalFileName}',
            f'MIME type: {material.MimeType or "unknown"}',
            f'File size: {material.FileSize} bytes',
        ]
        if upload_index is not None and upload_total is not None:
            source_context_parts.append(f'Upload batch position: {upload_index}/{upload_total}')
        source_context_parts.append('Instruction: summarize this single uploaded file only. Do not blend content from other uploads.')
        source_context = '\n'.join(source_context_parts)
        summary_text = summarize_text(raw_text, summary_mode=summary_mode, source_context=source_context)
        quality = evaluate_summary_quality(summary_text, raw_text, mode=summary_mode)
        # Use create_defaults so IsArchived=False is only applied on creation and
        # never written back to an existing record, preserving any archived state.
        summary, _ = Summary.objects.update_or_create(
            Lecture=material,
            defaults={'SummaryText': summary_text, 'IsVerified': False},
            create_defaults={'SummaryText': summary_text, 'IsVerified': False, 'IsArchived': False},
        )
        SummaryValidation.objects.update_or_create(
            Summary=summary,
            defaults={
                'Lecture': material,
                'SummaryTextSnapshot': summary_text,
                'IsVerified': False,
                'QualityScore': quality.get('score', 0),
                'QualityStatus': quality.get('status', 'low'),
                'QualityMetrics': quality.get('metrics', {}),
                'VerifiedBy': None,
            },
        )

        _trace_ai(
            f'✅ AI Analysis Success: material_id={material_pk}, summary_score={quality.get("score", 0)}, '
            'questions_created=0 (deferred until publish_quiz)'
        )
    except Exception as exc:
        try:
            material = LectureMaterial.objects.get(pk=material_pk)
            _persist_failed_summary(material, str(exc))
        except Exception as persistence_exc:
            _trace_ai(f'❌ AI Failure Persistence Error: material_id={material_pk}, error={persistence_exc}')

        _trace_ai(f'❌ AI Analysis Failure: material_id={material_pk}, error={exc}')
        raise


def _process_material_ai_batch(material_pks, educator_pk, summary_mode='detailed'):
    total = len(material_pks)
    for upload_index, material_pk in enumerate(material_pks, start=1):
        try:
            _process_material_ai(material_pk, educator_pk, summary_mode, upload_index, total)
        except Exception as exc:
            _trace_ai(
                f'❌ AI Batch Item Failure: material_id={material_pk}, '
                f'position={upload_index}/{total}, error={exc}'
            )


@login_required
def educator_dashboard(request):
    if not request.user.is_educator():
        return HttpResponseForbidden('Only educators can access this page.')

    selected_classroom = _get_selected_educator_classroom(request)
    if selected_classroom is None:
        return redirect('content:educator_classrooms')

    _mark_stale_pending_materials_failed(request.user, selected_classroom)
    _purge_expired_archived_summaries(request.user)

    if request.method == 'POST':
        form = LectureUploadForm(request.POST, request.FILES)
        if form.is_valid():
            summary_mode = form.cleaned_data['SummaryMode']
            base_title = form.cleaned_data['Title'].strip()
            uploaded_files = form.cleaned_data['UploadFile']
            created_count = 0
            created_material_pks = []

            for uploaded in uploaded_files:
                file_data = uploaded.read()
                material_title = base_title
                if len(uploaded_files) > 1:
                    file_stem = os.path.splitext(uploaded.name)[0].strip()
                    file_hint = slugify(file_stem).replace('-', ' ').strip()
                    if file_hint:
                        material_title = f'{base_title} - {file_hint}'
                    else:
                        material_title = f'{base_title} - {uploaded.name}'

                material = LectureMaterial.objects.create(
                    Title=material_title,
                    OriginalFileName=uploaded.name,
                    MimeType=getattr(uploaded, 'content_type', '') or '',
                    FileSize=len(file_data),
                    FileData=file_data,
                    UploadedBy=request.user,
                    Classroom=selected_classroom,
                )
                created_material_pks.append(material.pk)

                created_count += 1

            run_background(_process_material_ai_batch, created_material_pks, request.user.pk, summary_mode)

            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse(
                    {
                        'created_count': created_count,
                        'summary_mode': summary_mode,
                        'detail': f'{created_count} lecture material(s) uploaded. AI summary processing started in {summary_mode} mode for this batch.',
                        'redirect_url': request.path,
                    }
                )

            messages.success(
                request,
                f'{created_count} lecture material(s) uploaded. AI summary processing started in {summary_mode} mode for this batch. Quiz questions will be generated when you publish the quiz.',
            )
            return redirect('content:educator_dashboard')
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'errors': form.errors}, status=400)
    else:
        form = LectureUploadForm()

    active_summaries = Summary.objects.filter(
        Lecture__UploadedBy=request.user,
        Lecture__Classroom=selected_classroom,
        IsArchived=False,
    ).select_related('Lecture', 'validation').order_by('-CreatedAt')
    archived_summaries = Summary.objects.filter(
        Lecture__UploadedBy=request.user,
        Lecture__Classroom=selected_classroom,
        IsArchived=True,
    ).exclude(validation__QualityStatus='user_deleted').select_related('Lecture').order_by('-ArchivedAt', '-CreatedAt')

    pending_count = _get_recent_pending_materials(request.user, selected_classroom).count()
    summary_count = active_summaries.count()
    archived_count = archived_summaries.count()
    return render(
        request,
        'educator_dashboard.html',
        {
            'form': form,
            'selected_classroom': selected_classroom,
            'active_summaries': active_summaries,
            'archived_summaries': archived_summaries,
            'pending_count': pending_count,
            'summary_count': summary_count,
            'archived_count': archived_count,
        },
    )


@login_required
def ai_processing_status(request):
    if not request.user.is_educator():
        return HttpResponseForbidden('Only educators can access this endpoint.')

    selected_classroom = _get_selected_educator_classroom(request)
    if selected_classroom is None:
        return JsonResponse({'detail': 'No classroom selected.'}, status=403)

    _mark_stale_pending_materials_failed(request.user, selected_classroom)
    _purge_expired_archived_summaries(request.user)

    pending_count = _get_recent_pending_materials(request.user, selected_classroom).count()
    active_summaries = Summary.objects.filter(
        Lecture__UploadedBy=request.user,
        Lecture__Classroom=selected_classroom,
        IsArchived=False,
    ).select_related('Lecture', 'validation').order_by('-CreatedAt')
    archived_summaries = Summary.objects.filter(
        Lecture__UploadedBy=request.user,
        Lecture__Classroom=selected_classroom,
        IsArchived=True,
    ).exclude(validation__QualityStatus='user_deleted').select_related('Lecture').order_by('-ArchivedAt', '-CreatedAt')

    summary_count = active_summaries.count()
    archived_count = archived_summaries.count()
    summaries_html = render_to_string(
        'content_app/_summary_queue.html',
        {'summaries': active_summaries},
        request=request,
    )
    archived_summaries_html = render_to_string(
        'content_app/_archived_summary_queue.html',
        {'summaries': archived_summaries},
        request=request,
    )
    return JsonResponse(
        {
            'pending_count': pending_count,
            'summary_count': summary_count,
            'archived_count': archived_count,
            'summaries_html': summaries_html,
            'archived_summaries_html': archived_summaries_html,
        }
    )


@login_required
def verify_summary(request, summary_id):
    if not request.user.is_educator():
        return HttpResponseForbidden('Only educators can verify summaries.')

    selected_classroom = _get_selected_educator_classroom(request)
    if selected_classroom is None:
        return redirect('content:educator_classrooms')

    summary = get_object_or_404(
        Summary,
        pk=summary_id,
        Lecture__UploadedBy=request.user,
        Lecture__Classroom=selected_classroom,
    )
    if summary.IsArchived:
        messages.error(request, 'Restore the summary before verifying it.')
        return redirect('content:educator_dashboard')
    summary.IsVerified = True
    summary.VerifiedBy = request.user
    summary.save(update_fields=['IsVerified', 'VerifiedBy'])

    SummaryValidation.objects.update_or_create(
        Summary=summary,
        defaults={
            'Lecture': summary.Lecture,
            'SummaryTextSnapshot': summary.SummaryText,
            'IsVerified': True,
            'VerifiedBy': request.user,
        },
    )

    messages.success(request, 'Summary has been marked as verified.')
    return redirect('content:educator_dashboard')


@login_required
def edit_summary(request, summary_id):
    if not request.user.is_educator():
        return HttpResponseForbidden('Only educators can edit summaries.')

    selected_classroom = _get_selected_educator_classroom(request)
    if selected_classroom is None:
        return redirect('content:educator_classrooms')

    summary = get_object_or_404(
        Summary,
        pk=summary_id,
        Lecture__UploadedBy=request.user,
        Lecture__Classroom=selected_classroom,
    )
    if summary.IsArchived:
        messages.error(request, 'Restore the summary before editing it.')
        return redirect('content:educator_dashboard')

    if request.method == 'POST':
        form = SummaryEditForm(request.POST)
        if form.is_valid():
            summary.SummaryText = form.cleaned_data['SummaryText']
            summary.IsVerified = False
            summary.VerifiedBy = None
            summary.save(update_fields=['SummaryText', 'IsVerified', 'VerifiedBy'])

            raw_text = _extract_material_text(summary.Lecture)
            quality = evaluate_summary_quality(summary.SummaryText, raw_text, mode='detailed')

            SummaryValidation.objects.update_or_create(
                Summary=summary,
                defaults={
                    'Lecture': summary.Lecture,
                    'SummaryTextSnapshot': summary.SummaryText,
                    'IsVerified': False,
                    'QualityScore': quality.get('score', 0),
                    'QualityStatus': quality.get('status', 'low'),
                    'QualityMetrics': quality.get('metrics', {}),
                    'VerifiedBy': None,
                },
            )

            messages.success(request, f'Summary for "{summary.Lecture.Title}" was updated. Please verify it again.')
            return redirect('content:educator_dashboard')
    else:
        form = SummaryEditForm(initial={'SummaryText': summary.SummaryText})

    return render(
        request,
        'content_app/summary_edit.html',
        {
            'form': form,
            'summary': summary,
        },
    )


@login_required
def delete_summary(request, summary_id):
    if not request.user.is_educator():
        return HttpResponseForbidden('Only educators can delete summaries.')
    if request.method != 'POST':
        return HttpResponseForbidden('Invalid request method.')

    selected_classroom = _get_selected_educator_classroom(request)
    if selected_classroom is None:
        return redirect('content:educator_classrooms')

    summary = get_object_or_404(
        Summary,
        pk=summary_id,
        Lecture__UploadedBy=request.user,
        Lecture__Classroom=selected_classroom,
    )

    lecture_title = summary.Lecture.Title
    summary.IsArchived = True
    summary.ArchivedAt = timezone.now()
    summary.save(update_fields=['IsArchived', 'ArchivedAt'])
    messages.success(request, f'Summary for "{lecture_title}" was moved to archive.')
    return redirect('content:educator_dashboard')


@login_required
def delete_archived_summary(request, summary_id):
    if not request.user.is_educator():
        return HttpResponseForbidden('Only educators can delete summaries.')
    if request.method != 'POST':
        return HttpResponseForbidden('Invalid request method.')

    selected_classroom = _get_selected_educator_classroom(request)
    if selected_classroom is None:
        return redirect('content:educator_classrooms')

    summary = get_object_or_404(
        Summary,
        pk=summary_id,
        IsArchived=True,
        Lecture__UploadedBy=request.user,
        Lecture__Classroom=selected_classroom,
    )

    lecture_title = summary.Lecture.Title
    lecture = summary.Lecture

    # Mark the summary as user-deleted via a SummaryValidation tombstone so that
    # any still-running background AI job can detect the deletion and skip
    # recreating the summary (see _process_material_ai).  The Summary row itself
    # is kept as an archived tombstone — it is invisible to all dashboard queries
    # that filter on IsArchived=False, so the educator never sees it again.
    SummaryValidation.objects.update_or_create(
        Summary=summary,
        defaults={
            'Lecture': lecture,
            'SummaryTextSnapshot': '',
            'IsVerified': False,
            'QualityScore': 0,
            'QualityStatus': 'user_deleted',
            'QualityMetrics': {},
            'VerifiedBy': None,
        },
    )
    _trace_ai(
        f'\U0001f5d1\ufe0f Summary User-Deleted: material_id={lecture.pk} \u2014 tombstone written, '
        'background AI jobs will skip summary creation for this lecture.'
    )

    messages.success(request, f'Summary for \"{lecture_title}\" was permanently deleted.')
    return redirect('content:educator_dashboard')


@login_required
def restore_summary(request, summary_id):
    if not request.user.is_educator():
        return HttpResponseForbidden('Only educators can restore summaries.')
    if request.method != 'POST':
        return HttpResponseForbidden('Invalid request method.')

    selected_classroom = _get_selected_educator_classroom(request)
    if selected_classroom is None:
        return redirect('content:educator_classrooms')

    summary = get_object_or_404(
        Summary,
        pk=summary_id,
        Lecture__UploadedBy=request.user,
        Lecture__Classroom=selected_classroom,
    )

    summary.IsArchived = False
    summary.ArchivedAt = None
    summary.save(update_fields=['IsArchived', 'ArchivedAt'])
    messages.success(request, f'Summary for "{summary.Lecture.Title}" was restored.')
    return redirect('content:educator_dashboard')


@login_required
def publish_quiz(request, lecture_id):
    if not request.user.is_educator():
        return HttpResponseForbidden('Only educators can publish quizzes.')
    if request.method != 'POST':
        return HttpResponseForbidden('Invalid request method.')

    selected_classroom = _get_selected_educator_classroom(request)
    if selected_classroom is None:
        return redirect('content:educator_classrooms')

    lecture = get_object_or_404(
        LectureMaterial,
        pk=lecture_id,
        UploadedBy=request.user,
        Classroom=selected_classroom,
    )
    if not hasattr(lecture, 'summary') or lecture.summary.IsArchived or not lecture.summary.IsVerified:
        messages.error(request, 'Verify the summary before publishing quiz questions.')
        return redirect('content:educator_dashboard')

    publish_mode = (request.POST.get('publish_mode') or 'both').strip().lower()
    if publish_mode not in PUBLISH_MODES:
        messages.error(request, 'Invalid publish mode selected.')
        return redirect('content:educator_dashboard')

    concept, _ = Concept.objects.get_or_create(
        ConceptName=lecture.Title,
        defaults={'Description': f'Auto-generated concept from {lecture.Title}'},
    )

    targets = QUIZ_GENERATION_TARGETS[publish_mode]
    target_types = set(targets.keys())

    existing_counts = {
        q_type: Question.objects.filter(Lecture=lecture, QuestionType=q_type).count()
        for q_type in target_types
    }
    missing_counts = {
        q_type: max(0, targets[q_type] - existing_counts.get(q_type, 0))
        for q_type in target_types
    }

    generation_text = ''
    generation_source = ''
    if any(count > 0 for count in missing_counts.values()):
        generation_text, generation_source = _resolve_question_generation_source(lecture)

    if any(count > 0 for count in missing_counts.values()) and not generation_text.strip():
        messages.error(request, 'Unable to generate questions because no extractable source text was found.')
        return redirect('content:educator_dashboard')

    if Question.TYPE_CONSTRUCTED in target_types and missing_counts.get(Question.TYPE_CONSTRUCTED, 0) > 0:
        target_constructed = targets[Question.TYPE_CONSTRUCTED]
        current_constructed = existing_counts.get(Question.TYPE_CONSTRUCTED, 0)
        for _ in range(QUIZ_GENERATION_MAX_ATTEMPTS):
            required_constructed = max(0, target_constructed - current_constructed)
            if required_constructed == 0:
                break

            generated_constructed = generate_constructed_questions(generation_text, count=required_constructed)
            if not generated_constructed:
                break

            generated_answer_keys = generate_constructed_answer_keys(generation_text, generated_constructed)
            for idx, generated in enumerate(generated_constructed):
                answer_key = (
                    generated_answer_keys[idx].strip()
                    if idx < len(generated_answer_keys) and (generated_answer_keys[idx] or '').strip()
                    else 'To be validated by educator'
                )
                Question.objects.create(
                    Lecture=lecture,
                    Concept=concept,
                    QuestionText=generated,
                    QuestionType=Question.TYPE_CONSTRUCTED,
                    CorrectAnswerText=answer_key,
                    DifficultyLevel=_difficulty_for_index(current_constructed),
                    IsPublished=False,
                    IsAIGenerated=True,
                )
                current_constructed += 1

        remaining_constructed = max(0, target_constructed - current_constructed)
        if remaining_constructed > 0:
            _trace_ai(
                f'⚠️ Constructed generation partial: lecture_id={lecture.pk}, '
                f'missing={remaining_constructed}, attempts={QUIZ_GENERATION_MAX_ATTEMPTS}'
            )

    if Question.TYPE_MCQ in target_types and missing_counts.get(Question.TYPE_MCQ, 0) > 0:
        target_mcq = targets[Question.TYPE_MCQ]
        current_mcq = existing_counts.get(Question.TYPE_MCQ, 0)
        for _ in range(QUIZ_GENERATION_MAX_ATTEMPTS):
            required_mcq = max(0, target_mcq - current_mcq)
            if required_mcq == 0:
                break

            generated_mcq = generate_mcq_questions(generation_text, count=required_mcq)
            if not generated_mcq:
                break

            for mcq in generated_mcq:
                Question.objects.create(
                    Lecture=lecture,
                    Concept=concept,
                    QuestionText=mcq['question_text'],
                    QuestionType=Question.TYPE_MCQ,
                    CorrectAnswerText=mcq['correct_answer'],
                    DifficultyLevel=_difficulty_for_index(current_mcq),
                    IsPublished=False,
                    IsAIGenerated=True,
                )
                current_mcq += 1

        remaining_mcq = max(0, target_mcq - current_mcq)
        if remaining_mcq > 0:
            _trace_ai(
                f'⚠️ MCQ generation partial: lecture_id={lecture.pk}, '
                f'missing={remaining_mcq}, attempts={QUIZ_GENERATION_MAX_ATTEMPTS}'
            )

    selected_ids = []
    selected_by_type = {}
    for q_type, required_count in targets.items():
        type_ids = list(
            Question.objects.filter(Lecture=lecture, QuestionType=q_type)
            .order_by('QuestionID')
            .values_list('QuestionID', flat=True)[:required_count]
        )
        selected_by_type[q_type] = type_ids
        selected_ids.extend(type_ids)

    for q_type, type_ids in selected_by_type.items():
        if not type_ids:
            continue
        Question.objects.filter(Lecture=lecture, QuestionType=q_type).exclude(QuestionID__in=type_ids).update(IsPublished=False)
        Question.objects.filter(QuestionID__in=type_ids).update(IsPublished=True)

    if not selected_ids:
        mode_label = 'MCQ' if publish_mode == 'mcq' else 'constructed-response' if publish_mode == 'constructed' else 'all'
        messages.warning(request, f'No {mode_label} questions found to publish for "{lecture.Title}".')
        return redirect('content:educator_dashboard')

    updated = len(selected_ids)
    mode_label = 'both MCQ and constructed-response' if publish_mode == 'both' else 'MCQ' if publish_mode == 'mcq' else 'constructed-response'
    source_suffix = f' using {generation_source}-based generation' if generation_source else ''
    messages.success(request, f'Published {updated} {mode_label} quiz questions for "{lecture.Title}"{source_suffix}.')
    return redirect('content:educator_dashboard')


@login_required
def manage_lecture_questions(request, lecture_id):
    if not request.user.is_educator():
        return HttpResponseForbidden('Only educators can manage quiz questions.')

    selected_classroom = _get_selected_educator_classroom(request)
    if selected_classroom is None:
        return redirect('content:educator_classrooms')

    lecture = get_object_or_404(
        LectureMaterial,
        pk=lecture_id,
        UploadedBy=request.user,
        Classroom=selected_classroom,
    )
    questions = Question.objects.filter(Lecture=lecture).select_related('Concept').order_by('QuestionID')

    if request.method == 'POST':
        form = QuestionEditForm(request.POST)
        if form.is_valid():
            question = form.save(commit=False)
            question.Lecture = lecture
            question.Concept = Concept.objects.filter(ConceptName=lecture.Title).first()
            question.IsAIGenerated = False
            question.save()
            messages.success(request, 'Question created successfully.')
            return redirect('content:manage_lecture_questions', lecture_id=lecture.LectureID)
    else:
        form = QuestionEditForm(initial={'DifficultyLevel': 'Medium', 'QuestionType': Question.TYPE_CONSTRUCTED})

    return render(
        request,
        'content_app/question_manager.html',
        {
            'lecture': lecture,
            'questions': questions,
            'create_form': form,
        },
    )


@login_required
def edit_lecture_question(request, question_id):
    if not request.user.is_educator():
        return HttpResponseForbidden('Only educators can edit quiz questions.')

    selected_classroom = _get_selected_educator_classroom(request)
    if selected_classroom is None:
        return redirect('content:educator_classrooms')

    question = get_object_or_404(
        Question.objects.select_related('Lecture'),
        pk=question_id,
        Lecture__UploadedBy=request.user,
        Lecture__Classroom=selected_classroom,
    )

    if request.method == 'POST':
        form = QuestionEditForm(request.POST, instance=question)
        if form.is_valid():
            form.save()
            messages.success(request, 'Question updated successfully.')
            return redirect('content:manage_lecture_questions', lecture_id=question.Lecture_id)
    else:
        form = QuestionEditForm(instance=question)

    return render(
        request,
        'content_app/question_manager.html',
        {
            'lecture': question.Lecture,
            'questions': Question.objects.filter(Lecture=question.Lecture).order_by('QuestionID'),
            'edit_form': form,
            'editing_question': question,
            'create_form': QuestionEditForm(initial={'DifficultyLevel': 'Medium', 'QuestionType': Question.TYPE_CONSTRUCTED}),
        },
    )


@login_required
def download_summary(request, summary_id):
    summary = get_object_or_404(Summary, pk=summary_id)
    if summary.IsArchived:
        return HttpResponseForbidden('Archived summaries cannot be downloaded.')
    if not summary.IsVerified and not request.user.is_educator():
        return HttpResponseForbidden('Only verified summaries are available to students.')
    if request.user.is_educator():
        selected_classroom = _get_selected_educator_classroom(request)
        if selected_classroom is None:
            return redirect('content:educator_classrooms')
        if summary.Lecture.UploadedBy_id != request.user.id or summary.Lecture.Classroom_id != selected_classroom.ClassroomID:
            return HttpResponseForbidden('This summary is not available for your selected classroom.')
    if request.user.is_student():
        has_access = ClassroomEnrollment.objects.filter(
            Student=request.user,
            IsActive=True,
            Classroom__IsActive=True,
            Classroom__CreatedBy=summary.Lecture.UploadedBy,
        ).exists()
        if not has_access:
            return HttpResponseForbidden('Join the educator class to access this summary.')

    filename = f"summary-{slugify(summary.Lecture.Title) or summary.pk}.txt"
    response = HttpResponse(summary.SummaryText, content_type='text/plain; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
def educator_classrooms(request):
    if not request.user.is_educator():
        return HttpResponseForbidden('Only educators can access this page.')

    managed_classrooms = (
        Classroom.objects.filter(CreatedBy=request.user)
        .annotate(
            ActiveStudentCount=Count(
                'enrollments',
                filter=Q(enrollments__IsActive=True, enrollments__Student__Role__RoleName='Student'),
            )
        )
        .order_by('-CreatedAt')
    )

    active_classroom_id = request.session.get('educator_active_classroom_id')
    if active_classroom_id and not managed_classrooms.filter(ClassroomID=active_classroom_id, IsActive=True).exists():
        request.session.pop('educator_active_classroom_id', None)
        active_classroom_id = None

    return render(
        request,
        'educator_classrooms.html',
        {
            'classroom_form': CreateClassroomForm(),
            'managed_classrooms': managed_classrooms,
            'active_classroom_id': active_classroom_id,
        },
    )


@login_required
def select_educator_classroom(request, classroom_id):
    if not request.user.is_educator():
        return HttpResponseForbidden('Only educators can access this page.')
    if request.method != 'POST':
        return redirect('content:educator_classrooms')

    classroom = Classroom.objects.filter(ClassroomID=classroom_id, CreatedBy=request.user, IsActive=True).first()
    if classroom is None:
        return HttpResponseForbidden('You can only select your own active classrooms.')

    request.session['educator_active_classroom_id'] = classroom.ClassroomID
    messages.success(request, f'Classroom "{classroom.Name}" selected.')
    return redirect('content:educator_classrooms')
