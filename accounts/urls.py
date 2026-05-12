from django.urls import path

from .views import OnboardingView, SettingsView, invite_start

app_name = "accounts"

urlpatterns = [
    path("invite/<str:token>/", invite_start, name="invite_start"),
    path("onboarding/", OnboardingView.as_view(), name="onboarding"),
    path("settings/", SettingsView.as_view(), name="settings"),
]
