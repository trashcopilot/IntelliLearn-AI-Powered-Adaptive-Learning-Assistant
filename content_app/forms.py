from django import forms

from .models import LectureMaterial

ALLOWED_EXTENSIONS = ('.pdf', '.docx', '.doc', '.txt', '.mp3', '.wav', '.m4a', '.mp4', '.mov')


class LectureUploadForm(forms.ModelForm):
    class Meta:
        model = LectureMaterial
        fields = ['Title', 'FilePath']
        widgets = {
            'Title': forms.TextInput(attrs={'class': 'form-control'}),
            'FilePath': forms.ClearableFileInput(attrs={
                'class': 'form-control',
                'accept': '.pdf,.docx,.doc,.txt,.mp3,.wav,.m4a,.mp4,.mov',
            }),
        }

    def clean_FilePath(self):
        file = self.cleaned_data.get('FilePath')
        if file:
            import os
            ext = os.path.splitext(file.name)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                raise forms.ValidationError(
                    f'Unsupported file type "{ext}". Please upload a supported document, audio, or video file.'
                )
        return file
