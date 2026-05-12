from __future__ import annotations

import os

from django import forms
from django.contrib.auth import get_user_model

from .models import PilotProfile, Training


class PilotProfileForm(forms.ModelForm):
    class Meta:
        model = PilotProfile
        fields = ["license_number", "license_date", "license_image"]
        widgets = {
            "license_number": forms.TextInput(attrs={"class": "form-control"}),
            "license_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "license_image": forms.ClearableFileInput(attrs={"class": "form-control", "accept": ".pdf,.png,.jpg,.jpeg"}),
        }

    def clean_license_image(self):
        file = self.cleaned_data.get("license_image")
        if not file:
            return file
        ext = os.path.splitext(file.name)[1].lower()
        if ext not in {".pdf", ".png", ".jpg", ".jpeg"}:
            raise forms.ValidationError("File type must be PDF, PNG, JPG, or JPEG.")
        return file


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = get_user_model()
        fields = ["first_name", "last_name", "email"]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
        }


class TrainingForm(forms.ModelForm):
    class Meta:
        model = Training
        fields = ["title", "date_completed", "required", "certificate", "notes"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "date_completed": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "required": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "certificate": forms.ClearableFileInput(attrs={"class": "form-control", "accept": ".pdf,.png,.jpg,.jpeg"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def clean_certificate(self):
        file = self.cleaned_data.get("certificate")
        if not file:
            return file
        ext = os.path.splitext(file.name)[1].lower()
        if ext not in {".pdf", ".png", ".jpg", ".jpeg"}:
            raise forms.ValidationError("Certificate must be a PDF, PNG, JPG, or JPEG.")
        return file
