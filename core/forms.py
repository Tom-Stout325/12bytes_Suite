# core/forms.py
from __future__ import annotations

from django import forms
from core.models import Business


class BusinessOnboardingForm(forms.ModelForm):
    class Meta:
        model = Business
        fields = ["name"]
