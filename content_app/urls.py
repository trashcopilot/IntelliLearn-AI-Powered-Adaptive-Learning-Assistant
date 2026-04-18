from django.urls import path

from .views import (
    ai_processing_status,
    delete_summary,
    delete_archived_summary,
    download_summary,
    edit_lecture_question,
    educator_classrooms,
    educator_dashboard,
    edit_summary,
    manage_lecture_questions,
    publish_quiz,
    restore_summary,
    select_educator_classroom,
    verify_summary,
)

app_name = 'content'

urlpatterns = [
    path('educator/classrooms/', educator_classrooms, name='educator_classrooms'),
    path('educator/', educator_dashboard, name='educator_dashboard'),
    path('educator/classrooms/<int:classroom_id>/select/', select_educator_classroom, name='select_educator_classroom'),
    path('educator/ai-status/', ai_processing_status, name='ai_processing_status'),
    path('summary/<int:summary_id>/edit/', edit_summary, name='edit_summary'),
    path('summary/<int:summary_id>/delete/', delete_summary, name='delete_summary'),
    path('summary/<int:summary_id>/delete-archived/', delete_archived_summary, name='delete_archived_summary'),
    path('summary/<int:summary_id>/restore/', restore_summary, name='restore_summary'),
    path('summary/<int:summary_id>/verify/', verify_summary, name='verify_summary'),
    path('summary/<int:summary_id>/download/', download_summary, name='download_summary'),
    path('lecture/<int:lecture_id>/publish-quiz/', publish_quiz, name='publish_quiz'),
    path('lecture/<int:lecture_id>/questions/', manage_lecture_questions, name='manage_lecture_questions'),
    path('questions/<int:question_id>/edit/', edit_lecture_question, name='edit_lecture_question'),
]
