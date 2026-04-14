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

from ai_services.tasks import run_background
from ai_services.summary_quality import evaluate_summary_quality
from ai_services.ai_orchestrator import generate_questions, summarize_text
from ai_services.text_extraction import extract_text_from_bytes
from learning_app.forms import CreateClassroomForm
from learning_app.models import Classroom, ClassroomEnrollment, Concept, Question

from .forms import LectureUploadForm, QuestionEditForm, SummaryEditForm
from .models import LectureMaterial, Summary, SummaryValidation


ARCHIVE_RETENTION_DAYS = 30
PUBLISH_MODES = {'mcq', 'constructed', 'both'}


def _trace_ai(message: str) -> None:
    print(message, flush=True)


def _purge_expired_archived_summaries(user):
    cutoff = timezone.now() - timedelta(days=ARCHIVE_RETENTION_DAYS)
    Summary.objects.filter(
        Lecture__UploadedBy=user,
        IsArchived=True,
        ArchivedAt__lt=cutoff,
    ).delete()


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

        concept, _ = Concept.objects.get_or_create(
            ConceptName=material.Title,
            defaults={'Description': f'Auto-generated concept from {material.Title}'},
        )

        generated_questions = generate_questions(raw_text)[:10]
        for generated in generated_questions:
            Question.objects.create(
                Lecture=material,
                Concept=concept,
                QuestionText=generated,
                QuestionType=Question.TYPE_CONSTRUCTED,
                CorrectAnswerText='To be validated by educator',
                DifficultyLevel='Medium',
                IsPublished=False,
                IsAIGenerated=True,
            )

        _trace_ai(
            f'✅ AI Analysis Success: material_id={material_pk}, summary_score={quality.get("score", 0)}, '
            f'questions_created={len(generated_questions)}'
        )
    except Exception as exc:
        _trace_ai(f'❌ AI Analysis Failure: material_id={material_pk}, error={exc}')
        raise


@login_required
def educator_dashboard(request):
    if not request.user.is_educator():
        return HttpResponseForbidden('Only educators can access this page.')

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
            )

            summary_mode = form.cleaned_data['SummaryMode']
            run_background(_process_material_ai, material.pk, request.user.pk, summary_mode)
            messages.success(
                request,
                f'Lecture uploaded. AI processing started in {summary_mode} mode.',
            )
            return redirect('content:educator_dashboard')
    else:
        form = LectureUploadForm()

    active_summaries = Summary.objects.filter(
        Lecture__UploadedBy=request.user,
        IsArchived=False,
    ).select_related('Lecture', 'validation').order_by('-CreatedAt')
    archived_summaries = Summary.objects.filter(
        Lecture__UploadedBy=request.user,
        IsArchived=True,
    ).select_related('Lecture').order_by('-ArchivedAt', '-CreatedAt')
    pending_count = LectureMaterial.objects.filter(UploadedBy=request.user, summary__isnull=True).count()
    summary_count = active_summaries.count()
    archived_count = archived_summaries.count()
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
    return render(
        request,
        'educator_dashboard.html',
        {
            'form': form,
            'classroom_form': CreateClassroomForm(),
            'managed_classrooms': managed_classrooms,
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

    _purge_expired_archived_summaries(request.user)

    pending_count = LectureMaterial.objects.filter(UploadedBy=request.user, summary__isnull=True).count()
    active_summaries = Summary.objects.filter(
        Lecture__UploadedBy=request.user,
        IsArchived=False,
    ).select_related('Lecture', 'validation').order_by('-CreatedAt')
    archived_summaries = Summary.objects.filter(
        Lecture__UploadedBy=request.user,
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

    summary = get_object_or_404(Summary, pk=summary_id)
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

    summary = get_object_or_404(Summary, pk=summary_id)
    if summary.Lecture.UploadedBy_id != request.user.id:
        return HttpResponseForbidden('You can only edit your own lecture summaries.')
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

    summary = get_object_or_404(Summary, pk=summary_id)
    if summary.Lecture.UploadedBy_id != request.user.id:
        return HttpResponseForbidden('You can only delete your own lecture summaries.')

    lecture_title = summary.Lecture.Title
    summary.IsArchived = True
    summary.ArchivedAt = timezone.now()
    summary.save(update_fields=['IsArchived', 'ArchivedAt'])
    messages.success(request, f'Summary for "{lecture_title}" was moved to archive.')
    return redirect('content:educator_dashboard')


@login_required
def restore_summary(request, summary_id):
    if not request.user.is_educator():
        return HttpResponseForbidden('Only educators can restore summaries.')
    if request.method != 'POST':
        return HttpResponseForbidden('Invalid request method.')

    summary = get_object_or_404(Summary, pk=summary_id)
    if summary.Lecture.UploadedBy_id != request.user.id:
        return HttpResponseForbidden('You can only restore your own lecture summaries.')

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

    lecture = get_object_or_404(LectureMaterial, pk=lecture_id)
    if not hasattr(lecture, 'summary') or lecture.summary.IsArchived or not lecture.summary.IsVerified:
        messages.error(request, 'Verify the summary before publishing quiz questions.')
        return redirect('content:educator_dashboard')

    publish_mode = (request.POST.get('publish_mode') or 'both').strip().lower()
    if publish_mode not in PUBLISH_MODES:
        messages.error(request, 'Invalid publish mode selected.')
        return redirect('content:educator_dashboard')

    questions_qs = Question.objects.filter(Lecture=lecture)
    if publish_mode == 'mcq':
        questions_qs = questions_qs.filter(QuestionType=Question.TYPE_MCQ)
    elif publish_mode == 'constructed':
        questions_qs = questions_qs.filter(QuestionType=Question.TYPE_CONSTRUCTED)

    selected_ids = list(questions_qs.values_list('QuestionID', flat=True))

    if not selected_ids:
        mode_label = 'MCQ' if publish_mode == 'mcq' else 'constructed-response' if publish_mode == 'constructed' else 'all'
        messages.warning(request, f'No {mode_label} questions found to publish for "{lecture.Title}".')
        return redirect('content:educator_dashboard')

    updated = Question.objects.filter(QuestionID__in=selected_ids).update(IsPublished=True)
    mode_label = 'both MCQ and constructed-response' if publish_mode == 'both' else 'MCQ' if publish_mode == 'mcq' else 'constructed-response'
    messages.success(request, f'Published {updated} {mode_label} quiz questions for "{lecture.Title}".')
    return redirect('content:educator_dashboard')


@login_required
def manage_lecture_questions(request, lecture_id):
    if not request.user.is_educator():
        return HttpResponseForbidden('Only educators can manage quiz questions.')

    lecture = get_object_or_404(LectureMaterial, pk=lecture_id, UploadedBy=request.user)
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

    question = get_object_or_404(Question.objects.select_related('Lecture'), pk=question_id)
    if question.Lecture.UploadedBy_id != request.user.id:
        return HttpResponseForbidden('You can only edit questions from your own lectures.')

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
