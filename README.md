# Vehicle current-year odometer patch

This patch updates the Vehicle Dashboard annual snapshot logic so the current year does not show `Missing ending odometer`.

## What changes

- Adds `VehicleYear.effective_odometer_end`.
- Uses the saved `odometer_end` when present.
- For the current year only, uses the latest mileage log `end` value as a provisional ending odometer.
- Prior years still show `Missing ending odometer` when no annual ending odometer has been entered.
- The dashboard yearly summary now displays the effective/provisional ending odometer.

## Install

From the project root:

```bash
unzip vehicle_current_year_odometer_patch.zip -d /path/to/project
python manage.py check
```

No migration is required.
