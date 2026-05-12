from django.urls import path

from . import views

app_name = "pilot"

urlpatterns = [
    path("", views.profile, name="profile"),
    path("profile/", views.profile, name="profile_legacy"),
    path("profile/edit/", views.edit_profile, name="edit_profile"),
    path("training/add/", views.training_create, name="training_create"),
    path("training/<int:pk>/edit/", views.training_edit, name="training_edit"),
    path("training/<int:pk>/delete/", views.training_delete, name="training_delete"),
]
