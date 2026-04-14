from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from ai_services.ai_orchestrator import generate_micro_lesson, generate_similar_question
from content_app.models import Summary

from .forms import JoinClassroomForm, CreateClassroomForm
from .models import Classroom, ClassroomEnrollment, Concept, Question, QuestionResponse, QuizAttempt

DIFFICULTY_ORDER = ['Easy', 'Medium', 'Hard']


def _trace_ai(message: str) -> None:
    print(message, flush=True)


def _next_difficulty(current_level, is_correct):
    idx = DIFFICULTY_ORDER.index(current_level) if current_level in DIFFICULTY_ORDER else 1
    if is_correct:
        return DIFFICULTY_ORDER[min(idx + 1, 2)]
    return DIFFICULTY_ORDER[max(idx - 1, 0)]


def _get_student_active_enrollments(user):
    return ClassroomEnrollment.objects.filter(
        Student=user,
        IsActive=True,
        Classroom__IsActive=True,
    ).select_related('Classroom', 'Classroom__CreatedBy')


def _get_student_allowed_educator_ids(user):
    return list(
        _get_student_active_enrollments(user).values_list('Classroom__CreatedBy_id', flat=True).distinct()
    )


@login_required
def student_dashboard(request):
    if not request.user.is_student():
        return HttpResponseForbidden('Only students can access this page.')

    enrolled_classrooms = _get_student_active_enrollments(request.user).order_by('Classroom__Name')
    allowed_educator_ids = _get_student_allowed_educator_ids(request.user)

    concepts = (
        Concept.objects.filter(
            questions__IsPublished=True,
            questions__Lecture__UploadedBy_id__in=allowed_educator_ids,
        )
        .distinct()
        .order_by('ConceptName')
    )
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

    verified_summaries = (
        Summary.objects.filter(
            IsVerified=True,
            IsArchived=False,
            Lecture__UploadedBy_id__in=allowed_educator_ids,
        )
        .select_related('Lecture', 'Lecture__UploadedBy')
        .order_by('-CreatedAt')[:12]
    )

    return render(
        request,
        'student_dashboard.html',
        {
            'concepts': concepts,
            'attempt_data': attempt_data,
            'verified_summaries': verified_summaries,
            'join_form': JoinClassroomForm(),
            'enrolled_classrooms': enrolled_classrooms,
        },
    )


@login_required
def start_quiz(request, concept_id):
    if not request.user.is_student():
        return HttpResponseForbidden('Only students can start a quiz.')

    allowed_educator_ids = _get_student_allowed_educator_ids(request.user)
    if not allowed_educator_ids:
        messages.error(request, 'Join a class with a valid code before starting quizzes.')
        return redirect('learning:student_dashboard')

    concept = get_object_or_404(Concept, pk=concept_id)
    has_published_questions = Question.objects.filter(
        Concept=concept,
        IsPublished=True,
        Lecture__UploadedBy_id__in=allowed_educator_ids,
    ).exists()
    if not has_published_questions:
        return HttpResponseForbidden('You are not enrolled in a class that grants access to this concept.')

    attempt = QuizAttempt.objects.create(User=request.user)
    # Store quiz context in session since QuizAttempt has no concept/difficulty columns (per ERD)
    request.session[f'quiz_{attempt.AttemptID}_concept_id'] = concept_id
    request.session[f'quiz_{attempt.AttemptID}_difficulty'] = 'Medium'
    request.session[f'quiz_{attempt.AttemptID}_allowed_educator_ids'] = allowed_educator_ids
    return redirect('learning:student_quiz', attempt_id=attempt.AttemptID)


