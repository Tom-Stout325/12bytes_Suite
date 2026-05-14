Suite FlightLog Bulk Import Patch

What this changes:
- Updates flightlogs/views.py upload_flightlog_csv().
- Replaces per-row duplicate database lookups with one in-memory duplicate signature set.
- Replaces per-row FlightLog.objects.create() calls with bulk_create() batches of 500.
- Keeps exact duplicate protection, including duplicates already in the database and duplicates repeated inside the uploaded CSV.
- Requires no migration.

Install:
1. Unzip this patch over the root of your Suite project.
2. Commit and deploy:
   git add flightlogs/views.py
   git commit -m "Speed up flight log CSV imports"
   git push heroku main
   # or your normal GitHub/Heroku deploy workflow
3. Restart if needed:
   heroku restart -a suites

After deploy, retry the same full AirData CSV upload.
