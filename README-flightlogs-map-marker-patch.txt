FlightLogs Map Marker Patch
===========================

Problem fixed:
- /flightlogs/map/ was crashing with:
  ValueError: Missing staticfiles manifest entry for 'images/drone-marker.png'

What this patch includes:
- static/images/drone-marker.png
- flightlogs/templates/flightlogs/map.html

Apply:
1. Unzip this patch over the root of the Suites project.
2. Commit and deploy.
3. Make sure collectstatic runs during deploy.

Heroku commands after deploying, if needed:
  heroku run -a suites -- python manage.py collectstatic --noinput
  heroku restart -a suites

Notes:
- The template already points to {% static 'images/drone-marker.png' %}.
- This patch provides that static file so ManifestStaticFilesStorage can resolve it during render.
- The same marker is shared across all businesses.