@login_required
def student_quiz(request, attempt_id):
    attempt = get_object_or_404(QuizAttempt, pk=attempt_id, User=request.user)
    retry_key = f'retry_pending_{attempt.AttemptID}'
    micro_lesson_key = f'micro_lesson_{attempt.AttemptID}'
    micro_lesson = request.session.get(micro_lesson_key, '')

    concept_id = request.session.get(f'quiz_{attempt.AttemptID}_concept_id')
    current_difficulty = request.session.get(f'quiz_{attempt.AttemptID}_difficulty', 'Medium')
    allowed_educator_ids = request.session.get(f'quiz_{attempt.AttemptID}_allowed_educator_ids', [])

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
                Lecture__UploadedBy_id__in=allowed_educator_ids,
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
        _trace_ai(
            f'🏁 Quiz Completed: attempt_id={attempt.AttemptID}, total={total}, correct={correct}, '
            f'score={attempt.TotalScore}'
        )
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
    allowed_educator_ids = request.session.get(f'quiz_{attempt.AttemptID}_allowed_educator_ids', [])
    if question.Lecture.UploadedBy_id not in allowed_educator_ids:
        return HttpResponseForbidden('This question is not available for your enrolled classes.')

    is_correct = submitted_answer.lower() == question.CorrectAnswerText.strip().lower()

    _trace_ai(
        f'📝 Quiz Answer Submitted: attempt_id={attempt.AttemptID}, question_id={question.QuestionID}, '
        f'is_correct={is_correct}, difficulty={request.session.get(f"quiz_{attempt.AttemptID}_difficulty", "Medium")}'
    )

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
        _trace_ai(f'🔁 Wrong Answer Detected: attempt_id={attempt.AttemptID}, generating micro-lesson')
        concept_name = question.Concept.ConceptName if question.Concept else ''
        concept_micro_lesson = question.Concept.micro_lesson if question.Concept and question.Concept.micro_lesson else ''
        request.session[f'micro_lesson_{attempt.AttemptID}'] = generate_micro_lesson(
            question_text=question.QuestionText,
            student_answer=submitted_answer,
            correct_answer=question.CorrectAnswerText,
            concept_name=concept_name,
            fallback_text=concept_micro_lesson,
        )
        request.session[f'retry_pending_{attempt.AttemptID}'] = True
        request.session[f'retry_source_{attempt.AttemptID}'] = question.QuestionID
        _trace_ai(f'✅ Wrong Answer Flow Ready: attempt_id={attempt.AttemptID}, retry_pending=True')
    else:
        _trace_ai(f'✅ Correct Answer Recorded: attempt_id={attempt.AttemptID}, moving to next question')

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

    _trace_ai(
        f'🚀 Retry Question Generation Started: attempt_id={attempt.AttemptID}, '
        f'source_question_id={source_question.QuestionID}'
    )

    similar_text = generate_similar_question(
        source_question.QuestionText,
        source_question.Concept.ConceptName if source_question.Concept else '',
    )
    generated = Question.objects.create(
        Lecture=source_question.Lecture,
        Concept=source_question.Concept,
        QuestionText=similar_text,
        QuestionType=source_question.QuestionType,
        DifficultyLevel=source_question.DifficultyLevel,
        CorrectAnswerText=source_question.CorrectAnswerText,
        IsPublished=True,
        IsAIGenerated=True,
    )

    _trace_ai(
        f'✅ Retry Question Generation Success: attempt_id={attempt.AttemptID}, '
        f'generated_question_id={generated.QuestionID}'
    )

    request.session[f'quiz_{attempt.AttemptID}_override_question_id'] = generated.QuestionID
    request.session.pop(retry_key, None)
    request.session.pop(f'retry_source_{attempt.AttemptID}', None)
    request.session.pop(f'micro_lesson_{attempt.AttemptID}', None)

    _trace_ai(f'📚 Micro-Lesson Cleared: attempt_id={attempt.AttemptID}, ready for retry')

    return redirect('learning:student_quiz', attempt_id=attempt.AttemptID)


@login_required
def join_classroom(request):
    if not request.user.is_student():
        return HttpResponseForbidden('Only students can join classes.')
    if request.method != 'POST':
        return redirect('learning:student_dashboard')

    form = JoinClassroomForm(request.POST)
    if not form.is_valid():
        messages.error(request, next(iter(form.errors.get('join_code', [])), 'Invalid class code.'))
        return redirect('learning:student_dashboard')

    code = form.cleaned_data['join_code']
    classroom = Classroom.objects.filter(JoinCode=code).select_related('CreatedBy').first()
    if classroom is None:
        messages.error(request, 'Invalid class code. Check the code and try again.')
        return redirect('learning:student_dashboard')

    if not classroom.is_joinable():
        messages.error(request, 'This class code is inactive or expired. Ask your educator for a new code.')
        return redirect('learning:student_dashboard')

    enrollment, created = ClassroomEnrollment.objects.get_or_create(
        Classroom=classroom,
        Student=request.user,
        defaults={'IsActive': True},
    )
    if not created and not enrollment.IsActive:
        enrollment.IsActive = True
        enrollment.save(update_fields=['IsActive'])

    if created:
        messages.success(request, f'Joined class "{classroom.Name}" successfully.')
    else:
        messages.info(request, f'You are already enrolled in "{classroom.Name}".')
    return redirect('learning:student_dashboard')


@login_required
def create_classroom(request):
    if not request.user.is_educator():
        return HttpResponseForbidden('Only educators can create classes.')
    if request.method != 'POST':
        return redirect('content:educator_dashboard')

    form = CreateClassroomForm(request.POST)
    if not form.is_valid():
        messages.error(request, next(iter(form.errors.get('name', [])), 'Unable to create class.'))
        return redirect('content:educator_dashboard')

    classroom = Classroom(
        Name=form.cleaned_data['name'],
        CreatedBy=request.user,
    )
    classroom.generate_join_code(length=form.cleaned_data['code_length'])
    classroom.save()
    messages.success(request, f'Class "{classroom.Name}" created. Join code: {classroom.JoinCode}')
    return redirect('content:educator_dashboard')


@login_required
def regenerate_classroom_code(request, classroom_id):
    if not request.user.is_educator():
        return HttpResponseForbidden('Only educators can manage class codes.')
    if request.method != 'POST':
        return redirect('content:educator_dashboard')

    classroom = get_object_or_404(Classroom, pk=classroom_id, CreatedBy=request.user)
    classroom.regenerate_join_code()
    classroom.save(update_fields=['JoinCode', 'UpdatedAt'])
    messages.success(request, f'New join code for "{classroom.Name}": {classroom.JoinCode}')
    return redirect('content:educator_dashboard')


@login_required
def set_classroom_status(request, classroom_id):
    if not request.user.is_educator():
        return HttpResponseForbidden('Only educators can manage classes.')
    if request.method != 'POST':
        return redirect('content:educator_dashboard')

    classroom = get_object_or_404(Classroom, pk=classroom_id, CreatedBy=request.user)
    desired = request.POST.get('is_active', '1') == '1'
    classroom.IsActive = desired
    classroom.save(update_fields=['IsActive', 'UpdatedAt'])
    if desired:
        messages.success(request, f'Class "{classroom.Name}" is now active.')
    else:
        messages.success(request, f'Class "{classroom.Name}" has been deactivated.')
    return redirect('content:educator_dashboard')
