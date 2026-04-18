from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from ai_services.ai_orchestrator import generate_micro_lesson
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


def _normalize_mcq_answer(answer: str) -> str:
    cleaned = (answer or '').strip().upper()
    if not cleaned:
        return ''
    if cleaned and cleaned[0] in {'A', 'B', 'C', 'D'}:
        return cleaned[0]
    return cleaned


def _mcq_correct_by_option_text(question_text: str, submitted: str, correct_key: str) -> bool:
    lines = [line.strip() for line in (question_text or '').splitlines() if line.strip()]
    options = {}
    for line in lines:
        upper = line.upper()
        if len(line) >= 3 and upper[0] in {'A', 'B', 'C', 'D'} and upper[1] == ')':
            options[upper[0]] = line[2:].strip().lower()

    if correct_key in options and submitted.lower() == options[correct_key]:
        return True
    return False


def _get_or_generate_micro_lesson(question: Question, student_answer: str) -> str:
    concept = question.Concept
    cached_lesson = (concept.micro_lesson or '').strip() if concept else ''
    if cached_lesson:
        _trace_ai(
            f'📚 Reusing Cached Micro-Lesson: concept_id={concept.ConceptID}, question_id={question.QuestionID}'
        )
        return cached_lesson

    generated = generate_micro_lesson(
        question_text=question.QuestionText,
        student_answer=student_answer,
        correct_answer=question.CorrectAnswerText,
        concept_name=concept.ConceptName if concept else '',
        fallback_text='',
    )

    if concept and generated.strip() and not (concept.micro_lesson or '').strip():
        concept.micro_lesson = generated
        concept.save(update_fields=['micro_lesson'])
        _trace_ai(
            f'💾 Cached Micro-Lesson Saved: concept_id={concept.ConceptID}, question_id={question.QuestionID}'
        )

    return generated


def _get_student_active_enrollments(user):
    return ClassroomEnrollment.objects.filter(
        Student=user,
        IsActive=True,
        Classroom__IsActive=True,
    ).select_related('Classroom', 'Classroom__CreatedBy')


def _get_selected_enrollment(request, user):
    selected_classroom_id = request.session.get('student_active_classroom_id')
    if not selected_classroom_id:
        return None

    return _get_student_active_enrollments(user).filter(Classroom_id=selected_classroom_id).first()


@login_required
def student_dashboard(request):
    if not request.user.is_student():
        return HttpResponseForbidden('Only students can access this page.')

    selected_enrollment = _get_selected_enrollment(request, request.user)
    if selected_enrollment is None:
        return redirect('learning:student_classrooms')
    selected_educator_id = selected_enrollment.Classroom.CreatedBy_id

    concepts = (
        Concept.objects.filter(
            questions__IsPublished=True,
            questions__Lecture__UploadedBy_id=selected_educator_id,
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
            Lecture__UploadedBy_id=selected_educator_id,
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
            'selected_enrollment': selected_enrollment,
        },
    )


@login_required
def student_classrooms(request):
    if not request.user.is_student():
        return HttpResponseForbidden('Only students can access this page.')

    enrolled_classrooms = _get_student_active_enrollments(request.user).order_by('Classroom__Name')
    active_classroom_id = request.session.get('student_active_classroom_id')
    if active_classroom_id and not enrolled_classrooms.filter(Classroom_id=active_classroom_id).exists():
        request.session.pop('student_active_classroom_id', None)
        active_classroom_id = None

    return render(
        request,
        'student_classrooms.html',
        {
            'join_form': JoinClassroomForm(),
            'enrolled_classrooms': enrolled_classrooms,
            'active_classroom_id': active_classroom_id,
        },
    )


