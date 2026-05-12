# Delete candidates after this patch

Do not delete these until you have clicked through the Suite pages and confirmed no templates still include them.

Likely safe after review:

- `templates/partials/navbar.html` — replaced by `templates/partials/sidebar.html` in the base layout.
- `templates/partials/offcanvas_more.html` — old navbar "More" drawer; not included by the new Suite shell.
- `static/css/mp_navbar.css` — old MoneyPro navbar styling; no longer loaded by `templates/index.html`.

Keep:

- `dashboard/templates/dashboard/moneypro_home.html` — this is the old MoneyPro dashboard, now available from the MoneyPro tile at `/dashboard/moneypro/`.
- `dashboard/templates/dashboard/home.html` — left in place for now as a fallback/legacy template. It can be removed later after you confirm no references remain in your customized branches.
