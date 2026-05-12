from django.urls import path
from .views import (
    business_backup_admin,
    business_backup_all_xlsx,
    business_backup_cleanup,
    business_backup_send_to_s3,
    business_backup_table_csv,
    dashboard_chart_data,
    dashboard_home,
    rebuild_defaults,
    reseed_defaults,
)

app_name = "dashboard"

urlpatterns = [
    path("", dashboard_home, name="home"),
    path("chart-data/", dashboard_chart_data, name="chart_data"),
    path("backups/", business_backup_admin, name="business_backup_admin"),
    path("backups/download-all/", business_backup_all_xlsx, name="business_backup_all_xlsx"),
    path("backups/send-to-s3/", business_backup_send_to_s3, name="business_backup_send_to_s3"),
    path("backups/cleanup/", business_backup_cleanup, name="business_backup_cleanup"),
    path("backups/<slug:table_slug>/csv/", business_backup_table_csv, name="business_backup_table_csv"),
    path("seed-defaults/", reseed_defaults, name="seed_defaults"),
    path("rebuild-defaults/", rebuild_defaults, name="rebuild_defaults"),
]
