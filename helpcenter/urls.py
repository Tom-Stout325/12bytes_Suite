from django.urls import path
from . import views

app_name = "helpcenter"

urlpatterns = [
    path("", views.HelpHomeView.as_view(), name="home"),
    path("setup/", views.SetupHelpView.as_view(), name="setup"),
    path("financials/", views.FinancialsHelpView.as_view(), name="financials"),
]
