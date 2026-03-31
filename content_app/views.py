from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify

from ai_services.tasks import run_background
from ai_services.t5_model import generate_questions, summarize_text
from ai_services.text_extraction import extract_text_from_file
from learning_app.models import Concept, Question

from .forms import LectureUploadForm
from .models import LectureMaterial, Summary, SummaryValidation


def _process_material_ai(material_pk, educator_pk):
    material = LectureMaterial.objects.get(pk=material_pk)
    raw_text = extract_text_from_file(material.FilePath.path)
    summary_text = summarize_text(raw_text)
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

    if request.method == 'POST':
        form = LectureUploadForm(request.POST, request.FILES)
        if form.is_valid():
            material = form.save(commit=False)
            material.UploadedBy = request.user
            material.save()

            run_background(_process_material_ai, material.pk, request.user.pk)
            messages.success(request, 'Lecture uploaded. AI processing has started in the background.')
            return redirect('content:educator_dashboard')
    else:
        form = LectureUploadForm()

    summaries = Summary.objects.select_related('Lecture').order_by('-CreatedAt')
    return render(request, 'educator_dashboard.html', {'form': form, 'summaries': summaries})


@login_required
def verify_summary(request, summary_id):
    if not request.user.is_educator():
        return HttpResponseForbidden('Only educators can verify summaries.')

    summary = get_object_or_404(Summary, pk=summary_id)
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
def publish_quiz(request, lecture_id):
    if not request.user.is_educator():
        return HttpResponseForbidden('Only educators can publish quizzes.')

    lecture = get_object_or_404(LectureMaterial, pk=lecture_id)
    if not hasattr(lecture, 'summary') or not lecture.summary.IsVerified:
        messages.error(request, 'Verify the summary before publishing quiz questions.')
        return redirect('content:educator_dashboard')

    updated = Question.objects.filter(Lecture=lecture).update(IsPublished=True)
    messages.success(request, f'Published {updated} quiz questions for "{lecture.Title}".')
    return redirect('content:educator_dashboard')


@login_required
def download_summary(request, summary_id):
    summary = get_object_or_404(Summary, pk=summary_id)
    if not summary.IsVerified and not request.user.is_educator():
        return HttpResponseForbidden('Only verified summaries are available to students.')

    filename = f"summary-{slugify(summary.Lecture.Title) or summary.pk}.txt"
    response = HttpResponse(summary.SummaryText, content_type='text/plain; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
