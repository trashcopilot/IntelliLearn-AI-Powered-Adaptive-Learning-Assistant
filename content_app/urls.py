from django.urls import path

from .views import (
    ai_processing_status,
    delete_summary,
    download_summary,
    educator_dashboard,
    edit_summary,
    publish_quiz,
    restore_summary,
    verify_summary,
)

app_name = 'content'

urlpatterns = [
    path('educator/', educator_dashboard, name='educator_dashboard'),
    path('educator/ai-status/', ai_processing_status, name='ai_processing_status'),
    path('summary/<int:summary_id>/edit/', edit_summary, name='edit_summary'),
    path('summary/<int:summary_id>/delete/', delete_summary, name='delete_summary'),
    path('summary/<int:summary_id>/restore/', restore_summary, name='restore_summary'),
    path('summary/<int:summary_id>/verify/', verify_summary, name='verify_summary'),
    path('summary/<int:summary_id>/download/', download_summary, name='download_summary'),
    path('lecture/<int:lecture_id>/publish-quiz/', publish_quiz, name='publish_quiz'),
]
