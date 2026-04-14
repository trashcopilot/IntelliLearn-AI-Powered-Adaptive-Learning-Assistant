from django.contrib import admin

from .models import Classroom, ClassroomEnrollment, Concept, Question, QuestionResponse, QuizAttempt


@admin.register(Concept)
class ConceptAdmin(admin.ModelAdmin):
    list_display = ('ConceptID', 'ConceptName')
    search_fields = ('ConceptName',)


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ('QuestionID', 'QuestionType', 'Concept', 'Lecture', 'DifficultyLevel', 'IsPublished')
    list_filter = ('QuestionType', 'DifficultyLevel', 'IsPublished')


@admin.register(QuizAttempt)
class QuizAttemptAdmin(admin.ModelAdmin):
    list_display = ('AttemptID', 'User', 'StartTime', 'TotalScore')


@admin.register(QuestionResponse)
class QuestionResponseAdmin(admin.ModelAdmin):
    list_display = ('ResponseID', 'Attempt', 'Question', 'IsCorrect', 'TimeTaken')
    list_filter = ('IsCorrect',)


@admin.register(Classroom)
class ClassroomAdmin(admin.ModelAdmin):
    list_display = ('ClassroomID', 'Name', 'JoinCode', 'CreatedBy', 'IsActive', 'ExpiresAt', 'CreatedAt')
    list_filter = ('IsActive',)
    search_fields = ('Name', 'JoinCode', 'CreatedBy__username')


@admin.register(ClassroomEnrollment)
class ClassroomEnrollmentAdmin(admin.ModelAdmin):
    list_display = ('EnrollmentID', 'Classroom', 'Student', 'IsActive', 'JoinedAt')
    list_filter = ('IsActive',)
    search_fields = ('Classroom__Name', 'Student__username')
