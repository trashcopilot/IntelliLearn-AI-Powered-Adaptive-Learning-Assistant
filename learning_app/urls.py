from django.urls import path

from .views import (
    continue_to_next_question,
    create_classroom,
    join_classroom,
    regenerate_classroom_code,
    select_classroom,
    set_classroom_status,
    start_quiz,
    student_classrooms,
    student_dashboard,
    student_quiz,
    submit_answer,
)

app_name = 'learning'

urlpatterns = [
    path('classrooms/', student_classrooms, name='student_classrooms'),
    path('dashboard/', student_dashboard, name='student_dashboard'),
    path('classrooms/join/', join_classroom, name='join_classroom'),
    path('classrooms/create/', create_classroom, name='create_classroom'),
    path('classrooms/<int:classroom_id>/regenerate-code/', regenerate_classroom_code, name='regenerate_classroom_code'),
    path('classrooms/<int:classroom_id>/set-status/', set_classroom_status, name='set_classroom_status'),
    path('classrooms/<int:classroom_id>/select/', select_classroom, name='select_classroom'),
    path('quiz/start/<int:concept_id>/', start_quiz, name='start_quiz'),
    path('quiz/<int:attempt_id>/', student_quiz, name='student_quiz'),
    path('quiz/<int:attempt_id>/submit/', submit_answer, name='submit_answer'),
    path('quiz/<int:attempt_id>/next/', continue_to_next_question, name='continue_to_next_question'),
]
