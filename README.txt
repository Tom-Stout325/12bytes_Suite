Travel Expense PDF Fix Patch

Unzip this over the MoneyPro project root.

Files updated:
- reports/views.py
  - Adds business/company_profile/company_name to TravelExpenseSummaryView context.
  - Changes TravelExpenseSummaryPDFView to use the existing reports.pdf.render_pdf_from_template helper.
  - Keeps preview inline when ?preview=1 is present; otherwise downloads.
  - Removes direct WeasyPrint rendering from this view.

- reports/templates/reports/travel_expense_summary.html
  - Enables the Download PDF button.
  - Keeps PDF Preview opening in a new tab.

- reports/templates/reports/pdf/travel_expense_summary_pdf.html
  - Replaces the blank 0-byte template with a complete landscape PDF template.
  - Includes company logo, title Travel Expenses - selected year, summary cards, detail table, totals, averages, and footer.

After unzipping:
python manage.py check
git add reports/views.py reports/templates/reports/travel_expense_summary.html reports/templates/reports/pdf/travel_expense_summary_pdf.html
git commit -m "Fix travel expense PDF report"
git push
