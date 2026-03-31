from django.contrib import admin

from .models import Concept, Question, QuestionResponse, QuizAttempt


@admin.register(Concept)
class ConceptAdmin(admin.ModelAdmin):
    list_display = ('ConceptID', 'ConceptName')
    search_fields = ('ConceptName',)


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ('QuestionID', 'Concept', 'Lecture', 'DifficultyLevel', 'IsPublished')
    list_filter = ('DifficultyLevel', 'IsPublished')


@admin.register(QuizAttempt)
class QuizAttemptAdmin(admin.ModelAdmin):
    list_display = ('AttemptID', 'User', 'StartTime', 'TotalScore')


@admin.register(QuestionResponse)
class QuestionResponseAdmin(admin.ModelAdmin):
    list_display = ('ResponseID', 'Attempt', 'Question', 'IsCorrect', 'TimeTaken')
    list_filter = ('IsCorrect',)
