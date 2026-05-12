from django.urls import path

from .views import dashboard_chart_data, dashboard_home, reseed_defaults, rebuild_defaults

app_name = "dashboard"

urlpatterns = [
    path("", dashboard_home, name="home"),
    path("", dashboard_home, name="dashboard_home"),
    path("chart-data/", dashboard_chart_data, name="chart_data"),
    path("seed-defaults/", reseed_defaults, name="seed_defaults"),
    path("rebuild-defaults/", rebuild_defaults, name="rebuild_defaults"),
]
