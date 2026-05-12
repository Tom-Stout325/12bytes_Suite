from __future__ import annotations

from collections import defaultdict

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import FieldError
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render

from flightlogs.models import FlightLog

from .forms import PilotProfileForm, TrainingForm, UserProfileForm
from .models import PilotProfile, Training


def _require_business(request):
    business = getattr(request, "business", None)
    if business is None:
        messages.error(request, "Select an active business before using pilot profiles.")
    return business


def _get_or_create_pilot_profile(request):
    business = _require_business(request)
    if business is None:
        return None
    profile, _created = PilotProfile.objects.get_or_create(
        user=request.user,
        defaults={"business": business},
    )
    if profile.business_id != business.id:
        messages.error(request, "Your pilot profile belongs to a different business. Contact an administrator.")
        return None
    return profile


def _duration_to_seconds(value) -> int:
    if not value:
        return 0
    try:
        return int(value.total_seconds())
    except AttributeError:
        return int(value or 0)


def _build_drone_usage_stats(logs):
    stats = defaultdict(lambda: {"drone_name": "", "drone_serial": "", "flights": 0, "total_seconds": 0})
    for log in logs.only("drone_name", "drone_serial", "air_time"):
        drone_name = (log.drone_name or "").strip()
        drone_serial = (log.drone_serial or "").strip()
        key = (drone_name.lower(), drone_serial.lower())
        row = stats[key]
        row["drone_name"] = drone_name
        row["drone_serial"] = drone_serial
        row["flights"] += 1
        row["total_seconds"] += _duration_to_seconds(log.air_time)
    return sorted(stats.values(), key=lambda item: (item["flights"], item["total_seconds"], item["drone_name"]), reverse=True)


def _flightlogs_for_profile(profile: PilotProfile):
    full_name = profile.user.get_full_name().strip()
    qs = FlightLog.objects.filter(business=profile.business)
    if full_name:
        try:
            return qs.filter(pilot_in_command__iexact=full_name)
        except FieldError:
            return FlightLog.objects.none()
    return qs.filter(pilot_in_command__iexact=profile.user.get_username())


@login_required
def profile(request):
    pilot_profile = _get_or_create_pilot_profile(request)
    if pilot_profile is None:
        return redirect("dashboard:dashboard")

    profile_form = PilotProfileForm(instance=pilot_profile)
    user_form = UserProfileForm(instance=request.user)
    training_form = TrainingForm()

    if request.method == "POST":
        if "update_profile" in request.POST:
            profile_form = PilotProfileForm(request.POST, request.FILES, instance=pilot_profile)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, "Pilot profile updated.")
                return redirect("pilot:profile")
            messages.error(request, "Please correct the pilot profile errors below.")
        elif "update_user" in request.POST:
            user_form = UserProfileForm(request.POST, instance=request.user)
            if user_form.is_valid():
                user_form.save()
                messages.success(request, "User information updated.")
                return redirect("pilot:profile")
            messages.error(request, "Please correct the user information errors below.")
        elif "add_training" in request.POST:
            training_form = TrainingForm(request.POST, request.FILES)
            if training_form.is_valid():
                training = training_form.save(commit=False)
                training.business = pilot_profile.business
                training.pilot = pilot_profile
                training.save()
                messages.success(request, "Training record added.")
                return redirect("pilot:profile")
            messages.error(request, "Please correct the training errors below.")

    logs = _flightlogs_for_profile(pilot_profile)
    drone_stats = _build_drone_usage_stats(logs)

    totals = {
        "flight_count": logs.count(),
        "total_distance_ft": None,
        "total_air_time": None,
        "total_media_count": None,
    }
    try:
        totals["total_distance_ft"] = logs.aggregate(v=Coalesce(Sum("max_distance_ft"), 0.0))["v"]
    except FieldError:
        pass
    try:
        totals["total_air_time"] = logs.aggregate(v=Coalesce(Sum("air_time"), 0))["v"]
    except FieldError:
        pass
    try:
        totals["total_media_count"] = logs.aggregate(v=Coalesce(Sum("photos"), 0) + Coalesce(Sum("videos"), 0))["v"]
    except Exception:
        pass

    highest_altitude_flight = fastest_speed_flight = longest_flight = None
    try:
        highest_altitude_flight = logs.order_by("-max_altitude_ft").first()
    except FieldError:
        pass
    try:
        fastest_speed_flight = logs.order_by("-max_speed_mph").first()
    except FieldError:
        pass
    try:
        longest_flight = logs.order_by("-max_distance_ft").first()
    except FieldError:
        pass

    selected_year = request.GET.get("year", "").strip()
    trainings = pilot_profile.trainings.filter(business=pilot_profile.business)
    if selected_year.isdigit():
        trainings = trainings.filter(date_completed__year=int(selected_year))
    years = (
        pilot_profile.trainings.filter(business=pilot_profile.business)
        .dates("date_completed", "year", order="DESC")
    )

    context = {
        "profile": pilot_profile,
        "form": profile_form,
        "user_form": user_form,
        "training_form": training_form,
        "trainings": trainings,
        "years": [d.year for d in years],
        "selected_year": selected_year,
        "logs": logs,
        "drone_stats": drone_stats,
        "totals": totals,
        "highest_altitude_flight": highest_altitude_flight,
        "fastest_speed_flight": fastest_speed_flight,
        "longest_flight": longest_flight,
    }
    return render(request, "pilot/profile.html", context)


