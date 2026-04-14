from django.conf import settings
from django.db import models
from django.utils import timezone

import secrets


JOIN_CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


def _generate_join_code(length=6):
    return ''.join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(length))


# 3. Adaptive Content Structuring Cluster
class Concept(models.Model):
    ConceptID = models.AutoField(primary_key=True)
    ConceptName = models.CharField(max_length=255)
    Description = models.TextField(blank=True, null=True)
    # micro_lesson is an implementation field supporting the Micro-Lesson panel in the UI
    micro_lesson = models.TextField(blank=True)

    def __str__(self):
        return self.ConceptName


class Question(models.Model):
    TYPE_MCQ = 'mcq'
    TYPE_CONSTRUCTED = 'constructed'
    QUESTION_TYPE_CHOICES = [
        (TYPE_MCQ, 'MCQ'),
        (TYPE_CONSTRUCTED, 'Constructed-response'),
    ]

    QuestionID = models.AutoField(primary_key=True)
    Lecture = models.ForeignKey(
        'content_app.LectureMaterial', on_delete=models.CASCADE, related_name='questions'
    )
    Concept = models.ForeignKey(
        Concept, on_delete=models.SET_NULL, null=True, related_name='questions'
    )
    QuestionText = models.TextField()
    QuestionType = models.CharField(max_length=16, choices=QUESTION_TYPE_CHOICES, default=TYPE_CONSTRUCTED)
    DifficultyLevel = models.CharField(max_length=50)  # 'Easy', 'Medium', 'Hard'
    CorrectAnswerText = models.TextField()
    IsPublished = models.BooleanField(default=False)
    IsAIGenerated = models.BooleanField(default=True)

    def __str__(self):
        return self.QuestionText


# 4. Granular Performance Tracking Cluster
class QuizAttempt(models.Model):
    AttemptID = models.AutoField(primary_key=True)
    User = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='quiz_attempts')
    StartTime = models.DateTimeField(auto_now_add=True)
    EndTime = models.DateTimeField(null=True, blank=True)
    TotalScore = models.IntegerField(default=0)

    def __str__(self):
        return f'Attempt {self.AttemptID} by {self.User.username}'


class QuestionResponse(models.Model):
    ResponseID = models.AutoField(primary_key=True)
    Attempt = models.ForeignKey(QuizAttempt, on_delete=models.CASCADE, related_name='responses')
    Question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='responses')
    StudentAnswerText = models.TextField()
    IsCorrect = models.BooleanField()
    TimeTaken = models.IntegerField()  # Recorded in seconds for analytics

    def __str__(self):
        return f'Response to {self.Question.QuestionID} - Correct: {self.IsCorrect}'


class Classroom(models.Model):
    ClassroomID = models.AutoField(primary_key=True)
    Name = models.CharField(max_length=255)
    JoinCode = models.CharField(max_length=8, unique=True, db_index=True)
    CreatedBy = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='classrooms')
    IsActive = models.BooleanField(default=True)
    ExpiresAt = models.DateTimeField(null=True, blank=True)
    CreatedAt = models.DateTimeField(auto_now_add=True)
    UpdatedAt = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.Name} ({self.JoinCode})'

    def is_joinable(self):
        return self.IsActive and (self.ExpiresAt is None or self.ExpiresAt > timezone.now())

    def _set_unique_join_code(self, length=6):
        # Use a short bounded retry loop to avoid collisions while keeping join codes human-friendly.
        for _ in range(20):
            candidate = _generate_join_code(length)
            if not Classroom.objects.filter(JoinCode=candidate).exclude(pk=self.pk).exists():
                self.JoinCode = candidate
                return
        raise ValueError('Unable to generate a unique class join code. Please try again.')

    def generate_join_code(self, length=6):
        self._set_unique_join_code(length=length)

    def regenerate_join_code(self):
        current_length = len(self.JoinCode) if self.JoinCode else 6
        length = min(max(current_length, 6), 8)
        self.generate_join_code(length=length)

    def save(self, *args, **kwargs):
        if not self.JoinCode:
            self.generate_join_code(length=6)
        super().save(*args, **kwargs)


class ClassroomEnrollment(models.Model):
    EnrollmentID = models.AutoField(primary_key=True)
    Classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name='enrollments')
    Student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='classroom_enrollments')
    IsActive = models.BooleanField(default=True)
    JoinedAt = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['Classroom', 'Student'], name='uniq_classroom_student_enrollment'),
        ]

    def __str__(self):
        return f'{self.Student.username} in {self.Classroom.Name}'
