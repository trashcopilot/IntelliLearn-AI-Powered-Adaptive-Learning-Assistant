from django.urls import path

from .views import student_progress_api

app_name = 'analytics'

urlpatterns = [
    path('student/progress/', student_progress_api, name='student_progress_api'),
]