@login_required
def edit_profile(request):
    pilot_profile = _get_or_create_pilot_profile(request)
    if pilot_profile is None:
        return redirect("dashboard:dashboard")
    if request.method == "POST":
        form = PilotProfileForm(request.POST, request.FILES, instance=pilot_profile)
        if form.is_valid():
            form.save()
            messages.success(request, "Pilot profile updated.")
            return redirect("pilot:profile")
        messages.error(request, "Please correct the errors below.")
    else:
        form = PilotProfileForm(instance=pilot_profile)
    return render(request, "pilot/edit_profile.html", {"form": form, "profile": pilot_profile})


@login_required
def training_create(request):
    pilot_profile = _get_or_create_pilot_profile(request)
    if pilot_profile is None:
        return redirect("dashboard:dashboard")
    if request.method == "POST":
        form = TrainingForm(request.POST, request.FILES)
        if form.is_valid():
            training = form.save(commit=False)
            training.business = pilot_profile.business
            training.pilot = pilot_profile
            training.save()
            messages.success(request, "Training record added.")
            return redirect("pilot:profile")
        messages.error(request, "Please correct the errors below.")
    else:
        form = TrainingForm()
    return render(request, "pilot/training_form.html", {"form": form, "profile": pilot_profile, "mode": "create"})


@login_required
def training_edit(request, pk: int):
    pilot_profile = _get_or_create_pilot_profile(request)
    if pilot_profile is None:
        return redirect("dashboard:dashboard")
    training = get_object_or_404(Training, pk=pk, business=pilot_profile.business, pilot=pilot_profile)
    if request.method == "POST":
        form = TrainingForm(request.POST, request.FILES, instance=training)
        if form.is_valid():
            updated = form.save(commit=False)
            updated.business = pilot_profile.business
            updated.pilot = pilot_profile
            updated.save()
            messages.success(request, "Training record updated.")
            return redirect("pilot:profile")
        messages.error(request, "Please correct the errors below.")
    else:
        form = TrainingForm(instance=training)
    return render(request, "pilot/training_form.html", {"form": form, "profile": pilot_profile, "training": training, "mode": "edit"})


@login_required
def training_delete(request, pk: int):
    pilot_profile = _get_or_create_pilot_profile(request)
    if pilot_profile is None:
        return redirect("dashboard:dashboard")
    training = get_object_or_404(Training, pk=pk, business=pilot_profile.business, pilot=pilot_profile)
    if request.method == "POST":
        training.delete()
        messages.success(request, "Training record deleted.")
        return redirect("pilot:profile")
    return render(request, "pilot/training_confirm_delete.html", {"training": training})
