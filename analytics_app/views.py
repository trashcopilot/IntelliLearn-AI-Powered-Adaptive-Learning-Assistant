from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils.timezone import now

from learning_app.models import QuizAttempt


@login_required
def student_progress_api(request):
    attempts = QuizAttempt.objects.filter(User=request.user).order_by('-StartTime')[:30]
    count = attempts.count()

    payload = {
        'generated_at': now().isoformat(),
        'total_attempts': count,
        'average_score': round(sum(a.TotalScore for a in attempts) / count, 2) if count else 0,
        'history': [
            {
                'attempt_id': attempt.AttemptID,
                'score': attempt.TotalScore,
                'started_at': attempt.StartTime.isoformat(),
            }
            for attempt in attempts
        ],
    }
    return JsonResponse(payload)
