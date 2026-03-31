from django.urls import path

from .views import UserLoginView, dashboard, logout_view, signup_view

app_name = 'users'

urlpatterns = [
    path('', UserLoginView.as_view(), name='login'),
    path('signup/student/', signup_view, {'role_name': 'student'}, name='signup_student'),
    path('signup/educator/', signup_view, {'role_name': 'educator'}, name='signup_educator'),
    path('dashboard/', dashboard, name='dashboard'),
    path('logout/', logout_view, name='logout'),
]
