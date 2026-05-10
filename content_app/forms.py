import os

from django import forms
from learning_app.models import Question

ALLOWED_EXTENSIONS = ('.pdf', '.docx', '.doc', '.txt', '.mp3', '.wav', '.m4a', '.mp4', '.mov')
MAX_UPLOAD_SIZE_MB = int(os.getenv('MAX_UPLOAD_SIZE_MB', '48'))


class LectureUploadForm(forms.Form):
    SUMMARY_MODE_CHOICES = (
        ('brief', 'Brief (quick revision)'),
        ('standard', 'Standard (balanced study notes)'),
        ('detailed', 'Detailed (deep analytic summary)'),
    )

    Title = forms.CharField(max_length=255, widget=forms.TextInput(attrs={'class': 'form-control'}))
    UploadFile = forms.FileField(widget=forms.ClearableFileInput(attrs={
        'class': 'form-control',
        'accept': '.pdf,.docx,.doc,.txt,.mp3,.wav,.m4a,.mp4,.mov',
    }))
    SummaryMode = forms.ChoiceField(
        choices=SUMMARY_MODE_CHOICES,
        initial='detailed',
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text='Brief: concise exam snapshot. Standard: structured study notes. Detailed: full analytic breakdown.',
    )

    def clean_UploadFile(self):
        file = self.cleaned_data.get('UploadFile')
        if file:
            ext = os.path.splitext(file.name)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                raise forms.ValidationError(
                    f'Unsupported file type "{ext}". Please upload a supported document, audio, or video file.'
                )
            max_bytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024
            if file.size > max_bytes:
                raise forms.ValidationError(
                    f'File is too large. Maximum allowed size is {MAX_UPLOAD_SIZE_MB} MB.'
                )
        return file


class SummaryEditForm(forms.Form):
    SummaryText = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 12}),
        help_text='Edit the summary to correct misinformation or add missing details.',
    )


class QuestionEditForm(forms.ModelForm):
    class Meta:
        model = Question
        fields = ['QuestionText', 'QuestionType', 'DifficultyLevel', 'CorrectAnswerText', 'IsPublished']
        widgets = {
            'QuestionText': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'QuestionType': forms.Select(attrs={'class': 'form-select'}),
            'DifficultyLevel': forms.Select(
                attrs={'class': 'form-select'},
                choices=[('Easy', 'Easy'), ('Medium', 'Medium'), ('Hard', 'Hard')],
            ),
            'CorrectAnswerText': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'IsPublished': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
