from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from ai_services.t5_model import generate_similar_question
from content_app.models import Summary

from .models import Concept, Question, QuestionResponse, QuizAttempt

DIFFICULTY_ORDER = ['Easy', 'Medium', 'Hard']


def _next_difficulty(current_level, is_correct):
    idx = DIFFICULTY_ORDER.index(current_level) if current_level in DIFFICULTY_ORDER else 1
    if is_correct:
        return DIFFICULTY_ORDER[min(idx + 1, 2)]
    return DIFFICULTY_ORDER[max(idx - 1, 0)]


@login_required
def student_dashboard(request):
    if not request.user.is_student():
        return HttpResponseForbidden('Only students can access this page.')

    concepts = Concept.objects.all().order_by('ConceptName')
    attempts = QuizAttempt.objects.filter(User=request.user).order_by('-StartTime')[:7]

    # Annotate each attempt with its concept name derived from the first response
    attempt_data = []
    for attempt in attempts:
        first_resp = attempt.responses.select_related('Question__Concept').first()
        concept_name = (
            first_resp.Question.Concept.ConceptName
            if first_resp and first_resp.Question_id and first_resp.Question.Concept_id
            else 'N/A'
        )
        attempt_data.append({'attempt': attempt, 'concept_name': concept_name})

    verified_summaries = Summary.objects.filter(IsVerified=True).select_related('Lecture').order_by('-CreatedAt')[:12]

    return render(
        request,
        'student_dashboard.html',
        {
            'concepts': concepts,
            'attempt_data': attempt_data,
            'verified_summaries': verified_summaries,
        },
    )


@login_required
def start_quiz(request, concept_id):
    if not request.user.is_student():
        return HttpResponseForbidden('Only students can start a quiz.')

    concept = get_object_or_404(Concept, pk=concept_id)
    attempt = QuizAttempt.objects.create(User=request.user)
    # Store quiz context in session since QuizAttempt has no concept/difficulty columns (per ERD)
    request.session[f'quiz_{attempt.AttemptID}_concept_id'] = concept_id
    request.session[f'quiz_{attempt.AttemptID}_difficulty'] = 'Medium'
    return redirect('learning:student_quiz', attempt_id=attempt.AttemptID)


@login_required
def student_quiz(request, attempt_id):
    attempt = get_object_or_404(QuizAttempt, pk=attempt_id, User=request.user)
    retry_key = f'retry_pending_{attempt.AttemptID}'
    micro_lesson_key = f'micro_lesson_{attempt.AttemptID}'
    micro_lesson = request.session.get(micro_lesson_key, '')

    concept_id = request.session.get(f'quiz_{attempt.AttemptID}_concept_id')
    current_difficulty = request.session.get(f'quiz_{attempt.AttemptID}_difficulty', 'Medium')

    if request.session.get(retry_key):
        source_id = request.session.get(f'retry_source_{attempt.AttemptID}')
        source_question = Question.objects.filter(pk=source_id).first()
        return render(
            request,
            'student_quiz.html',
            {
                'attempt': attempt,
                'completed': False,
                'retry_pending': True,
                'source_question': source_question,
                'micro_lesson': micro_lesson,
            },
        )

    override_key = f'quiz_{attempt.AttemptID}_override_question_id'
    override_question_id = request.session.pop(override_key, None)
    question = None
    if override_question_id:
        question = Question.objects.filter(pk=override_question_id).first()

    answered_ids = attempt.responses.values_list('Question_id', flat=True)
    if question is None:
        question = (
            Question.objects.filter(
                Concept_id=concept_id,
                DifficultyLevel=current_difficulty,
                IsPublished=True,
            )
            .exclude(QuestionID__in=answered_ids)
            .first()
        )

    if question:
        request.session.pop(micro_lesson_key, None)

    if question is None:
        attempt.EndTime = timezone.now()
        total = attempt.responses.count()
        correct = attempt.responses.filter(IsCorrect=True).count()
        attempt.TotalScore = int((correct / total * 100) if total else 0)
        attempt.save(update_fields=['EndTime', 'TotalScore'])
        return render(request, 'student_quiz.html', {'attempt': attempt, 'completed': True})

    return render(
        request,
        'student_quiz.html',
        {
            'attempt': attempt,
            'question': question,
            'current_difficulty': current_difficulty,
            'completed': False,
            'micro_lesson': micro_lesson,
        },
    )


@login_required
def submit_answer(request, attempt_id):
    attempt = get_object_or_404(QuizAttempt, pk=attempt_id, User=request.user)

    if request.method != 'POST':
        return redirect('learning:student_quiz', attempt_id=attempt.AttemptID)

    question_id = request.POST.get('question_id')
    submitted_answer = request.POST.get('answer', '').strip()
    time_taken = int(request.POST.get('time_taken', 0))

    question = get_object_or_404(Question, pk=question_id)
    is_correct = submitted_answer.lower() == question.CorrectAnswerText.strip().lower()

    QuestionResponse.objects.create(
        Attempt=attempt,
        Question=question,
        StudentAnswerText=submitted_answer,
        IsCorrect=is_correct,
        TimeTaken=time_taken,
    )

    current_difficulty = request.session.get(f'quiz_{attempt.AttemptID}_difficulty', 'Medium')
    request.session[f'quiz_{attempt.AttemptID}_difficulty'] = _next_difficulty(current_difficulty, is_correct)

    if not is_correct:
        if question.Concept and question.Concept.micro_lesson:
            request.session[f'micro_lesson_{attempt.AttemptID}'] = question.Concept.micro_lesson
        request.session[f'retry_pending_{attempt.AttemptID}'] = True
        request.session[f'retry_source_{attempt.AttemptID}'] = question.QuestionID

    return redirect('learning:student_quiz', attempt_id=attempt.AttemptID)


@login_required
def retry_similar_question(request, attempt_id):
    if not request.user.is_student():
        return HttpResponseForbidden('Only students can retry quiz questions.')

    attempt = get_object_or_404(QuizAttempt, pk=attempt_id, User=request.user)
    if request.method != 'POST':
        return redirect('learning:student_quiz', attempt_id=attempt.AttemptID)

    retry_key = f'retry_pending_{attempt.AttemptID}'
    source_id = request.session.get(f'retry_source_{attempt.AttemptID}')
    source_question = get_object_or_404(Question, pk=source_id)

    similar_text = generate_similar_question(
        source_question.QuestionText,
        source_question.Concept.ConceptName if source_question.Concept else '',
    )
    generated = Question.objects.create(
        Lecture=source_question.Lecture,
        Concept=source_question.Concept,
        QuestionText=similar_text,
        DifficultyLevel=source_question.DifficultyLevel,
        CorrectAnswerText=source_question.CorrectAnswerText,
        IsPublished=True,
        IsAIGenerated=True,
    )

    request.session[f'quiz_{attempt.AttemptID}_override_question_id'] = generated.QuestionID
    request.session.pop(retry_key, None)
    request.session.pop(f'retry_source_{attempt.AttemptID}', None)
    request.session.pop(f'micro_lesson_{attempt.AttemptID}', None)

    return redirect('learning:student_quiz', attempt_id=attempt.AttemptID)
