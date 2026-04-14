from django import forms
from learning_app.models import Question

ALLOWED_EXTENSIONS = ('.pdf', '.docx', '.doc', '.txt', '.mp3', '.wav', '.m4a', '.mp4', '.mov')


class LectureUploadForm(forms.Form):
    SUMMARY_MODE_CHOICES = (
        ('brief', 'Brief'),
        ('standard', 'Standard'),
        ('detailed', 'Detailed'),
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
        help_text='Choose how much detail the AI should include in the summary.',
    )

    def clean_UploadFile(self):
        file = self.cleaned_data.get('UploadFile')
        if file:
            import os
            ext = os.path.splitext(file.name)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                raise forms.ValidationError(
                    f'Unsupported file type "{ext}". Please upload a supported document, audio, or video file.'
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
