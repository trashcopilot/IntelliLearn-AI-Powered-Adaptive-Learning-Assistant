from django.urls import path

from .views import retry_similar_question, start_quiz, student_dashboard, student_quiz, submit_answer

app_name = 'learning'

urlpatterns = [
    path('dashboard/', student_dashboard, name='student_dashboard'),
    path('quiz/start/<int:concept_id>/', start_quiz, name='start_quiz'),
    path('quiz/<int:attempt_id>/', student_quiz, name='student_quiz'),
    path('quiz/<int:attempt_id>/submit/', submit_answer, name='submit_answer'),
    path('quiz/<int:attempt_id>/retry/', retry_similar_question, name='retry_similar_question'),
]
