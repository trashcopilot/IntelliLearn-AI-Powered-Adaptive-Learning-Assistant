from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.urls import reverse_lazy

from .forms import LoginForm, SignUpForm
from .models import Role


class UserLoginView(LoginView):
    template_name = 'login.html'
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def get_success_url(self):
        return reverse_lazy('users:dashboard')


@login_required
def dashboard(request):
    if request.user.is_admin():
        return redirect('/admin/')
    if request.user.is_educator():
        return redirect('content:educator_dashboard')
    return redirect('learning:student_dashboard')


def logout_view(request):
    logout(request)
    return redirect('users:login')


def signup_view(request, role_name):
    allowed_roles = {'student': 'Student', 'educator': 'Educator'}
    if role_name not in allowed_roles:
        return redirect('users:login')

    if request.user.is_authenticated:
        return redirect('users:dashboard')

    if request.method == 'POST':
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            role, _ = Role.objects.get_or_create(RoleName=allowed_roles[role_name])
            user.Role = role
            user.save()
            login(request, user)
            messages.success(request, f'Account created as {role.RoleName}.')
            return redirect('users:dashboard')
    else:
        form = SignUpForm()

    return render(
        request,
        'signup.html',
        {
            'form': form,
            'role_name': role_name,
            'role_display': allowed_roles[role_name],
        },
    )
