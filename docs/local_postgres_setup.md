# Local Postgres setup for MoneyPro

This patch switches local development to Postgres by loading `.env.local`
after `.env`. The included `.env.local` points Django at:

```bash
postgresql://postgres@127.0.0.1:5432/moneypro_dev
```

If your local Postgres uses a different username, password, host, port, or
database name, edit `.env.local`.

## Create the database

If `moneypro_dev` does not exist yet:

```bash
createdb moneypro_dev
```

If your Postgres install uses a different superuser, create the DB with that
user instead.

## Run migrations

```bash
python manage.py migrate
```

## Create a superuser if needed

```bash
python manage.py createsuperuser
```

## Verify the database connection

```bash
python manage.py shell -c "from django.db import connection; print(connection.settings_dict['ENGINE']); print(connection.settings_dict['NAME'])"
```

Expected engine:

```text
django.db.backends.postgresql
```
