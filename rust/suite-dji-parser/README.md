# Suite DJI parser proof of concept

This is a local-only Rust proof of concept for parsing original DJI
`DJIFlightRecord*.txt` files with `dji-log-parser` 0.5.7. DJI log versions 13
and newer are encrypted. For those files, the program builds the DJI keychain
request, retrieves the keychain using the configured developer key, and parses
the normalized frames in memory. Keychains are not persisted.

The program writes one summary JSON document to standard output. It does not
output the developer key, keychains, DJI API responses, raw records, or full
telemetry.

## Build

Install the Rust toolchain, then run:

```bash
cd rust/suite-dji-parser
cargo build --release
```

## Run

Set `DJI_API_KEY` in the process environment without placing it in this
repository. One shell-safe approach is to prompt for it for the current shell:

```bash
read -s DJI_API_KEY
export DJI_API_KEY
./target/release/suite-dji-parser /absolute/path/to/DJIFlightRecord.txt
unset DJI_API_KEY
```

For an unencrypted log, `DJI_API_KEY` is not required.

## Batch analysis

Batch mode processes every regular `.txt` file whose name contains
`FlightRecord` in the selected directory. It writes one CSV row per file and
continues after sanitized per-file failures. Concurrency defaults to `1` so
encrypted logs do not generate parallel DJI keychain API requests.

From the Suite project root:

```bash
cargo run --release --manifest-path rust/suite-dji-parser/Cargo.toml -- \
  --batch "/absolute/path/to/flight-record-directory" \
  --output "/absolute/path/to/dji-summary.csv"
```

To explicitly configure the bounded concurrency limit, add
`--concurrency N`. Persistent keychain caching is not implemented.

Errors are sanitized, written to standard error, and result in a non-zero exit
status. In single-file mode, the input file path remains the only command-line
argument.

## Output field semantics

- `success`: `true` after the file and all required encrypted records parse.
- `parser_version`: pinned `dji-log-parser` crate version.
- `log_version`: DJI flight-record format version from the file prefix.
- `encrypted`: `true` for DJI log format version 13 or newer.
- `aircraft_model`: parser product name, or `null` when its numeric code is not
  mapped by `dji-log-parser`. A user-assigned aircraft name is not substituted.
- `aircraft_model_code`: numeric DJI product type from the Details header. If
  that header contains `None`, the first non-`None` OSD drone-type code is used.
  This is parser metadata, not a stable physical-aircraft or model identifier;
  DJI app/firmware generations can emit different unmapped codes.
- `aircraft_serial`: the single distinct variable-length ComponentSerial type 2
  value found in decoded records, or `null`. `dji-log-parser` calls type 2
  `Aircraft`; DJI's reference library calls it `FlightController` and uses it
  as the full component serial corresponding to header `aircraftSN`. Multiple
  different type 2 values are treated as ambiguous and produce `null`.
- `aircraft_serial_header`: the DJI Details `aircraftSN` value. This field is
  exactly 16 bytes in log versions 6 and newer and may therefore be truncated.
- `battery_serial`: the single distinct variable-length ComponentSerial type 4
  value found in decoded records, or `null`. Multiple different battery values
  are ambiguous for this single-value output and produce `null`.
- `battery_serial_header`: the DJI Details `batterySN` value. Like the aircraft
  header serial, it is exactly 16 bytes in log versions 6 and newer and may be
  truncated.
- `start_time`: DJI Details `start_time`, stored as Unix epoch milliseconds and
  rendered as UTC RFC 3339. The program performs no controller-local timezone
  conversion or location-based timezone inference.
- `duration_seconds`: DJI Details `total_time`, stored as milliseconds. This is
  the flight-record duration associated with motor-on playback, not a computed
  airborne-only duration.
- `airborne_duration_seconds`: sum of consecutive OSD `fly_time` intervals whose
  starting frame reports motors on and aircraft not on ground.
- `takeoff_latitude` / `takeoff_longitude`: WGS84 position of the first decoded
  frame that reports motor on, aircraft not on ground, GPS in use, GPS level at
  least 3, and valid coordinates. If no such frame exists, both are `null`.
- `maximum_altitude_relative_m`: maximum of the Details maximum height and
  decoded OSD relative height. Both represent meters above the takeoff/home
  reference, not altitude above mean sea level.
- `maximum_distance_from_home_m`: maximum spherical great-circle distance in
  meters between each valid aircraft coordinate and the recorded home point in
  that same frame.
- `total_distance_m`: DJI Details total distance. DJI stores this header float
  in kilometers; the program multiplies it by 1000 to emit meters.
- `maximum_satellites`: maximum OSD GPS satellite count across decoded frames.
- `minimum_airborne_satellites` (and the compatibility field
  `minimum_satellites_airborne`): minimum OSD satellite count while motors are
  on and the aircraft is not on the ground. `minimum_gps_signal_level_airborne`
  is DJI's raw ordinal GPS level, not a percentage.
- Battery takeoff/landing percent, voltage, and remaining capacity use the first
  and last available motor-on airborne `FrameBattery` samples. Voltage is volts
  and capacity is mAh. Maximum temperature is emitted in degrees Celsius.
- Cell voltage extrema are exact decoded per-cell values in volts; estimated
  per-cell values are not used. Battery cycle count and the raw, unit-unknown
  battery life value (`battery_life_raw`, also retained as the compatibility
  field `battery_life_value`) are emitted only when decoded records contain one
  unambiguous value. The raw life value is not labeled as a percentage.
- Maximum horizontal speed is meters/second from DJI Details and airborne OSD
  vector magnitudes. Maximum vertical speed is absolute airborne OSD speed in
  meters/second.
- Signal-loss count is the number of airborne OSD `rc_outcontrol` intervals that
  last at least one second according to OSD `fly_time`.
- `rc_serial` and `camera_serial` use unambiguous variable-length
  ComponentSerial values. Header serials are not substituted.
- `flight_modes` are distinct observed airborne OSD modes.
- `warnings`, `serious_warnings`, and `tips` are collected directly from their
  corresponding AppWarn, AppSeriousWarn, and AppTip records. Repeated values
  are removed while first-seen order is retained. Each category is limited to
  25 messages and each message to 300 Unicode characters. `messages` remains a
  compatibility alias for `tips`. These are never converted into user-authored
  notes, and full telemetry is never emitted.
- DJI's official standardization interface defines a categorical wind warning,
  but dji-log-parser 0.5.7 does not expose that field. This CLI does not infer a
  category from localized warning text.
