Recurring Expenses Month/Year Processing Patch

Files included:
- ledger/models.py
- ledger/views.py
- ledger/templates/ledger/recurring_expenses/recurring_expense_list.html

What this patch does:
- Adds a Month and Year selector to the Recurring Expenses list page.
- Allows manual processing for any selected month/year.
- Creates transactions dated to each recurring expense's configured day_of_month in the selected month.
- If day_of_month is not valid for that month (example: 31 in February), it uses the last valid day of the month.
- Blocks duplicates per recurring expense for the selected calendar month.
- Prevents catch-up processing for older months from moving last_run_date or next_run_date backward.

No database migration is required.

After deploying, run:
python manage.py check

On Heroku:
heroku run -a suites -- python manage.py check
