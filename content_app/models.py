from django.conf import settings
from django.db import models


# 2. Content Management & Verification Cluster
class LectureMaterial(models.Model):
    LectureID = models.AutoField(primary_key=True)
    Title = models.CharField(max_length=255)
    OriginalFileName = models.CharField(max_length=255)
    MimeType = models.CharField(max_length=100, blank=True)
    FileSize = models.PositiveIntegerField(default=0)
    FileData = models.BinaryField()
    UploadedBy = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    UploadedAt = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.Title


class Summary(models.Model):
    SummaryID = models.AutoField(primary_key=True)
    Lecture = models.OneToOneField(LectureMaterial, on_delete=models.CASCADE, related_name='summary')
    SummaryText = models.TextField()
    IsVerified = models.BooleanField(default=False)  # Enforces Human-in-the-Loop validation
    VerifiedBy = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='verified_summaries',
    )
    CreatedAt = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'Summary for {self.Lecture.Title}'


class SummaryValidation(models.Model):
    """Stores the explicit validation record for a generated summary."""

    Summary = models.OneToOneField(
        Summary,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name='validation',
    )
    Lecture = models.ForeignKey(LectureMaterial, on_delete=models.CASCADE, related_name='summary_validations')
    SummaryTextSnapshot = models.TextField()
    IsVerified = models.BooleanField(default=False)
    VerifiedBy = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='summary_validation_actions',
    )
    CreatedAt = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        state = 'Verified' if self.IsVerified else 'Pending'
        return f'{state} validation for {self.Lecture.Title}'
