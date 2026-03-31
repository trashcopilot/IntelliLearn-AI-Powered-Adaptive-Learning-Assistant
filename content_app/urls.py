from django.urls import path

from .views import download_summary, educator_dashboard, publish_quiz, verify_summary

app_name = 'content'

urlpatterns = [
    path('educator/', educator_dashboard, name='educator_dashboard'),
    path('summary/<int:summary_id>/verify/', verify_summary, name='verify_summary'),
    path('summary/<int:summary_id>/download/', download_summary, name='download_summary'),
    path('lecture/<int:lecture_id>/publish-quiz/', publish_quiz, name='publish_quiz'),
]
