from django import forms
from django.conf import settings

from .models import UploadedFile


class UploadForm(forms.ModelForm):
    class Meta:
        model = UploadedFile
        fields = ["file"]
        labels = {"file": "Escolha um arquivo"}

    def clean_file(self):
        uploaded = self.cleaned_data["file"]
        if uploaded.size > settings.MAX_UPLOAD_BYTES:
            raise forms.ValidationError(
                f"O arquivo excede o limite de {settings.MAX_UPLOAD_MB} MB."
            )
        return uploaded
