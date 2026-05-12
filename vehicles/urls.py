
from django.urls import path

from vehicles import views
from vehicles import views_report

app_name = "vehicles"

urlpatterns = [
    path("", views.VehiclesHomeView.as_view(), name="home"),
    path("list/", views.VehicleListView.as_view(), name="vehicle_list"),
    path("add/", views.VehicleCreateView.as_view(), name="vehicle_add"),
    path("<int:pk>/", views.VehicleDetailView.as_view(), name="vehicle_detail"),
    path("<int:pk>/edit/", views.VehicleUpdateView.as_view(), name="vehicle_edit"),
    path("<int:pk>/delete/", views.VehicleDeleteView.as_view(), name="vehicle_delete"),
    path("<int:pk>/archive/", views.vehicle_archive, name="vehicle_archive"),
    path("<int:pk>/unarchive/", views.vehicle_unarchive, name="vehicle_unarchive"),
    path("years/", views.VehicleYearListView.as_view(), name="vehicle_year_list"),
    path("years/add/", views.VehicleYearCreateView.as_view(), name="vehicle_year_add"),
    path("years/<int:pk>/edit/", views.VehicleYearUpdateView.as_view(), name="vehicle_year_edit"),
    path("years/<int:pk>/delete/", views.VehicleYearDeleteView.as_view(), name="vehicle_year_delete"),
    path("miles/", views.VehicleMilesListView.as_view(), name="vehicle_miles_list"),
    path("miles/add/", views.VehicleMilesCreateView.as_view(), name="vehicle_miles_add"),
    path("miles/quick-add/", views.quick_mileage_add, name="vehicle_miles_quick_add"),
    path("miles/<int:pk>/edit/", views.VehicleMilesUpdateView.as_view(), name="vehicle_miles_edit"),
    path("miles/<int:pk>/delete/", views.VehicleMilesDeleteView.as_view(), name="vehicle_miles_delete"),
    path("reports/mileage/", views_report.YearlyMileageReportView.as_view(), name="yearly_mileage_report"),
]
