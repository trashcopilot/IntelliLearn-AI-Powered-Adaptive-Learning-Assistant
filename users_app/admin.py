from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Role, User


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ('RoleID', 'RoleName')


@admin.register(User)
class IntelliLearnUserAdmin(UserAdmin):
    list_display = ('username', 'email', 'Role', 'is_staff', 'is_active')
    list_filter = ('Role', 'is_staff', 'is_active')
    fieldsets = UserAdmin.fieldsets + (
        ('IntelliLearn Fields', {'fields': ('Role',)}),
    )
