# SendGrid setup for MoneyPro production

## Heroku config vars
Use placeholders and replace the values before running:

```bash
heroku config:set -a moneypro \
  EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend \
  EMAIL_HOST=smtp.sendgrid.net \
  EMAIL_PORT=587 \
  EMAIL_HOST_USER=apikey \
  EMAIL_HOST_PASSWORD=YOUR_SENDGRID_API_KEY \
  SENDGRID_API_KEY=YOUR_SENDGRID_API_KEY \
  EMAIL_USE_TLS=True \
  EMAIL_USE_SSL=False \
  DEFAULT_FROM_EMAIL=noreply@moneypro.12bytes.net \
  REPLY_TO_EMAIL=noreply@moneypro.12bytes.net \
  BUSINESS_EMAIL_PLATFORM_DOMAIN=moneypro.12bytes.net \
  BUSINESS_EMAIL_LOCALPART=noreply
```

## Domain authentication
Authenticate the subdomain `moneypro.12bytes.net` inside SendGrid and add the DNS records they give you at your DNS host.

## Notes
- Invitations now send through Django mail like every other email path.
- Business-specific reply-to addresses still come from `BusinessEmailSettings.reply_to_email` when a business is known.
- Platform-level emails such as invitations use `REPLY_TO_EMAIL`.
