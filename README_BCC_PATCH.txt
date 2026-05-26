Suites BCC Email Patch
======================

This patch adds app-wide BCC support for outbound Suites emails.

Default BCC recipient:
  tom@airborne-images.net

Configurable environment variable:
  EMAIL_BCC=tom@airborne-images.net

You can also add multiple BCC recipients as a comma-separated list:
  EMAIL_BCC=tom@airborne-images.net,another@example.com

Files changed:
  core/emailing.py
  project/settings/base.py
  project/settings/prod.py
  contractor/services/w9_email.py
  contractor/views.py
  invoices/services.py
  accounts/services/invitations.py

Covered email flows found in the project zip:
  - Invoice emails with PDF attachment
  - Business/user invitation emails
  - Contractor W-9 request emails
  - Contractor 1099 Copy B emails

After applying the patch, optionally set the Heroku config explicitly:
  heroku config:set -a suites -- EMAIL_BCC=tom@airborne-images.net

Then restart the app:
  heroku restart -a suites
