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

from ai_services.tasks import run_background
from ai_services.summary_quality import evaluate_summary_quality
from ai_services.ai_orchestrator import (
    generate_constructed_answer_keys,
    generate_constructed_questions,
    generate_mcq_questions,
    summarize_text,
)
from ai_services.text_extraction import extract_text_from_bytes
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
    'both': {Question.TYPE_MCQ: 10, Question.TYPE_CONSTRUCTED: 10},
}
DIFFICULTY_MIX_PATTERN = ['Easy', 'Easy', 'Easy', 'Medium', 'Medium', 'Medium', 'Medium', 'Hard', 'Hard', 'Hard']


def _trace_ai(message: str) -> None:
    print(message, flush=True)


def _resolve_question_generation_source(lecture):
    summary_text = ''
    quality_score = 0.0

    if hasattr(lecture, 'summary') and lecture.summary and not lecture.summary.IsArchived:
        summary_text = (lecture.summary.SummaryText or '').strip()
        validation = getattr(lecture.summary, 'validation', None)
        if validation is not None:
            try:
                quality_score = float(validation.QualityScore or 0)
            except (TypeError, ValueError):
                quality_score = 0.0

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

    material_text = extract_text_from_bytes(lecture.OriginalFileName, lecture.FileData)
    _trace_ai(
        f'📄 Quiz Source Selected: lecture_id={lecture.pk}, source=material, '
        f'summary_chars={len(summary_text)}, summary_quality={quality_score:.1f}, material_chars={len(material_text)}'
    )
    return material_text, 'material'


def _difficulty_for_index(index: int) -> str:
    return DIFFICULTY_MIX_PATTERN[index % len(DIFFICULTY_MIX_PATTERN)]


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


def _process_material_ai(material_pk, educator_pk, summary_mode='detailed'):
    try:
        material = LectureMaterial.objects.get(pk=material_pk)
        _trace_ai(f'🚀 AI Analysis Started: material_id={material_pk}, title="{material.Title}", mode={summary_mode}')
        raw_text = extract_text_from_bytes(material.OriginalFileName, material.FileData)
        _trace_ai(f'📄 AI Text Extracted: material_id={material_pk}, chars={len(raw_text)}')

        summary_text = summarize_text(raw_text, summary_mode=summary_mode)
        quality = evaluate_summary_quality(summary_text, raw_text, mode=summary_mode)
        summary, _ = Summary.objects.update_or_create(
            Lecture=material,
            defaults={'SummaryText': summary_text, 'IsVerified': False},
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
            uploaded = form.cleaned_data['UploadFile']
            file_data = uploaded.read()
            material = LectureMaterial.objects.create(
                Title=form.cleaned_data['Title'],
                OriginalFileName=uploaded.name,
                MimeType=getattr(uploaded, 'content_type', '') or '',
                FileSize=len(file_data),
                FileData=file_data,
                UploadedBy=request.user,
                Classroom=selected_classroom,
            )

            summary_mode = form.cleaned_data['SummaryMode']
            run_background(_process_material_ai, material.pk, request.user.pk, summary_mode)
            messages.success(
                request,
                f'Lecture uploaded. AI summary processing started in {summary_mode} mode. Quiz questions will be generated when you publish the quiz.',
            )
            return redirect('content:educator_dashboard')
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
    ).select_related('Lecture').order_by('-ArchivedAt', '-CreatedAt')
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
    ).select_related('Lecture').order_by('-ArchivedAt', '-CreatedAt')
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

            raw_text = extract_text_from_bytes(summary.Lecture.OriginalFileName, summary.Lecture.FileData)
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
    summary.delete()
    messages.success(request, f'Summary for "{lecture_title}" was permanently deleted.')
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
        required_constructed = missing_counts[Question.TYPE_CONSTRUCTED]
        generated_constructed = generate_constructed_questions(generation_text, count=required_constructed)
        generated_answer_keys = generate_constructed_answer_keys(generation_text, generated_constructed)
        starting_idx = existing_counts.get(Question.TYPE_CONSTRUCTED, 0)
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
                DifficultyLevel=_difficulty_for_index(starting_idx + idx),
                IsPublished=False,
                IsAIGenerated=True,
            )

    if Question.TYPE_MCQ in target_types and missing_counts.get(Question.TYPE_MCQ, 0) > 0:
        required_mcq = missing_counts[Question.TYPE_MCQ]
        generated_mcq = generate_mcq_questions(generation_text, count=required_mcq)
        starting_idx = existing_counts.get(Question.TYPE_MCQ, 0)
        for mcq in generated_mcq:
            Question.objects.create(
                Lecture=lecture,
                Concept=concept,
                QuestionText=mcq['question_text'],
                QuestionType=Question.TYPE_MCQ,
                CorrectAnswerText=mcq['correct_answer'],
                DifficultyLevel=_difficulty_for_index(starting_idx),
                IsPublished=False,
                IsAIGenerated=True,
            )
            starting_idx += 1

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
