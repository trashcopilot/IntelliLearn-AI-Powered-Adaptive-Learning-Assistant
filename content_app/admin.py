from django.contrib import admin

from .models import LectureMaterial, Summary, SummaryValidation


@admin.register(LectureMaterial)
class LectureMaterialAdmin(admin.ModelAdmin):
    list_display = ('Title', 'UploadedBy', 'UploadedAt')
    search_fields = ('Title',)


@admin.register(Summary)
class SummaryAdmin(admin.ModelAdmin):
    list_display = ('Lecture', 'IsVerified', 'CreatedAt')
    list_filter = ('IsVerified',)


@admin.register(SummaryValidation)
class SummaryValidationAdmin(admin.ModelAdmin):
    list_display = ('Summary', 'Lecture', 'QualityScore', 'QualityStatus', 'IsVerified', 'VerifiedBy', 'CreatedAt')
    list_filter = ('IsVerified', 'QualityStatus')
