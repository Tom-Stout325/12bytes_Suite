from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
import re
from .models import CompanyProfile

from allauth.account.forms import SignupForm


US_STATE_CHOICES = [
    ("", "—"),
    ("AL", "Alabama"),
    ("AK", "Alaska"),
    ("AZ", "Arizona"),
    ("AR", "Arkansas"),
    ("CA", "California"),
    ("CO", "Colorado"),
    ("CT", "Connecticut"),
    ("DE", "Delaware"),
    ("DC", "District of Columbia"),
    ("FL", "Florida"),
    ("GA", "Georgia"),
    ("HI", "Hawaii"),
    ("ID", "Idaho"),
    ("IL", "Illinois"),
    ("IN", "Indiana"),
    ("IA", "Iowa"),
    ("KS", "Kansas"),
    ("KY", "Kentucky"),
    ("LA", "Louisiana"),
    ("ME", "Maine"),
    ("MD", "Maryland"),
    ("MA", "Massachusetts"),
    ("MI", "Michigan"),
    ("MN", "Minnesota"),
    ("MS", "Mississippi"),
    ("MO", "Missouri"),
    ("MT", "Montana"),
    ("NE", "Nebraska"),
    ("NV", "Nevada"),
    ("NH", "New Hampshire"),
    ("NJ", "New Jersey"),
    ("NM", "New Mexico"),
    ("NY", "New York"),
    ("NC", "North Carolina"),
    ("ND", "North Dakota"),
    ("OH", "Ohio"),
    ("OK", "Oklahoma"),
    ("OR", "Oregon"),
    ("PA", "Pennsylvania"),
    ("RI", "Rhode Island"),
    ("SC", "South Carolina"),
    ("SD", "South Dakota"),
    ("TN", "Tennessee"),
    ("TX", "Texas"),
    ("UT", "Utah"),
    ("VT", "Vermont"),
    ("VA", "Virginia"),
    ("WA", "Washington"),
    ("WV", "West Virginia"),
    ("WI", "Wisconsin"),
    ("WY", "Wyoming"),
]



class CompanyProfileForm(forms.ModelForm):
    class Meta:
        model = CompanyProfile
        fields = [
            "company_name", "legal_name", "ein",
            "phone", "billing_email",
            "address_line1", "address_line2", "city", "state", "postal_code", "country",
            "timezone", "currency",
            "logo",
        ]
        widgets = {
            "company_name": forms.TextInput(attrs={"placeholder": "Business name"}),
            "legal_name": forms.TextInput(attrs={"placeholder": "Legal name (optional)"}),
            "ein": forms.TextInput(attrs={"placeholder": "12-3456789", "inputmode": "numeric"}),

            "phone": forms.TextInput(attrs={"placeholder": "Business phone (optional)"}),
            "billing_email": forms.EmailInput(attrs={"placeholder": "Billing email (optional)"}),

            "address_line1": forms.TextInput(attrs={"placeholder": "Address line 1"}),
            "address_line2": forms.TextInput(attrs={"placeholder": "Address line 2 (optional)"}),
            "city": forms.TextInput(attrs={"placeholder": "City"}),

            "postal_code": forms.TextInput(attrs={"placeholder": "ZIP / Postal code"}),
            "country": forms.TextInput(attrs={"placeholder": "Country"}),

            "timezone": forms.TextInput(attrs={"placeholder": "America/Indiana/Indianapolis"}),
            "currency": forms.TextInput(attrs={"placeholder": "USD"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # State dropdown (stores USPS 2-letter codes)
        self.fields["state"].widget = forms.Select(choices=US_STATE_CHOICES)
        self.fields["state"].help_text = "USPS 2-letter code (recommended)."

        # Default country to US when blank
        current_country = (getattr(self.instance, "country", "") or "").strip()
        if not self.initial.get("country") and not current_country:
            self.initial["country"] = "US"
        elif not self.initial.get("country") and current_country:
            self.initial["country"] = current_country

        # Display phone as (xxx)xxx-xxxx if stored as digits-only
        phone = (getattr(self.instance, "phone", "") or "").strip()
        if phone.isdigit() and len(phone) == 10:
            self.initial["phone"] = f"({phone[:3]}){phone[3:6]}-{phone[6:]}"

    def clean_phone(self):
        raw = (self.cleaned_data.get("phone") or "").strip()
        if not raw:
            return ""

        digits = re.sub(r"\D+", "", raw)

        # Handle US leading country code
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]

        if len(digits) != 10:
            raise forms.ValidationError("Enter a valid 10-digit US phone number.")

        return digits

    def clean_state(self):
        state = (self.cleaned_data.get("state") or "").strip().upper()
        if not state:
            return ""
        if len(state) != 2:
            raise forms.ValidationError("Select a valid US state.")
        return state

    def clean_ein(self):
        raw = (self.cleaned_data.get("ein") or "").strip()
        if not raw:
            return ""

        digits = "".join(ch for ch in raw if ch.isdigit())
        if len(digits) != 9:
            raise ValidationError("EIN must have 9 digits (example: 12-3456789).")

        return f"{digits[:2]}-{digits[2:]}"



User = get_user_model()


class UserInfoForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email"]
        widgets = {
            "username": forms.TextInput(attrs={"placeholder": "Username"}),
            "first_name": forms.TextInput(attrs={"placeholder": "First name"}),
            "last_name": forms.TextInput(attrs={"placeholder": "Last name"}),
            "email": forms.EmailInput(attrs={"placeholder": "Email"}),
        }

    def clean_username(self):
        username = (self.cleaned_data.get("username") or "").strip()

        if not username:
            raise forms.ValidationError("Username is required.")

        # Default Django User.username max_length is 150
        if len(username) > 150:
            raise forms.ValidationError("Username is too long.")

        qs = User.objects.filter(username__iexact=username)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)

        if qs.exists():
            raise forms.ValidationError("That username is already taken.")

        return username

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if not email:
            raise forms.ValidationError("Email is required.")

        qs = User.objects.filter(email__iexact=email)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)

        if qs.exists():
            raise forms.ValidationError("That email is already in use.")
        return email





