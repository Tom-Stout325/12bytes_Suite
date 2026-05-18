Suite invoice PDF patch

What it fixes:
1. Invoice logo rendering in PDFs by embedding the company logo as a base64 data URI through Django storage.
   This works with media/company_logos/... whether media is local or S3-backed.
2. Invoice PDF footer placement by using fixed-position PDF footer CSS so it stays at the bottom of the page.
3. Adds a Regenerate PDF button on the invoice detail page.
4. Adds POST route: invoices:<pk>/pdf/regenerate/ using URL name invoices:invoice_pdf_regenerate.

Install:
- Unzip this patch at the root of your Suite project so it overwrites the matching files.
- Commit the changes.
- Deploy.
- Open an existing invoice and click Regenerate PDF to replace the old frozen PDF.

No database migration is required.