@login_required
def start_quiz(request, concept_id):
    if not request.user.is_student():
        return HttpResponseForbidden('Only students can start a quiz.')

    selected_enrollment = _get_selected_enrollment(request, request.user)
    if selected_enrollment is None:
        messages.error(request, 'Select a classroom before starting a quiz.')
        return redirect('learning:student_classrooms')

    selected_educator_id = selected_enrollment.Classroom.CreatedBy_id

    concept = get_object_or_404(Concept, pk=concept_id)
    has_published_questions = Question.objects.filter(
        Concept=concept,
        IsPublished=True,
        Lecture__UploadedBy_id=selected_educator_id,
    ).exists()
    if not has_published_questions:
        return HttpResponseForbidden('This concept is not available for your selected classroom.')

    attempt = QuizAttempt.objects.create(User=request.user)
    # Store quiz context in session since QuizAttempt has no concept/difficulty columns (per ERD)
    request.session[f'quiz_{attempt.AttemptID}_concept_id'] = concept_id
    request.session[f'quiz_{attempt.AttemptID}_difficulty'] = 'Medium'
    request.session[f'quiz_{attempt.AttemptID}_selected_educator_id'] = selected_educator_id
    request.session[f'quiz_{attempt.AttemptID}_selected_classroom_id'] = selected_enrollment.Classroom_id
    return redirect('learning:student_quiz', attempt_id=attempt.AttemptID)


