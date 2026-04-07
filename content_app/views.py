from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.http import HttpResponse
from django.http import HttpResponseForbidden
from django.utils import timezone
from django.template.loader import render_to_string
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from datetime import timedelta

from ai_services.tasks import run_background
from ai_services.t5_model import generate_questions, summarize_text
from ai_services.text_extraction import extract_text_from_bytes
from learning_app.models import Concept, Question

from .forms import LectureUploadForm, SummaryEditForm
from .models import LectureMaterial, Summary, SummaryValidation


ARCHIVE_RETENTION_DAYS = 30


def _purge_expired_archived_summaries(user):
    cutoff = timezone.now() - timedelta(days=ARCHIVE_RETENTION_DAYS)
    Summary.objects.filter(
        Lecture__UploadedBy=user,
        IsArchived=True,
        ArchivedAt__lt=cutoff,
    ).delete()


def _process_material_ai(material_pk, educator_pk, summary_mode='detailed'):
    material = LectureMaterial.objects.get(pk=material_pk)
    raw_text = extract_text_from_bytes(material.OriginalFileName, material.FileData)
    summary_text = summarize_text(raw_text, summary_mode=summary_mode)
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
            'VerifiedBy': None,
        },
    )

    concept, _ = Concept.objects.get_or_create(
        ConceptName=material.Title,
        defaults={'Description': f'Auto-generated concept from {material.Title}'},
    )

    for generated in generate_questions(raw_text)[:10]:
        Question.objects.create(
            Lecture=material,
            Concept=concept,
            QuestionText=generated,
            CorrectAnswerText='To be validated by educator',
            DifficultyLevel='Medium',
            IsPublished=False,
            IsAIGenerated=True,
        )


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
    ).select_related('Lecture').order_by('-CreatedAt')
    archived_summaries = Summary.objects.filter(
        Lecture__UploadedBy=request.user,
        IsArchived=True,
    ).select_related('Lecture').order_by('-ArchivedAt', '-CreatedAt')
    pending_count = LectureMaterial.objects.filter(UploadedBy=request.user, summary__isnull=True).count()
    summary_count = active_summaries.count()
    archived_count = archived_summaries.count()
    return render(
        request,
        'educator_dashboard.html',
        {
            'form': form,
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
    ).select_related('Lecture').order_by('-CreatedAt')
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

            SummaryValidation.objects.update_or_create(
                Summary=summary,
                defaults={
                    'Lecture': summary.Lecture,
                    'SummaryTextSnapshot': summary.SummaryText,
                    'IsVerified': False,
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

    lecture = get_object_or_404(LectureMaterial, pk=lecture_id)
    if not hasattr(lecture, 'summary') or lecture.summary.IsArchived or not lecture.summary.IsVerified:
        messages.error(request, 'Verify the summary before publishing quiz questions.')
        return redirect('content:educator_dashboard')

    updated = Question.objects.filter(Lecture=lecture).update(IsPublished=True)
    messages.success(request, f'Published {updated} quiz questions for "{lecture.Title}".')
    return redirect('content:educator_dashboard')


@login_required
def download_summary(request, summary_id):
    summary = get_object_or_404(Summary, pk=summary_id)
    if summary.IsArchived:
        return HttpResponseForbidden('Archived summaries cannot be downloaded.')
    if not summary.IsVerified and not request.user.is_educator():
        return HttpResponseForbidden('Only verified summaries are available to students.')

    filename = f"summary-{slugify(summary.Lecture.Title) or summary.pk}.txt"
    response = HttpResponse(summary.SummaryText, content_type='text/plain; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
