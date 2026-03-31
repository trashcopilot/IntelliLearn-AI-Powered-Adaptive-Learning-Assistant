from django.contrib.auth.models import AbstractUser
from django.db import models


# 1. User Management Cluster
class Role(models.Model):
    RoleID = models.AutoField(primary_key=True)
    RoleName = models.CharField(max_length=50)  # e.g., 'Educator', 'Student'

    def __str__(self):
        return self.RoleName


class User(AbstractUser):
    # AbstractUser provides: username, email, password (hashed), date_joined, etc.
    # Extended here to add the Role FK from the ERD.
    Role = models.ForeignKey(Role, on_delete=models.RESTRICT, null=True, blank=True, related_name='users')
    CreatedAt = models.DateTimeField(auto_now_add=True)

    def is_educator(self):
        try:
            return self.Role.RoleName == 'Educator'
        except Exception:
            return False

    def is_admin(self):
        try:
            return self.is_staff or self.is_superuser or self.Role.RoleName == 'Admin'
        except Exception:
            return self.is_staff or self.is_superuser

    def is_student(self):
        try:
            return self.Role.RoleName == 'Student'
        except Exception:
            return False
