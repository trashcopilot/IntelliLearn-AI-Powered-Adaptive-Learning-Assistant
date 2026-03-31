from django.conf import settings
from django.db import models


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
    QuestionID = models.AutoField(primary_key=True)
    Lecture = models.ForeignKey(
        'content_app.LectureMaterial', on_delete=models.CASCADE, related_name='questions'
    )
    Concept = models.ForeignKey(
        Concept, on_delete=models.SET_NULL, null=True, related_name='questions'
    )
    QuestionText = models.TextField()
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
