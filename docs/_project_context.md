docs/_project_context.md

GitHub Repo:        https://github.com/Tom-Stout325/MoneyPro.git
Local Database:     db.sqlite3
Base Template:      templates/index.html
Base CSS:           static/css/main.css
Auth CSS:           static/css/auth.css



heroku config:set USE_S3=True -a moneypro
heroku config:set AWS_ACCESS_KEY_ID=AKIAZFJGRP44UFCZ3UTQ -a moneypro
heroku config:set AWS_SECRET_ACCESS_KEY=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-a moneypro
heroku config:set AWS_STORAGE_BUCKET_NAME=moneypro-prod-media -a moneypro
heroku config:set AWS_S3_REGION_NAME=us-east-2 -a moneypro



Heroku App:

Auth
    allauth is the system of record for auth flows (login/register/password reset).
    Email is the unique identifier.
  
Data Scoping
    All Models are to be business-owned unless specifically stated differently


Deployment
    keeping SQLite only for local dev convenience and will switch to Postgres in prod via DATABASE_URL.
    WhiteNoise will handle static files in production

Project root is _MONEYPRO/

Django Project Name:    project

project/settings/ has:
    base.py, 
    dev.py, 
    prod.py

Django apps:
    accounts (auth)
    dashboard (main entry + visualized data)
    ledger (core accounting features)
    reports (report generators)
    vehicles (vehicle info)


Project-level templates:
    templates/index.html (base)
    templates/home.html
    templates/partials/


Mobile-first Bootstrap 5 UI everywhere
The “dashboard” app owns the home page and navigation, with the other apps linked in
templates/index.html is the global base layout with shared navbar, messages, etc.


Currency: USD $ always with cents
Date display: MM/DD/YYYY
Templates: per-app + shared partials
Tenancy: user-owned for now; may evolve to account/org


Tell me what you see, what assumptions you’d make, and what you still need to know