from django import forms

ALLOWED_EXTENSIONS = ('.pdf', '.docx', '.doc', '.txt', '.mp3', '.wav', '.m4a', '.mp4', '.mov')


class LectureUploadForm(forms.Form):
    Title = forms.CharField(max_length=255, widget=forms.TextInput(attrs={'class': 'form-control'}))
    UploadFile = forms.FileField(widget=forms.ClearableFileInput(attrs={
        'class': 'form-control',
        'accept': '.pdf,.docx,.doc,.txt,.mp3,.wav,.m4a,.mp4,.mov',
    }))

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