class InviteSignupForm(SignupForm):
    def __init__(self, *args, **kwargs):
        self.request = kwargs.get("request")
        super().__init__(*args, **kwargs)

        invited_email = ((self.request.session.get("invite_email") if self.request else "") or "").strip()
        if invited_email:
            self.fields["email"].initial = invited_email
            self.fields["email"].widget.attrs["readonly"] = "readonly"

    def clean_email(self):
        invited_email = ((self.request.session.get("invite_email") if self.request else "") or "").strip().lower()
        if invited_email:
            return invited_email
        return super().clean_email()

    def save(self, request):
        user = super().save(request)

        invited_email = (request.session.get("invite_email") or "").strip().lower()
        if invited_email:
            current_email = (user.email or "").strip().lower()
            if current_email != invited_email:
                user.email = invited_email
                user.save(update_fields=["email"])

        return user

from core.models import BusinessEmailSettings


class BusinessEmailSettingsForm(forms.ModelForm):
    class Meta:
        model = BusinessEmailSettings
        fields = [
            "display_name",
            "from_name",
            "from_email",
            "reply_to_email",
            "invoice_cc_email",
            "payment_questions_email",
            "email_signature",
            "send_mode",
        ]
        widgets = {
            "display_name": forms.TextInput(attrs={"placeholder": "Business name shown to recipients"}),
            "from_name": forms.TextInput(attrs={"placeholder": "Sender name"}),
            "from_email": forms.EmailInput(attrs={"readonly": "readonly"}),
            "reply_to_email": forms.EmailInput(attrs={"placeholder": "Where replies should go"}),
            "invoice_cc_email": forms.EmailInput(attrs={"placeholder": "Optional internal copy"}),
            "payment_questions_email": forms.EmailInput(attrs={"placeholder": "Optional payment contact"}),
            "email_signature": forms.Textarea(attrs={"rows": 4, "placeholder": "Thank you for your business."}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["from_email"].disabled = True
        self.fields["send_mode"].help_text = "Use MoneyPro default for V1. Custom domain is reserved for a later phase."

    def clean(self):
        cleaned = super().clean()
        if self.instance and self.instance.pk:
            cleaned["from_email"] = self.instance.from_email
        return cleaned
