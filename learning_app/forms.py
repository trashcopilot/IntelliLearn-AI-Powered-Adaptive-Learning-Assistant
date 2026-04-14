from django import forms


class JoinClassroomForm(forms.Form):
    join_code = forms.CharField(
        label='Class Join Code',
        min_length=6,
        max_length=8,
        widget=forms.TextInput(
            attrs={
                'placeholder': 'Enter 6-8 character code',
                'autocomplete': 'off',
            }
        ),
    )

    def clean_join_code(self):
        code = ''.join((self.cleaned_data.get('join_code') or '').split()).upper()
        if len(code) < 6 or len(code) > 8 or not code.isalnum():
            raise forms.ValidationError('Enter a valid 6-8 character alphanumeric code.')
        return code


class CreateClassroomForm(forms.Form):
    name = forms.CharField(
        label='Class Name',
        max_length=255,
        widget=forms.TextInput(attrs={'placeholder': 'e.g. Biology 101 - Period 2'}),
    )
    code_length = forms.ChoiceField(
        label='Join Code Length',
        choices=[('6', '6 characters'), ('7', '7 characters'), ('8', '8 characters')],
        initial='6',
    )

    def clean_code_length(self):
        length = int(self.cleaned_data['code_length'])
        if length < 6 or length > 8:
            raise forms.ValidationError('Code length must be between 6 and 8.')
        return length