@login_required
def student_quiz(request, attempt_id):
    attempt = get_object_or_404(QuizAttempt, pk=attempt_id, User=request.user)
    feedback_key = f'feedback_pending_{attempt.AttemptID}'
    micro_lesson_key = f'micro_lesson_{attempt.AttemptID}'
    micro_lesson = request.session.get(micro_lesson_key, '')

    concept_id = request.session.get(f'quiz_{attempt.AttemptID}_concept_id')
    current_difficulty = request.session.get(f'quiz_{attempt.AttemptID}_difficulty', 'Medium')
    selected_educator_id = request.session.get(f'quiz_{attempt.AttemptID}_selected_educator_id')

    if request.session.get(feedback_key):
        source_id = request.session.get(f'feedback_source_{attempt.AttemptID}')
        source_question = Question.objects.filter(pk=source_id).first()
        return render(
            request,
            'student_quiz.html',
            {
                'attempt': attempt,
                'completed': False,
                'feedback_pending': True,
                'source_question': source_question,
                'micro_lesson': micro_lesson,
            },
        )

    question = None

    answered_ids = attempt.responses.values_list('Question_id', flat=True)
    if question is None:
        question = (
            Question.objects.filter(
                Concept_id=concept_id,
                DifficultyLevel=current_difficulty,
                IsPublished=True,
                Lecture__UploadedBy_id=selected_educator_id,
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
    selected_educator_id = request.session.get(f'quiz_{attempt.AttemptID}_selected_educator_id')
    if question.Lecture.UploadedBy_id != selected_educator_id:
        return HttpResponseForbidden('This question is not available for your selected classroom.')

    if question.QuestionType == Question.TYPE_MCQ:
        submitted_norm = _normalize_mcq_answer(submitted_answer)
        correct_norm = _normalize_mcq_answer(question.CorrectAnswerText)
        is_correct = submitted_norm == correct_norm or _mcq_correct_by_option_text(
            question.QuestionText,
            submitted_answer,
            correct_norm,
        )
    else:
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
        request.session[f'micro_lesson_{attempt.AttemptID}'] = _get_or_generate_micro_lesson(
            question=question,
            student_answer=submitted_answer,
        )
        request.session[f'feedback_pending_{attempt.AttemptID}'] = True
        request.session[f'feedback_source_{attempt.AttemptID}'] = question.QuestionID
        _trace_ai(f'✅ Wrong Answer Flow Ready: attempt_id={attempt.AttemptID}, waiting for next question action')
    else:
        _trace_ai(f'✅ Correct Answer Recorded: attempt_id={attempt.AttemptID}, moving to next question')

    return redirect('learning:student_quiz', attempt_id=attempt.AttemptID)


@login_required
def continue_to_next_question(request, attempt_id):
    if not request.user.is_student():
        return HttpResponseForbidden('Only students can continue quizzes.')

    attempt = get_object_or_404(QuizAttempt, pk=attempt_id, User=request.user)
    if request.method != 'POST':
        return redirect('learning:student_quiz', attempt_id=attempt.AttemptID)

    request.session.pop(f'feedback_pending_{attempt.AttemptID}', None)
    request.session.pop(f'feedback_source_{attempt.AttemptID}', None)
    request.session.pop(f'micro_lesson_{attempt.AttemptID}', None)

    _trace_ai(f'📚 Micro-Lesson Cleared: attempt_id={attempt.AttemptID}, moving to next question')

    return redirect('learning:student_quiz', attempt_id=attempt.AttemptID)


@login_required
def join_classroom(request):
    if not request.user.is_student():
        return HttpResponseForbidden('Only students can join classes.')
    if request.method != 'POST':
        return redirect('learning:student_classrooms')

    form = JoinClassroomForm(request.POST)
    if not form.is_valid():
        messages.error(request, next(iter(form.errors.get('join_code', [])), 'Invalid class code.'))
        return redirect('learning:student_classrooms')

    code = form.cleaned_data['join_code']
    classroom = Classroom.objects.filter(JoinCode=code).select_related('CreatedBy').first()
    if classroom is None:
        messages.error(request, 'Invalid class code. Check the code and try again.')
        return redirect('learning:student_classrooms')

    if not classroom.is_joinable():
        messages.error(request, 'This class code is inactive or expired. Ask your educator for a new code.')
        return redirect('learning:student_classrooms')

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
    request.session['student_active_classroom_id'] = classroom.ClassroomID
    return redirect('learning:student_classrooms')


@login_required
def select_classroom(request, classroom_id):
    if not request.user.is_student():
        return HttpResponseForbidden('Only students can access this page.')
    if request.method != 'POST':
        return redirect('learning:student_classrooms')

    enrollment = ClassroomEnrollment.objects.filter(
        Student=request.user,
        Classroom_id=classroom_id,
        IsActive=True,
        Classroom__IsActive=True,
    ).select_related('Classroom').first()
    if enrollment is None:
        return HttpResponseForbidden('You are not enrolled in that class.')

    request.session['student_active_classroom_id'] = enrollment.Classroom_id
    messages.success(request, f'Class "{enrollment.Classroom.Name}" selected.')
    return redirect('learning:student_classrooms')


@login_required
def create_classroom(request):
    if not request.user.is_educator():
        return HttpResponseForbidden('Only educators can create classes.')
    if request.method != 'POST':
        return redirect('content:educator_classrooms')

    form = CreateClassroomForm(request.POST)
    if not form.is_valid():
        messages.error(request, next(iter(form.errors.get('name', [])), 'Unable to create class.'))
        return redirect('content:educator_classrooms')

    classroom = Classroom(
        Name=form.cleaned_data['name'],
        CreatedBy=request.user,
    )
    classroom.generate_join_code(length=form.cleaned_data['code_length'])
    classroom.save()
    messages.success(request, f'Class "{classroom.Name}" created. Join code: {classroom.JoinCode}')
    return redirect('content:educator_classrooms')


@login_required
def regenerate_classroom_code(request, classroom_id):
    if not request.user.is_educator():
        return HttpResponseForbidden('Only educators can manage class codes.')
    if request.method != 'POST':
        return redirect('content:educator_classrooms')

    classroom = get_object_or_404(Classroom, pk=classroom_id, CreatedBy=request.user)
    classroom.regenerate_join_code()
    classroom.save(update_fields=['JoinCode', 'UpdatedAt'])
    messages.success(request, f'New join code for "{classroom.Name}": {classroom.JoinCode}')
    return redirect('content:educator_classrooms')


@login_required
def set_classroom_status(request, classroom_id):
    if not request.user.is_educator():
        return HttpResponseForbidden('Only educators can manage classes.')
    if request.method != 'POST':
        return redirect('content:educator_classrooms')

    classroom = get_object_or_404(Classroom, pk=classroom_id, CreatedBy=request.user)
    desired = request.POST.get('is_active', '1') == '1'
    classroom.IsActive = desired
    classroom.save(update_fields=['IsActive', 'UpdatedAt'])
    if desired:
        messages.success(request, f'Class "{classroom.Name}" is now active.')
    else:
        if request.session.get('educator_active_classroom_id') == classroom.ClassroomID:
            request.session.pop('educator_active_classroom_id', None)
        messages.success(request, f'Class "{classroom.Name}" has been deactivated.')
    return redirect('content:educator_classrooms')
