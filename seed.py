"""
Run with:  python seed.py
Creates both Role records and two default accounts:
  educator / educator123
  student  / student123
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'intellilearn_project.settings')
django.setup()

from users_app.models import Role, User

# --- Roles ---
educator_role, _ = Role.objects.get_or_create(RoleName='Educator')
student_role, _  = Role.objects.get_or_create(RoleName='Student')
print(f'Roles ready: {educator_role}, {student_role}')

# --- Educator account ---
if not User.objects.filter(username='educator').exists():
    u = User.objects.create_superuser(username='educator', email='educator@intellilearn.local', password='educator123')
    u.Role = educator_role
    u.save()
    print('Created educator (superuser) — username: educator / password: educator123')
else:
    print('educator account already exists.')

# --- Student account ---
if not User.objects.filter(username='student').exists():
    u = User.objects.create_user(username='student', email='student@intellilearn.local', password='student123')
    u.Role = student_role
    u.save()
    print('Created student account — username: student / password: student123')
else:
    print('student account already exists.')
