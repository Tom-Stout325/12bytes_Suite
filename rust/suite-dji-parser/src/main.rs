use dji_log_parser::frame::{records_to_frames, Frame};
use dji_log_parser::layout::details::ProductType;
use dji_log_parser::record::component_serial::ComponentType;
use dji_log_parser::record::osd::DroneType;
use dji_log_parser::record::Record;
use dji_log_parser::{DJILog, Error as DjiParserError};
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::ffi::OsString;
use std::fs;
use std::io::{self, Write};
use std::panic::{self, AssertUnwindSafe};
use std::path::PathBuf;
use std::process::ExitCode;
use std::thread;

const DJI_LOG_PARSER_VERSION: &str = "0.5.7";
const ENCRYPTED_VERSION_MINIMUM: u8 = 13;
const EARTH_RADIUS_M: f64 = 6_371_000.0;
const MAX_OPERATIONAL_MESSAGES_PER_KIND: usize = 25;
const MAX_OPERATIONAL_MESSAGE_CHARS: usize = 300;

#[derive(Clone, Debug, Serialize)]
struct Output {
    success: bool,
    parser_version: &'static str,
    log_version: u8,
    encrypted: bool,
    aircraft_model: Option<String>,
    aircraft_model_code: Option<u8>,
    aircraft_name: Option<String>,
    aircraft_serial: Option<String>,
    aircraft_serial_header: Option<String>,
    battery_serial: Option<String>,
    battery_serial_header: Option<String>,
    start_time: Option<String>,
    duration_seconds: Option<f64>,
    airborne_duration_seconds: Option<f64>,
    takeoff_latitude: Option<f64>,
    takeoff_longitude: Option<f64>,
    takeoff_altitude_asl_m: Option<f32>,
    maximum_altitude_relative_m: Option<f32>,
    maximum_distance_from_home_m: Option<f64>,
    total_distance_m: Option<f32>,
    maximum_satellites: Option<u8>,
    minimum_satellites_airborne: Option<u8>,
    minimum_airborne_satellites: Option<u8>,
    minimum_gps_signal_level_airborne: Option<u8>,
    maximum_gps_signal_level: Option<u8>,
    takeoff_battery_percent: Option<u8>,
    landing_battery_percent: Option<u8>,
    takeoff_battery_voltage_v: Option<f32>,
    landing_battery_voltage_v: Option<f32>,
    takeoff_battery_capacity_mah: Option<u32>,
    landing_battery_capacity_mah: Option<u32>,
    maximum_battery_temperature_c: Option<f32>,
    minimum_cell_voltage_v: Option<f32>,
    maximum_cell_voltage_v: Option<f32>,
    battery_cycle_count: Option<u16>,
    battery_life_value: Option<u8>,
    battery_life_raw: Option<u8>,
    maximum_horizontal_speed_m_s: Option<f32>,
    maximum_vertical_speed_m_s: Option<f32>,
    maximum_vertical_speed_mps: Option<f32>,
    signal_loss_events_over_one_second: Option<u32>,
    photo_count: Option<u32>,
    flight_modes: Vec<String>,
    rc_serial: Option<String>,
    camera_serial: Option<String>,
    warnings: Vec<String>,
    serious_warnings: Vec<String>,
    tips: Vec<String>,
    messages: Vec<String>,
}

#[derive(Clone, Debug, PartialEq)]
struct Failure {
    message: &'static str,
    diagnostic_code: Option<&'static str>,
    http_status: Option<u16>,
    api_status: Option<u16>,
    log_version: Option<u8>,
    encrypted: Option<bool>,
}

impl Failure {
    fn user_safe(message: &'static str) -> Self {
        Self {
            message,
            diagnostic_code: None,
            http_status: None,
            api_status: None,
            log_version: None,
            encrypted: None,
        }
    }

    fn coded(message: &'static str, diagnostic_code: &'static str) -> Self {
        let mut failure = Self::user_safe(message);
        failure.diagnostic_code = Some(diagnostic_code);
        failure
    }

    fn with_log_context(mut self, log_version: u8) -> Self {
        self.log_version = Some(log_version);
        self.encrypted = Some(log_version >= ENCRYPTED_VERSION_MINIMUM);
        self
    }
}

fn main() -> ExitCode {
    let args: Vec<OsString> = env::args_os().skip(1).collect();
    if args.first().and_then(|value| value.to_str()) == Some("--batch") {
        return batch_main(&args);
    }
    single_file_main(&args)
}

fn single_file_main(args: &[OsString]) -> ExitCode {
    let path = match single_input_path(args) {
        Ok(path) => path,
        Err(failure) => return fail(&failure),
    };
    let result = with_sanitized_panic_output(|| process_file(path));
    match result {
        Ok(output) => match serde_json::to_string(&output) {
            Ok(json) => {
                println!("{json}");
                ExitCode::SUCCESS
            }
            Err(_) => fail(&Failure::user_safe(
                "Could not serialize the parser result.",
            )),
        },
        Err(failure) => fail(&failure),
    }
}

fn process_file(path: PathBuf) -> Result<Output, Failure> {
    let bytes = fs::read(path)
        .map_err(|_| Failure::user_safe("Could not read the DJI flight-record file."))?;
    let parser = DJILog::from_bytes(bytes)
        .map_err(|_| Failure::user_safe("The file is not a supported DJI flight record."))?;
    panic::catch_unwind(AssertUnwindSafe(|| process_parsed_log(&parser)))
        .unwrap_or_else(|_| {
            Err(Failure::coded(
                "The DJI parser could not decode this flight record.",
                "DJI_PARSER_PANIC",
            ))
        })
        .map_err(|failure| failure.with_log_context(parser.version))
}

fn with_sanitized_panic_output<T>(operation: impl FnOnce() -> T) -> T {
    let previous_panic_hook = panic::take_hook();
    panic::set_hook(Box::new(|_| {}));
    let result = operation();
    panic::set_hook(previous_panic_hook);
    result
}

fn process_parsed_log(parser: &DJILog) -> Result<Output, Failure> {
    let encrypted = parser.version >= ENCRYPTED_VERSION_MINIMUM;
    let keychains = if encrypted {
        let api_key = env::var("DJI_API_KEY")
            .ok()
            .filter(|value| !value.trim().is_empty())
            .ok_or_else(|| {
                Failure::coded(
                    "DJI_API_KEY is required to decrypt this flight record.",
                    "DJI_API_KEY_MISSING",
                )
            })?;

        // Build the request explicitly so this proof of concept verifies the
        // v13+ key-storage parsing step before contacting DJI.
        let request = parser.keychains_request().map_err(|_| Failure {
            message: "Could not generate the DJI keychain request.",
            diagnostic_code: Some("DJI_UNSUPPORTED_DEPARTMENT_VERSION_REQUEST"),
            http_status: None,
            api_status: None,
            log_version: None,
            encrypted: None,
        })?;
        let fetched = request
            .fetch(&api_key, None)
            .map_err(keychain_fetch_failure)?;
        Some(fetched)
    } else {
        None
    };

    let records = parser
        .records(keychains)
        .map_err(|_| Failure::user_safe("Could not decrypt or parse the DJI flight record."))?;
    let component_serials = component_serials(&records);
    let record_metrics = record_metrics(&records, parser.version);
    let operational_messages = operational_messages(&records);
    let signal_loss_events_over_one_second = signal_loss_events_over_one_second(&records);
    let frames = records_to_frames(records, parser.details.clone());
    let minimum_airborne_satellites = minimum_satellites_airborne(&frames);
    let maximum_vertical_speed_mps =
        maximum_vertical_speed_m_s(parser.details.max_vertical_speed, &frames);

    let details = &parser.details;
    let (aircraft_model, aircraft_model_code) = aircraft_model_and_code(&parser, &frames);
    let (takeoff_latitude, takeoff_longitude) = takeoff_coordinates(&frames);

    Ok(Output {
        success: true,
        parser_version: DJI_LOG_PARSER_VERSION,
        log_version: parser.version,
        encrypted,
        aircraft_model,
        aircraft_model_code,
        aircraft_name: nonempty(&details.aircraft_name),
        aircraft_serial: component_serials.aircraft,
        aircraft_serial_header: nonempty(&details.aircraft_sn),
        battery_serial: component_serials.battery,
        battery_serial_header: nonempty(&details.battery_sn),
        start_time: (details.start_time.timestamp() > 0).then(|| details.start_time.to_rfc3339()),
        duration_seconds: nonnegative_f64(details.total_time),
        airborne_duration_seconds: airborne_duration_seconds(&frames),
        takeoff_latitude,
        takeoff_longitude,
        takeoff_altitude_asl_m: takeoff_altitude_asl_m(details.take_off_altitude, &frames),
        maximum_altitude_relative_m: maximum_relative_altitude(details.max_height, &frames),
        maximum_distance_from_home_m: maximum_distance_from_home(&frames),
        total_distance_m: total_distance_m(details.total_distance),
        maximum_satellites: maximum_satellites(&frames),
        minimum_satellites_airborne: minimum_airborne_satellites,
        minimum_airborne_satellites,
        minimum_gps_signal_level_airborne: minimum_gps_level_airborne(&frames),
        maximum_gps_signal_level: frames.iter().map(|frame| frame.osd.gps_level).max(),
        takeoff_battery_percent: airborne_battery_frames(&frames)
            .0
            .and_then(valid_battery_percent),
        landing_battery_percent: airborne_battery_frames(&frames)
            .1
            .and_then(valid_battery_percent),
        takeoff_battery_voltage_v: airborne_battery_frames(&frames)
            .0
            .and_then(valid_battery_voltage),
        landing_battery_voltage_v: airborne_battery_frames(&frames)
            .1
            .and_then(valid_battery_voltage),
        takeoff_battery_capacity_mah: airborne_battery_frames(&frames)
            .0
            .and_then(valid_battery_capacity),
        landing_battery_capacity_mah: airborne_battery_frames(&frames)
            .1
            .and_then(valid_battery_capacity),
        maximum_battery_temperature_c: record_metrics.maximum_battery_temperature_c,
        minimum_cell_voltage_v: record_metrics.minimum_cell_voltage_v,
        maximum_cell_voltage_v: record_metrics.maximum_cell_voltage_v,
        battery_cycle_count: record_metrics.battery_cycle_count,
        battery_life_value: record_metrics.battery_life_value,
        battery_life_raw: record_metrics.battery_life_value,
        maximum_horizontal_speed_m_s: maximum_horizontal_speed_m_s(
            details.max_horizontal_speed,
            &frames,
        ),
        maximum_vertical_speed_m_s: maximum_vertical_speed_mps,
        maximum_vertical_speed_mps,
        signal_loss_events_over_one_second,
        photo_count: u32::try_from(details.capture_num).ok(),
        flight_modes: flight_modes(&frames),
        rc_serial: component_serials.rc,
        camera_serial: component_serials.camera,
        warnings: operational_messages.warnings,
        serious_warnings: operational_messages.serious_warnings,
        tips: operational_messages.tips.clone(),
        // Retained for compatibility with the existing JSON contract.
        messages: operational_messages.tips,
    })
}

fn single_input_path(args: &[OsString]) -> Result<PathBuf, Failure> {
    let path = args
        .first()
        .ok_or_else(|| Failure::user_safe("Usage: suite-dji-parser <DJIFlightRecord.txt>"))?;
    if args.len() != 1 {
        return Err(Failure::user_safe(
            "Usage: suite-dji-parser <DJIFlightRecord.txt>",
        ));
    }
    Ok(PathBuf::from(path))
}

#[derive(Debug)]
struct BatchOptions {
    directory: PathBuf,
    output: PathBuf,
    concurrency: usize,
}

#[derive(Clone, Debug, PartialEq)]
struct BatchRow {
    filename: String,
    success: bool,
    log_version: Option<u8>,
    encrypted: Option<bool>,
    aircraft_model: Option<String>,
    aircraft_model_code: Option<u8>,
    aircraft_serial: Option<String>,
    aircraft_serial_header: Option<String>,
    battery_serial: Option<String>,
    battery_serial_header: Option<String>,
    start_time: Option<String>,
    duration_seconds: Option<f64>,
    takeoff_latitude: Option<f64>,
    takeoff_longitude: Option<f64>,
    maximum_altitude_relative_m: Option<f32>,
    maximum_distance_from_home_m: Option<f64>,
    total_distance_m: Option<f32>,
    maximum_satellites: Option<u8>,
    warning_count: usize,
    sanitized_error_code: Option<String>,
}

impl BatchRow {
    fn from_result(path: &std::path::Path, result: Result<Output, Failure>) -> Self {
        let filename = path
            .file_name()
            .map(|value| value.to_string_lossy().into_owned())
            .unwrap_or_default();
        match result {
            Ok(output) => Self {
                filename,
                success: true,
                log_version: Some(output.log_version),
                encrypted: Some(output.encrypted),
                aircraft_model: output.aircraft_model,
                aircraft_model_code: output.aircraft_model_code,
                aircraft_serial: output.aircraft_serial,
                aircraft_serial_header: output.aircraft_serial_header,
                battery_serial: output.battery_serial,
                battery_serial_header: output.battery_serial_header,
                start_time: output.start_time,
                duration_seconds: output.duration_seconds,
                takeoff_latitude: output.takeoff_latitude,
                takeoff_longitude: output.takeoff_longitude,
                maximum_altitude_relative_m: output.maximum_altitude_relative_m,
                maximum_distance_from_home_m: output.maximum_distance_from_home_m,
                total_distance_m: output.total_distance_m,
                maximum_satellites: output.maximum_satellites,
                warning_count: output.warnings.len(),
                sanitized_error_code: None,
            },
            Err(failure) => Self {
                filename,
                success: false,
                log_version: failure.log_version,
                encrypted: failure.encrypted,
                aircraft_model: None,
                aircraft_model_code: None,
                aircraft_serial: None,
                aircraft_serial_header: None,
                battery_serial: None,
                battery_serial_header: None,
                start_time: None,
                duration_seconds: None,
                takeoff_latitude: None,
                takeoff_longitude: None,
                maximum_altitude_relative_m: None,
                maximum_distance_from_home_m: None,
                total_distance_m: None,
                maximum_satellites: None,
                warning_count: 0,
                sanitized_error_code: Some(batch_error_code(&failure).to_owned()),
            },
        }
    }

    fn csv_values(&self) -> Vec<String> {
        vec![
            self.filename.clone(),
            self.success.to_string(),
            option_string(self.log_version),
            option_string(self.encrypted),
            self.aircraft_model.clone().unwrap_or_default(),
            option_string(self.aircraft_model_code),
            self.aircraft_serial.clone().unwrap_or_default(),
            self.aircraft_serial_header.clone().unwrap_or_default(),
            self.battery_serial.clone().unwrap_or_default(),
            self.battery_serial_header.clone().unwrap_or_default(),
            self.start_time.clone().unwrap_or_default(),
            option_string(self.duration_seconds),
            option_string(self.takeoff_latitude),
            option_string(self.takeoff_longitude),
            option_string(self.maximum_altitude_relative_m),
            option_string(self.maximum_distance_from_home_m),
            option_string(self.total_distance_m),
            option_string(self.maximum_satellites),
            self.warning_count.to_string(),
            self.sanitized_error_code.clone().unwrap_or_default(),
        ]
    }
}

fn batch_error_code(failure: &Failure) -> &'static str {
    match failure.diagnostic_code {
        Some("DJI_HTTP_STATUS_FAILURE" | "DJI_NETWORK_TLS_FAILURE") => "DJI_KEYCHAIN_UNAVAILABLE",
        Some("DJI_MALFORMED_RESPONSE") => "DJI_KEYCHAIN_RESPONSE_INVALID",
        Some("DJI_RECORD_PARSE_FAILURE") => "DJI_PARSE_ERROR",
        Some(code) => code,
        None => match failure.message {
            "Could not read the DJI flight-record file." => "DJI_IO_ERROR",
            "The file is not a supported DJI flight record." => "DJI_INVALID_FILE",
            "DJI_API_KEY is required to decrypt this flight record." => "DJI_API_KEY_MISSING",
            "Could not decrypt or parse the DJI flight record." => "DJI_PARSE_ERROR",
            _ => "DJI_BATCH_FILE_FAILURE",
        },
    }
}

fn batch_main(args: &[OsString]) -> ExitCode {
    let options = match parse_batch_options(args) {
        Ok(options) => options,
        Err(message) => {
            eprintln!("suite-dji-parser: {message}");
            return ExitCode::FAILURE;
        }
    };
    let paths = match flight_record_paths(&options.directory) {
        Ok(paths) => paths,
        Err(message) => {
            eprintln!("suite-dji-parser: {message}");
            return ExitCode::FAILURE;
        }
    };
    let processed = with_sanitized_panic_output(|| {
        process_paths_with(&paths, options.concurrency, &|path| {
            process_file(path.to_path_buf())
        })
    });
    let rows: Vec<_> = processed
        .into_iter()
        .map(|(path, result)| BatchRow::from_result(&path, result))
        .collect();
    if write_batch_csv(&options.output, &rows).is_err() {
        eprintln!("suite-dji-parser: Could not write the batch CSV summary.");
        return ExitCode::FAILURE;
    }
    print_batch_summary(&rows);
    ExitCode::SUCCESS
}

fn parse_batch_options(args: &[OsString]) -> Result<BatchOptions, &'static str> {
    const USAGE: &str =
        "Usage: suite-dji-parser --batch <DIRECTORY> --output <SUMMARY.csv> [--concurrency <N>]";
    if args.len() < 4 || args.first().and_then(|value| value.to_str()) != Some("--batch") {
        return Err(USAGE);
    }
    let directory = PathBuf::from(args.get(1).ok_or(USAGE)?);
    let mut output = None;
    let mut concurrency = 1usize;
    let mut index = 2;
    while index < args.len() {
        match args[index].to_str() {
            Some("--output") => {
                output = Some(PathBuf::from(args.get(index + 1).ok_or(USAGE)?));
                index += 2;
            }
            Some("--concurrency") => {
                concurrency = args
                    .get(index + 1)
                    .and_then(|value| value.to_str())
                    .and_then(|value| value.parse::<usize>().ok())
                    .filter(|value| *value > 0)
                    .ok_or("Concurrency must be a positive integer.")?;
                index += 2;
            }
            _ => return Err(USAGE),
        }
    }
    Ok(BatchOptions {
        directory,
        output: output.ok_or(USAGE)?,
        concurrency,
    })
}

fn flight_record_paths(directory: &std::path::Path) -> Result<Vec<PathBuf>, &'static str> {
    if !directory.is_dir() {
        return Err("The batch input path is not a readable directory.");
    }
    let entries =
        fs::read_dir(directory).map_err(|_| "The batch input directory could not be read.")?;
    let mut paths = Vec::new();
    for entry in entries {
        let Ok(entry) = entry else { continue };
        let path = entry.path();
        let is_txt = path
            .extension()
            .and_then(|value| value.to_str())
            .is_some_and(|value| value.eq_ignore_ascii_case("txt"));
        let is_flight_record = path
            .file_name()
            .and_then(|value| value.to_str())
            .is_some_and(|value| value.to_ascii_lowercase().contains("flightrecord"));
        if path.is_file() && is_txt && is_flight_record {
            paths.push(path);
        }
    }
    paths.sort();
    Ok(paths)
}

fn process_paths_with<F>(
    paths: &[PathBuf],
    concurrency: usize,
    processor: &F,
) -> Vec<(PathBuf, Result<Output, Failure>)>
where
    F: Fn(&std::path::Path) -> Result<Output, Failure> + Sync,
{
    let mut processed = Vec::with_capacity(paths.len());
    for chunk in paths.chunks(concurrency.max(1)) {
        thread::scope(|scope| {
            let handles: Vec<_> = chunk
                .iter()
                .map(|path| {
                    let path = path.clone();
                    let worker_path = path.clone();
                    (
                        path,
                        scope.spawn(move || {
                            panic::catch_unwind(AssertUnwindSafe(|| processor(&worker_path)))
                                .unwrap_or_else(|_| {
                                    Err(Failure::coded(
                                        "The DJI parser could not decode this flight record.",
                                        "DJI_PARSER_PANIC",
                                    ))
                                })
                        }),
                    )
                })
                .collect();
            for (path, handle) in handles {
                match handle.join() {
                    Ok(result) => processed.push((path, result)),
                    Err(_) => processed.push((
                        path,
                        Err(Failure::coded(
                            "The DJI parser worker failed.",
                            "DJI_PARSER_WORKER_FAILURE",
                        )),
                    )),
                }
            }
        });
    }
    processed
}

const CSV_HEADERS: [&str; 20] = [
    "filename",
    "success",
    "log_version",
    "encrypted",
    "aircraft_model",
    "aircraft_model_code",
    "aircraft_serial",
    "aircraft_serial_header",
    "battery_serial",
    "battery_serial_header",
    "start_time",
    "duration_seconds",
    "takeoff_latitude",
    "takeoff_longitude",
    "maximum_altitude_relative_m",
    "maximum_distance_from_home_m",
    "total_distance_m",
    "maximum_satellites",
    "warning_count",
    "sanitized_error_code",
];

fn write_batch_csv(path: &std::path::Path, rows: &[BatchRow]) -> io::Result<()> {
    let mut file = fs::File::create(path)?;
    write_csv_record(&mut file, CSV_HEADERS.iter().copied())?;
    for row in rows {
        let values = row.csv_values();
        write_csv_record(&mut file, values.iter().map(String::as_str))?;
    }
    Ok(())
}

fn write_csv_record<'a>(
    writer: &mut impl Write,
    values: impl IntoIterator<Item = &'a str>,
) -> io::Result<()> {
    let encoded: Vec<String> = values.into_iter().map(csv_escape).collect();
    writeln!(writer, "{}", encoded.join(","))
}

fn csv_escape(value: &str) -> String {
    if value.contains([',', '"', '\n', '\r']) {
        format!("\"{}\"", value.replace('"', "\"\""))
    } else {
        value.to_owned()
    }
}

fn option_string(value: Option<impl ToString>) -> String {
    value.map(|value| value.to_string()).unwrap_or_default()
}

fn print_batch_summary(rows: &[BatchRow]) {
    let successful = rows.iter().filter(|row| row.success).count();
    let encrypted = rows
        .iter()
        .filter(|row| row.encrypted == Some(true))
        .count();
    let unencrypted = rows
        .iter()
        .filter(|row| row.encrypted == Some(false))
        .count();
    let versions: BTreeSet<_> = rows.iter().filter_map(|row| row.log_version).collect();
    let model_codes: BTreeSet<_> = rows
        .iter()
        .filter_map(|row| row.aircraft_model_code)
        .collect();
    let unknown_model_codes: BTreeSet<_> = rows
        .iter()
        .filter(|row| row.aircraft_model.is_none())
        .filter_map(|row| row.aircraft_model_code)
        .collect();
    let aircraft_serials: BTreeSet<_> = rows
        .iter()
        .filter_map(|row| row.aircraft_serial.as_deref())
        .collect();
    let battery_serials: BTreeSet<_> = rows
        .iter()
        .filter_map(|row| row.battery_serial.as_deref())
        .collect();
    let mut errors = BTreeMap::<&str, usize>::new();
    for code in rows
        .iter()
        .filter_map(|row| row.sanitized_error_code.as_deref())
    {
        *errors.entry(code).or_default() += 1;
    }
    println!("total files: {}", rows.len());
    println!("successful: {successful}");
    println!("failed: {}", rows.len() - successful);
    println!("encrypted: {encrypted}");
    println!("unencrypted: {unencrypted}");
    println!("log versions seen: {}", display_set(&versions));
    println!("aircraft model codes seen: {}", display_set(&model_codes));
    println!(
        "unknown aircraft model codes: {}",
        display_set(&unknown_model_codes)
    );
    println!(
        "distinct aircraft ComponentSerial values: {}",
        display_set(&aircraft_serials)
    );
    println!(
        "distinct battery ComponentSerial values: {}",
        display_set(&battery_serials)
    );
    let error_summary = errors
        .iter()
        .map(|(code, count)| format!("{code} ({count})"))
        .collect::<Vec<_>>()
        .join(", ");
    println!(
        "parser error codes seen: {}",
        if error_summary.is_empty() {
            "none"
        } else {
            &error_summary
        }
    );
}

fn display_set<T: ToString + Ord>(values: &BTreeSet<T>) -> String {
    if values.is_empty() {
        "none".to_owned()
    } else {
        values
            .iter()
            .map(ToString::to_string)
            .collect::<Vec<_>>()
            .join(", ")
    }
}

fn fail(failure: &Failure) -> ExitCode {
    eprint!("suite-dji-parser: {}", failure.message);
    if let Some(code) = failure.diagnostic_code {
        eprint!(" [diagnostic_code={code}");
        if let Some(status) = failure.http_status {
            eprint!(" http_status={status}");
        }
        if let Some(status) = failure.api_status {
            eprint!(" api_status={status}");
        }
        eprint!("]");
    }
    eprintln!();
    ExitCode::FAILURE
}

fn keychain_fetch_failure(error: DjiParserError) -> Failure {
    const MESSAGE: &str = "Could not retrieve the DJI decryption keychain.";

    let (diagnostic_code, http_status, api_status) = match error {
        DjiParserError::ApiKeyError => ("DJI_AUTHORIZATION_FAILURE", Some(403), None),
        DjiParserError::NetworkRequestStatus(status) if matches!(status, 401 | 403) => {
            ("DJI_AUTHORIZATION_FAILURE", Some(status), None)
        }
        DjiParserError::NetworkRequestStatus(status) => {
            ("DJI_HTTP_STATUS_FAILURE", Some(status), None)
        }
        DjiParserError::Serialization(_) => ("DJI_MALFORMED_RESPONSE", None, None),
        DjiParserError::NetworkConnection => ("DJI_NETWORK_TLS_FAILURE", None, None),
        DjiParserError::ApiError(message) if is_unsupported_request_message(&message) => {
            ("DJI_UNSUPPORTED_DEPARTMENT_VERSION_REQUEST", None, None)
        }
        DjiParserError::ApiError(message) if message == "Missing keychain data" => {
            ("DJI_MALFORMED_RESPONSE", None, None)
        }
        DjiParserError::ApiError(_) => ("DJI_API_REJECTED_REQUEST", None, None),
        _ => ("DJI_KEYCHAIN_RETRIEVAL_FAILURE", None, None),
    };

    Failure {
        message: MESSAGE,
        diagnostic_code: Some(diagnostic_code),
        http_status,
        // dji-log-parser 0.5.7 does not retain the numeric DJI result code.
        api_status,
        log_version: None,
        encrypted: None,
    }
}

fn is_unsupported_request_message(message: &str) -> bool {
    let message = message.to_ascii_lowercase();
    message.contains("department")
        || message.contains("unsupported version")
        || message.contains("invalid version")
        || message.contains("version not support")
}

fn nonempty(value: &str) -> Option<String> {
    let value = value.trim();
    (!value.is_empty()).then(|| value.to_owned())
}

#[derive(Debug, Default, PartialEq)]
struct ComponentSerials {
    aircraft: Option<String>,
    battery: Option<String>,
    rc: Option<String>,
    camera: Option<String>,
}

fn component_serials(records: &[Record]) -> ComponentSerials {
    let mut aircraft = BTreeSet::new();
    let mut battery = BTreeSet::new();
    let mut rc = BTreeSet::new();
    let mut camera = BTreeSet::new();

    for record in records {
        if let Record::ComponentSerial(component) = record {
            let Some(serial) = nonempty(&component.serial) else {
                continue;
            };
            match component.component_type {
                // The Rust crate calls type 2 "Aircraft". DJI's reference
                // library calls it "FlightController" and uses it as the
                // full component serial corresponding to header aircraftSN.
                ComponentType::Aircraft => {
                    aircraft.insert(serial);
                }
                ComponentType::Battery => {
                    battery.insert(serial);
                }
                ComponentType::RC => {
                    rc.insert(serial);
                }
                ComponentType::Camera => {
                    camera.insert(serial);
                }
                ComponentType::Unknown(_) => {}
            }
        }
    }

    ComponentSerials {
        aircraft: only_distinct_serial(aircraft),
        battery: only_distinct_serial(battery),
        rc: only_distinct_serial(rc),
        camera: only_distinct_serial(camera),
    }
}

#[derive(Debug, Default, PartialEq)]
struct RecordMetrics {
    maximum_battery_temperature_c: Option<f32>,
    minimum_cell_voltage_v: Option<f32>,
    maximum_cell_voltage_v: Option<f32>,
    battery_cycle_count: Option<u16>,
    battery_life_value: Option<u8>,
}

fn record_metrics(records: &[Record], log_version: u8) -> RecordMetrics {
    let mut temperatures = Vec::new();
    let mut cell_voltages = Vec::new();
    let mut cycle_counts = BTreeSet::new();
    let mut life_values = BTreeSet::new();

    for record in records {
        match record {
            Record::CenterBattery(battery) => {
                if log_version >= 8 && battery.temperature.is_finite() {
                    temperatures.push(battery.temperature);
                }
                cell_voltages.extend(
                    [
                        battery.voltage_cell1,
                        battery.voltage_cell2,
                        battery.voltage_cell3,
                        battery.voltage_cell4,
                        battery.voltage_cell5,
                        battery.voltage_cell6,
                    ]
                    .into_iter()
                    .filter(|value| value.is_finite() && *value > 0.0),
                );
                cycle_counts.insert(battery.number_of_discharges);
                life_values.insert(battery.life);
            }
            Record::SmartBatteryGroup(group) => match group {
                dji_log_parser::record::smart_battery_group::SmartBatteryGroup::SmartBatteryStatic(
                    battery,
                ) => {
                    cycle_counts.insert(battery.loop_times);
                    life_values.insert(battery.battery_life);
                }
                dji_log_parser::record::smart_battery_group::SmartBatteryGroup::SmartBatteryDynamic(
                    battery,
                ) => {
                    if battery.temperature.is_finite() {
                        temperatures.push(battery.temperature);
                    }
                }
                dji_log_parser::record::smart_battery_group::SmartBatteryGroup::SmartBatterySingleVoltage(
                    battery,
                ) => {
                    cell_voltages.extend(
                        battery
                            .cell_voltages
                            .iter()
                            .copied()
                            .filter(|value| value.is_finite() && *value > 0.0),
                    );
                }
            },
            _ => {}
        }
    }

    RecordMetrics {
        maximum_battery_temperature_c: temperatures.into_iter().max_by(f32::total_cmp),
        minimum_cell_voltage_v: cell_voltages.iter().copied().min_by(f32::total_cmp),
        maximum_cell_voltage_v: cell_voltages.iter().copied().max_by(f32::total_cmp),
        battery_cycle_count: only_distinct_value(cycle_counts),
        battery_life_value: only_distinct_value(life_values),
    }
}

fn only_distinct_value<T: Ord>(values: BTreeSet<T>) -> Option<T> {
    (values.len() == 1)
        .then(|| values.into_iter().next())
        .flatten()
}

fn is_airborne(frame: &Frame) -> bool {
    frame.osd.is_motor_on && !frame.osd.is_on_ground
}

fn airborne_battery_frames(frames: &[Frame]) -> (Option<&Frame>, Option<&Frame>) {
    let mut airborne = frames
        .iter()
        .filter(|frame| is_airborne(frame) && battery_sample_available(frame));
    let first = airborne.next();
    let last = airborne.last().or(first);
    (first, last)
}

fn battery_sample_available(frame: &Frame) -> bool {
    frame.battery.charge_level > 0
        || frame.battery.current_capacity > 0
        || (frame.battery.voltage.is_finite() && frame.battery.voltage > 0.0)
}

fn valid_battery_percent(frame: &Frame) -> Option<u8> {
    (battery_sample_available(frame) && frame.battery.charge_level <= 100)
        .then_some(frame.battery.charge_level)
}

fn valid_battery_voltage(frame: &Frame) -> Option<f32> {
    (frame.battery.voltage.is_finite() && frame.battery.voltage > 0.0)
        .then_some(frame.battery.voltage)
}

fn valid_battery_capacity(frame: &Frame) -> Option<u32> {
    (frame.battery.current_capacity > 0).then_some(frame.battery.current_capacity)
}

fn airborne_duration_seconds(frames: &[Frame]) -> Option<f64> {
    let mut total = 0.0_f64;
    let mut observed_interval = false;
    for pair in frames.windows(2) {
        let start = &pair[0];
        let end = &pair[1];
        if !is_airborne(start) {
            continue;
        }
        let delta = f64::from(end.osd.fly_time - start.osd.fly_time);
        if delta.is_finite() && delta >= 0.0 {
            total += delta;
            observed_interval = true;
        }
    }
    observed_interval.then_some(total)
}

fn minimum_satellites_airborne(frames: &[Frame]) -> Option<u8> {
    frames
        .iter()
        .filter(|frame| is_airborne(frame))
        .map(|frame| frame.osd.gps_num)
        .min()
}

fn maximum_satellites(frames: &[Frame]) -> Option<u8> {
    frames.iter().map(|frame| frame.osd.gps_num).max()
}

fn minimum_gps_level_airborne(frames: &[Frame]) -> Option<u8> {
    frames
        .iter()
        .filter(|frame| is_airborne(frame))
        .map(|frame| frame.osd.gps_level)
        .min()
}

fn maximum_horizontal_speed_m_s(details_max: f32, frames: &[Frame]) -> Option<f32> {
    let frame_max = frames
        .iter()
        .filter(|frame| is_airborne(frame))
        .map(|frame| frame.osd.x_speed.hypot(frame.osd.y_speed))
        .filter(|speed| speed.is_finite())
        .max_by(f32::total_cmp);
    match (nonnegative_f32(details_max), frame_max) {
        (Some(details), Some(frame)) => Some(details.max(frame)),
        (Some(details), None) => Some(details),
        (None, frame) => frame,
    }
}

fn maximum_vertical_speed_m_s(details_max: f32, frames: &[Frame]) -> Option<f32> {
    let frame_max = frames
        .iter()
        .filter(|frame| is_airborne(frame))
        .map(|frame| frame.osd.z_speed.abs())
        .filter(|speed| speed.is_finite())
        .max_by(f32::total_cmp);
    match (nonnegative_f32(details_max.abs()), frame_max) {
        (Some(details), Some(frame)) => Some(details.max(frame)),
        (Some(details), None) => Some(details),
        (None, frame) => frame,
    }
}

fn flight_modes(frames: &[Frame]) -> Vec<String> {
    frames
        .iter()
        .filter(|frame| is_airborne(frame))
        .filter_map(|frame| frame.osd.flyc_state)
        .map(|mode| format!("{mode:?}"))
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect()
}

fn signal_loss_events_over_one_second(records: &[Record]) -> Option<u32> {
    let mut saw_osd = false;
    let mut event_start = None;
    let mut last_time = None;
    let mut count = 0_u32;

    for record in records {
        let Record::OSD(osd) = record else { continue };
        saw_osd = true;
        let airborne = osd.is_motor_up
            && matches!(
                osd.ground_or_sky,
                dji_log_parser::record::osd::GroundOrSky::Sky
            );
        let lost = airborne && osd.rc_outcontrol;
        if lost && event_start.is_none() {
            event_start = Some(osd.fly_time);
        } else if !lost {
            if let Some(start) = event_start.take() {
                if osd.fly_time - start >= 1.0 {
                    count += 1;
                }
            }
        }
        last_time = Some(osd.fly_time);
    }
    if let (Some(start), Some(end)) = (event_start, last_time) {
        if end - start >= 1.0 {
            count += 1;
        }
    }
    saw_osd.then_some(count)
}

fn only_distinct_serial(serials: BTreeSet<String>) -> Option<String> {
    if serials.len() == 1 {
        serials.into_iter().next()
    } else {
        None
    }
}

fn nonnegative_f32(value: f32) -> Option<f32> {
    (value.is_finite() && value >= 0.0).then_some(value)
}

fn nonnegative_f64(value: f64) -> Option<f64> {
    (value.is_finite() && value >= 0.0).then_some(value)
}

fn valid_coordinate(latitude: f64, longitude: f64) -> bool {
    latitude.is_finite()
        && longitude.is_finite()
        && (-90.0..=90.0).contains(&latitude)
        && (-180.0..=180.0).contains(&longitude)
        && !(latitude == 0.0 && longitude == 0.0)
}

fn takeoff_coordinates(frames: &[Frame]) -> (Option<f64>, Option<f64>) {
    frames
        .iter()
        .find(|frame| {
            frame.osd.is_motor_on
                && !frame.osd.is_on_ground
                && frame.osd.is_gpd_used
                && frame.osd.gps_level >= 3
                && valid_coordinate(frame.osd.latitude, frame.osd.longitude)
        })
        .map(|frame| (Some(frame.osd.latitude), Some(frame.osd.longitude)))
        .unwrap_or((None, None))
}

fn total_distance_m(header_distance_km: f32) -> Option<f32> {
    nonnegative_f32(header_distance_km).and_then(|value| {
        let meters = value * 1000.0;
        meters.is_finite().then_some(meters)
    })
}

fn maximum_relative_altitude(details_max_height: f32, frames: &[Frame]) -> Option<f32> {
    let frame_max = frames
        .iter()
        .map(|frame| frame.osd.height)
        .filter(|value| value.is_finite())
        .max_by(|a, b| a.total_cmp(b));

    match (nonnegative_f32(details_max_height), frame_max) {
        (Some(details), Some(frame)) => Some(details.max(frame)),
        (Some(details), None) => Some(details),
        (None, Some(frame)) if frame >= 0.0 => Some(frame),
        _ => None,
    }
}

fn plausible_asl_m(value: f32) -> Option<f32> {
    (value.is_finite() && (-500.0..=10_000.0).contains(&value)).then_some(value)
}

fn takeoff_altitude_asl_m(details_takeoff_altitude: f32, frames: &[Frame]) -> Option<f32> {
    plausible_asl_m(details_takeoff_altitude).or_else(|| {
        frames
            .iter()
            .find(|frame| frame.home.is_home_record)
            .and_then(|frame| plausible_asl_m(frame.home.altitude))
    })
}

fn maximum_distance_from_home(frames: &[Frame]) -> Option<f64> {
    frames
        .iter()
        .filter(|frame| {
            frame.home.is_home_record
                && valid_coordinate(frame.home.latitude, frame.home.longitude)
                && valid_coordinate(frame.osd.latitude, frame.osd.longitude)
        })
        .map(|frame| {
            haversine_m(
                frame.home.latitude,
                frame.home.longitude,
                frame.osd.latitude,
                frame.osd.longitude,
            )
        })
        .filter(|distance| distance.is_finite())
        .max_by(|a, b| a.total_cmp(b))
}

fn haversine_m(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    let lat1 = lat1.to_radians();
    let lat2 = lat2.to_radians();
    let delta_lat = lat2 - lat1;
    let delta_lon = (lon2 - lon1).to_radians();
    let a =
        (delta_lat / 2.0).sin().powi(2) + lat1.cos() * lat2.cos() * (delta_lon / 2.0).sin().powi(2);
    2.0 * EARTH_RADIUS_M * a.sqrt().atan2((1.0 - a).sqrt())
}

fn aircraft_model_and_code(parser: &DJILog, frames: &[Frame]) -> (Option<String>, Option<u8>) {
    let product = parser.details.product_type;
    if !matches!(product, ProductType::None) {
        let code = product_type_code(product);
        let name = (!matches!(product, ProductType::Unknown(_))).then(|| format!("{product:?}"));
        return (name, Some(code));
    }

    if let Some(drone_type) = frames
        .iter()
        .filter_map(|frame| frame.osd.drone_type)
        .find(|model| !matches!(model, DroneType::None))
    {
        let code = drone_type_code(drone_type);
        let name =
            (!matches!(drone_type, DroneType::Unknown(_))).then(|| format!("{drone_type:?}"));
        return (name, Some(code));
    }

    (None, None)
}

fn product_type_code(product: ProductType) -> u8 {
    match product {
        ProductType::None => 0,
        ProductType::Inspire1 => 1,
        ProductType::Phantom3Standard => 2,
        ProductType::Phantom3Advanced => 3,
        ProductType::Phantom3Pro => 4,
        ProductType::OSMO => 5,
        ProductType::Matrice100 => 6,
        ProductType::Phantom4 => 7,
        ProductType::LB2 => 8,
        ProductType::Inspire1Pro => 9,
        ProductType::A3 => 10,
        ProductType::Matrice600 => 11,
        ProductType::Phantom34K => 12,
        ProductType::MavicPro => 13,
        ProductType::ZenmuseXT => 14,
        ProductType::Inspire1RAW => 15,
        ProductType::A2 => 16,
        ProductType::Inspire2 => 17,
        ProductType::OSMOPro => 18,
        ProductType::OSMORaw => 19,
        ProductType::OSMOPlus => 20,
        ProductType::Mavic => 21,
        ProductType::OSMOMobile => 22,
        ProductType::OrangeCV600 => 23,
        ProductType::Phantom4Pro => 24,
        ProductType::N3FC => 25,
        ProductType::Spark => 26,
        ProductType::Matrice600Pro => 27,
        ProductType::Phantom4Advanced => 28,
        ProductType::Phantom3SE => 29,
        ProductType::AG405 => 30,
        ProductType::Matrice200 => 31,
        ProductType::Matrice210 => 33,
        ProductType::Matrice210RTK => 34,
        ProductType::MavicAir => 38,
        ProductType::Mavic2 => 42,
        ProductType::Phantom4ProV2 => 44,
        ProductType::Phantom4RTK => 46,
        ProductType::Phantom4Multispectral => 57,
        ProductType::Mavic2Enterprise => 58,
        ProductType::MavicMini => 59,
        ProductType::Matrice200V2 => 60,
        ProductType::Matrice210V2 => 61,
        ProductType::Matrice210RTKV2 => 62,
        ProductType::MavicAir2 => 67,
        ProductType::Matrice300RTK => 70,
        ProductType::FPV => 73,
        ProductType::MavicAir2S => 75,
        ProductType::Mini2 => 76,
        ProductType::Mavic3 => 77,
        ProductType::MiniSE => 96,
        ProductType::Mini3Pro => 103,
        ProductType::Mavic3Pro => 111,
        ProductType::Mini2SE => 113,
        ProductType::Matrice30 => 116,
        ProductType::Mavic3Enterprise => 118,
        ProductType::Avata => 121,
        ProductType::Mini4Pro => 126,
        ProductType::Avata2 => 152,
        ProductType::Matrice350RTK => 170,
        ProductType::Unknown(code) => code,
    }
}

fn drone_type_code(drone_type: DroneType) -> u8 {
    match drone_type {
        DroneType::None => 0,
        DroneType::Inspire1 => 1,
        DroneType::Phantom3Advanced => 2,
        DroneType::Phantom3Pro => 3,
        DroneType::Phantom3Standard => 4,
        DroneType::OpenFrame => 5,
        DroneType::AceOne => 6,
        DroneType::WKM => 7,
        DroneType::Naza => 8,
        DroneType::A2 => 9,
        DroneType::A3 => 10,
        DroneType::Phantom4 => 11,
        DroneType::Matrice600 => 14,
        DroneType::Phantom34K => 15,
        DroneType::MavicPro => 16,
        DroneType::Inspire2 => 17,
        DroneType::Phantom4Pro => 18,
        DroneType::N3 => 20,
        DroneType::Spark => 21,
        DroneType::Matrice600Pro => 23,
        DroneType::MavicAir => 24,
        DroneType::Matrice200 => 25,
        DroneType::Phantom4Advanced => 27,
        DroneType::Matrice210 => 28,
        DroneType::Phantom3SE => 29,
        DroneType::Matrice210RTK => 30,
        DroneType::Phantom4ProV2 => 36,
        DroneType::Mavic2 => 41,
        DroneType::Mavic2Enterprise => 51,
        DroneType::MavicAir2 => 58,
        DroneType::Matrice300RTK => 60,
        DroneType::Mini2 => 63,
        DroneType::Mavic3Enterprise => 77,
        DroneType::Mavic3Pro => 84,
        DroneType::Matrice350RTK => 89,
        DroneType::Mini4Pro => 93,
        DroneType::Avata2 => 94,
        DroneType::Unknown(code) => code,
    }
}

#[derive(Debug, Default, PartialEq)]
struct OperationalMessages {
    warnings: Vec<String>,
    serious_warnings: Vec<String>,
    tips: Vec<String>,
}

fn operational_messages(records: &[Record]) -> OperationalMessages {
    let mut result = OperationalMessages::default();
    let mut warnings_seen = BTreeSet::new();
    let mut serious_seen = BTreeSet::new();
    let mut tips_seen = BTreeSet::new();

    for record in records {
        match record {
            Record::AppWarn(value) => {
                push_bounded_message(&mut result.warnings, &mut warnings_seen, &value.message)
            }
            Record::AppSeriousWarn(value) => push_bounded_message(
                &mut result.serious_warnings,
                &mut serious_seen,
                &value.message,
            ),
            Record::AppTip(value) => {
                push_bounded_message(&mut result.tips, &mut tips_seen, &value.message)
            }
            _ => {}
        }
    }
    result
}

fn push_bounded_message(output: &mut Vec<String>, seen: &mut BTreeSet<String>, message: &str) {
    if output.len() >= MAX_OPERATIONAL_MESSAGES_PER_KIND {
        return;
    }
    let normalized = message.split_whitespace().collect::<Vec<_>>().join(" ");
    if normalized.is_empty() {
        return;
    }
    let bounded: String = normalized
        .chars()
        .take(MAX_OPERATIONAL_MESSAGE_CHARS)
        .collect();
    if seen.insert(bounded.clone()) {
        output.push(bounded);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn successful_output(version: u8, model_code: u8) -> Output {
        Output {
            success: true,
            parser_version: DJI_LOG_PARSER_VERSION,
            log_version: version,
            encrypted: version >= ENCRYPTED_VERSION_MINIMUM,
            aircraft_model: None,
            aircraft_model_code: Some(model_code),
            aircraft_name: None,
            aircraft_serial: Some(format!("AIRCRAFT-{model_code}")),
            aircraft_serial_header: Some("HEADER".to_owned()),
            battery_serial: Some(format!("BATTERY-{model_code}")),
            battery_serial_header: Some("BAT-HEADER".to_owned()),
            start_time: None,
            duration_seconds: Some(10.0),
            airborne_duration_seconds: None,
            takeoff_latitude: None,
            takeoff_longitude: None,
            takeoff_altitude_asl_m: None,
            maximum_altitude_relative_m: None,
            maximum_distance_from_home_m: None,
            total_distance_m: None,
            maximum_satellites: None,
            minimum_satellites_airborne: None,
            minimum_airborne_satellites: None,
            minimum_gps_signal_level_airborne: None,
            maximum_gps_signal_level: None,
            takeoff_battery_percent: None,
            landing_battery_percent: None,
            takeoff_battery_voltage_v: None,
            landing_battery_voltage_v: None,
            takeoff_battery_capacity_mah: None,
            landing_battery_capacity_mah: None,
            maximum_battery_temperature_c: None,
            minimum_cell_voltage_v: None,
            maximum_cell_voltage_v: None,
            battery_cycle_count: None,
            battery_life_value: None,
            battery_life_raw: None,
            maximum_horizontal_speed_m_s: None,
            maximum_vertical_speed_m_s: None,
            maximum_vertical_speed_mps: None,
            signal_loss_events_over_one_second: None,
            photo_count: None,
            flight_modes: Vec::new(),
            rc_serial: None,
            camera_serial: None,
            warnings: Vec::new(),
            serious_warnings: Vec::new(),
            tips: Vec::new(),
            messages: Vec::new(),
        }
    }

    #[test]
    fn batch_processes_multiple_successful_files() {
        let paths = vec![
            PathBuf::from("FlightRecord_a.txt"),
            PathBuf::from("FlightRecord_b.txt"),
        ];
        let results = process_paths_with(&paths, 2, &|path| {
            let code = if path.ends_with("FlightRecord_a.txt") {
                108
            } else {
                137
            };
            Ok(successful_output(14, code))
        });
        assert_eq!(results.len(), 2);
        assert!(results.iter().all(|(_, result)| result.is_ok()));
    }

    #[test]
    fn batch_continues_after_one_parser_failure() {
        let paths = vec![
            PathBuf::from("FlightRecord_good_a.txt"),
            PathBuf::from("FlightRecord_bad.txt"),
            PathBuf::from("FlightRecord_good_b.txt"),
        ];
        let results = process_paths_with(&paths, 1, &|path| {
            if path.ends_with("FlightRecord_bad.txt") {
                Err(Failure::user_safe(
                    "Could not decrypt or parse the DJI flight record.",
                ))
            } else {
                Ok(successful_output(14, 108))
            }
        });
        assert_eq!(results.len(), 3);
        assert_eq!(
            results.iter().filter(|(_, result)| result.is_ok()).count(),
            2
        );
        assert_eq!(
            results.iter().filter(|(_, result)| result.is_err()).count(),
            1
        );
        assert!(results[2].1.is_ok());
    }

    #[test]
    fn batch_csv_escapes_filename_commas_quotes_and_newlines() {
        assert_eq!(csv_escape("plain.txt"), "plain.txt");
        assert_eq!(csv_escape("flight,one.txt"), "\"flight,one.txt\"");
        assert_eq!(csv_escape("flight\"one.txt"), "\"flight\"\"one.txt\"");
        assert_eq!(csv_escape("flight\none.txt"), "\"flight\none.txt\"");
    }

    #[test]
    fn missing_api_key_has_sanitized_batch_code() {
        let failure = Failure::user_safe("DJI_API_KEY is required to decrypt this flight record.")
            .with_log_context(14);
        let row = BatchRow::from_result(
            std::path::Path::new("FlightRecord_encrypted.txt"),
            Err(failure),
        );
        assert!(!row.success);
        assert_eq!(row.encrypted, Some(true));
        assert_eq!(
            row.sanitized_error_code.as_deref(),
            Some("DJI_API_KEY_MISSING")
        );
    }

    #[test]
    fn batch_csv_contains_mixed_success_and_failure_rows() {
        let directory = tempfile::tempdir().unwrap();
        let output_path = directory.path().join("summary.csv");
        let success = BatchRow::from_result(
            std::path::Path::new("FlightRecord_ok.txt"),
            Ok(successful_output(14, 108)),
        );
        let failure = BatchRow::from_result(
            std::path::Path::new("FlightRecord_bad.txt"),
            Err(Failure::user_safe(
                "The file is not a supported DJI flight record.",
            )),
        );
        write_batch_csv(&output_path, &[success, failure]).unwrap();
        let csv = fs::read_to_string(output_path).unwrap();
        assert_eq!(csv.lines().count(), 3);
        assert!(csv.contains("FlightRecord_ok.txt,true"));
        assert!(csv.contains("FlightRecord_bad.txt,false"));
        assert!(csv.contains("DJI_INVALID_FILE"));
    }

    #[test]
    fn batch_maps_failures_to_stable_sanitized_codes() {
        let cases = [
            (
                Failure::user_safe("Could not read the DJI flight-record file."),
                "DJI_IO_ERROR",
            ),
            (
                Failure::user_safe("The file is not a supported DJI flight record."),
                "DJI_INVALID_FILE",
            ),
            (
                Failure::coded("Safe.", "DJI_NETWORK_TLS_FAILURE"),
                "DJI_KEYCHAIN_UNAVAILABLE",
            ),
            (
                Failure::coded("Safe.", "DJI_MALFORMED_RESPONSE"),
                "DJI_KEYCHAIN_RESPONSE_INVALID",
            ),
            (
                Failure::user_safe("Could not decrypt or parse the DJI flight record."),
                "DJI_PARSE_ERROR",
            ),
        ];
        for (failure, expected) in cases {
            assert_eq!(batch_error_code(&failure), expected);
        }
    }

    #[test]
    fn batch_classifies_parser_panic_and_continues() {
        let paths = vec![
            PathBuf::from("FlightRecord_panic.txt"),
            PathBuf::from("FlightRecord_good.txt"),
        ];
        let previous_panic_hook = panic::take_hook();
        panic::set_hook(Box::new(|_| {}));
        let results = process_paths_with(&paths, 1, &|path| {
            if path.ends_with("FlightRecord_panic.txt") {
                panic!("parser implementation detail that must not be serialized");
            }
            Ok(successful_output(14, 108))
        });
        panic::set_hook(previous_panic_hook);

        assert_eq!(results.len(), 2);
        let failure = results[0].1.as_ref().unwrap_err();
        assert_eq!(failure.diagnostic_code, Some("DJI_PARSER_PANIC"));
        assert_eq!(batch_error_code(failure), "DJI_PARSER_PANIC");
        assert!(results[1].1.is_ok());
    }

    #[test]
    fn haversine_is_zero_for_same_point() {
        assert_eq!(haversine_m(39.0, -86.0, 39.0, -86.0), 0.0);
    }

    #[test]
    fn takeoff_asl_accepts_plausible_details_and_rejects_sentinels() {
        assert_eq!(takeoff_altitude_asl_m(250.5, &[]), Some(250.5));
        assert_eq!(takeoff_altitude_asl_m(-999.0, &[]), None);
        assert_eq!(takeoff_altitude_asl_m(f32::NAN, &[]), None);
    }

    #[test]
    fn empty_serial_is_missing() {
        assert_eq!(nonempty("   "), None);
    }

    #[test]
    fn only_one_distinct_component_serial_is_authoritative() {
        let one = BTreeSet::from(["FULL-SERIAL-1234567890".to_owned()]);
        assert_eq!(
            only_distinct_serial(one),
            Some("FULL-SERIAL-1234567890".to_owned())
        );

        let ambiguous = BTreeSet::from(["BATTERY-A".to_owned(), "BATTERY-B".to_owned()]);
        assert_eq!(only_distinct_serial(ambiguous), None);
    }

    #[test]
    fn component_serial_rules_apply_to_rc_and_camera_identities() {
        let rc = BTreeSet::from(["RC-SERIAL".to_owned()]);
        assert_eq!(only_distinct_serial(rc), Some("RC-SERIAL".to_owned()));

        let ambiguous_camera = BTreeSet::from(["CAMERA-A".to_owned(), "CAMERA-B".to_owned()]);
        assert_eq!(only_distinct_serial(ambiguous_camera), None);
    }

    #[test]
    fn operational_messages_preserve_order_deduplicate_and_bound_output() {
        let mut output = Vec::new();
        let mut seen = BTreeSet::new();
        push_bounded_message(&mut output, &mut seen, " First   warning ");
        push_bounded_message(&mut output, &mut seen, "First warning");
        push_bounded_message(&mut output, &mut seen, "Second warning");
        push_bounded_message(&mut output, &mut seen, &"x".repeat(500));

        assert_eq!(&output[..2], ["First warning", "Second warning"]);
        assert_eq!(output.len(), 3);
        assert_eq!(output[2].chars().count(), MAX_OPERATIONAL_MESSAGE_CHARS);

        for index in 0..100 {
            push_bounded_message(
                &mut output,
                &mut seen,
                &format!("Additional warning {index}"),
            );
        }
        assert_eq!(output.len(), MAX_OPERATIONAL_MESSAGES_PER_KIND);
    }

    #[test]
    fn zero_coordinates_are_missing() {
        assert!(!valid_coordinate(0.0, 0.0));
        assert!(valid_coordinate(39.0, -86.0));
    }

    #[test]
    fn details_distance_is_converted_from_kilometers_to_meters() {
        assert_eq!(total_distance_m(2.696763), Some(2696.763));
    }

    #[test]
    fn unknown_product_has_code_but_no_invented_name() {
        let product = ProductType::Unknown(108);
        assert_eq!(product_type_code(product), 108);
        assert!(matches!(product, ProductType::Unknown(108)));
    }

    #[test]
    fn takeoff_requires_an_airborne_motor_on_gps_frame() {
        let mut ground = Frame::default();
        ground.osd.latitude = 47.0;
        ground.osd.longitude = -122.0;
        ground.osd.is_motor_on = true;
        ground.osd.is_on_ground = true;
        ground.osd.is_gpd_used = true;
        ground.osd.gps_level = 5;

        let mut airborne = ground.clone();
        airborne.osd.latitude = 47.1;
        airborne.osd.longitude = -122.1;
        airborne.osd.is_on_ground = false;

        assert_eq!(
            takeoff_coordinates(&[ground, airborne]),
            (Some(47.1), Some(-122.1))
        );
    }

    #[test]
    fn airborne_duration_uses_only_airborne_frame_intervals() {
        let mut ground = Frame::default();
        ground.osd.fly_time = 0.0;
        ground.osd.is_motor_on = true;
        ground.osd.is_on_ground = true;

        let mut airborne_one = ground.clone();
        airborne_one.osd.fly_time = 1.0;
        airborne_one.osd.is_on_ground = false;

        let mut airborne_two = airborne_one.clone();
        airborne_two.osd.fly_time = 3.0;

        let mut landed = ground.clone();
        landed.osd.fly_time = 4.0;

        assert_eq!(
            airborne_duration_seconds(&[ground, airborne_one, airborne_two, landed]),
            Some(3.0)
        );
    }

    #[test]
    fn satellite_and_gps_levels_use_exact_airborne_semantics() {
        let mut ground = Frame::default();
        ground.osd.gps_num = 35;
        ground.osd.gps_level = 5;

        let mut airborne_one = Frame::default();
        airborne_one.osd.is_motor_on = true;
        airborne_one.osd.is_on_ground = false;
        airborne_one.osd.gps_num = 18;
        airborne_one.osd.gps_level = 4;

        let mut airborne_two = airborne_one.clone();
        airborne_two.osd.gps_num = 12;
        airborne_two.osd.gps_level = 2;

        let frames = [ground, airborne_one, airborne_two];
        assert_eq!(maximum_satellites(&frames), Some(35));
        assert_eq!(minimum_satellites_airborne(&frames), Some(12));
        assert_eq!(minimum_gps_level_airborne(&frames), Some(2));
    }

    #[test]
    fn battery_samples_use_first_and_last_available_airborne_values() {
        let mut missing = Frame::default();
        missing.osd.is_motor_on = true;
        missing.osd.is_on_ground = false;

        let mut takeoff = missing.clone();
        takeoff.battery.charge_level = 95;
        takeoff.battery.voltage = 17.2;
        takeoff.battery.current_capacity = 4800;

        let mut landing = takeoff.clone();
        landing.battery.charge_level = 21;
        landing.battery.voltage = 14.8;
        landing.battery.current_capacity = 1050;

        let frames = [missing, takeoff, landing];
        let (first, last) = airborne_battery_frames(&frames);
        assert_eq!(first.and_then(valid_battery_percent), Some(95));
        assert_eq!(last.and_then(valid_battery_percent), Some(21));
        assert_eq!(first.and_then(valid_battery_capacity), Some(4800));
        assert_eq!(last.and_then(valid_battery_capacity), Some(1050));
    }

    #[test]
    fn maximum_horizontal_speed_uses_vector_magnitude_and_details_maximum() {
        let mut frame = Frame::default();
        frame.osd.is_motor_on = true;
        frame.osd.is_on_ground = false;
        frame.osd.x_speed = 3.0;
        frame.osd.y_speed = 4.0;
        assert_eq!(maximum_horizontal_speed_m_s(4.5, &[frame]), Some(5.0));
    }

    #[test]
    fn keychain_http_error_reports_only_status_and_code() {
        let failure = keychain_fetch_failure(DjiParserError::NetworkRequestStatus(429));
        assert_eq!(
            failure.message,
            "Could not retrieve the DJI decryption keychain."
        );
        assert_eq!(failure.diagnostic_code, Some("DJI_HTTP_STATUS_FAILURE"));
        assert_eq!(failure.http_status, Some(429));
        assert_eq!(failure.api_status, None);
    }

    #[test]
    fn keychain_authentication_error_is_distinct() {
        let failure = keychain_fetch_failure(DjiParserError::ApiKeyError);
        assert_eq!(failure.diagnostic_code, Some("DJI_AUTHORIZATION_FAILURE"));
        assert_eq!(failure.http_status, Some(403));
    }

    #[test]
    fn raw_api_message_is_not_retained_in_failure() {
        let sensitive_response_text = "unsupported department: private response details";
        let failure =
            keychain_fetch_failure(DjiParserError::ApiError(sensitive_response_text.to_owned()));
        assert_eq!(
            failure.diagnostic_code,
            Some("DJI_UNSUPPORTED_DEPARTMENT_VERSION_REQUEST")
        );
        assert!(!failure.message.contains(sensitive_response_text));
    }

    #[test]
    fn network_and_malformed_response_are_distinct() {
        let network = keychain_fetch_failure(DjiParserError::NetworkConnection);
        let malformed =
            keychain_fetch_failure(DjiParserError::ApiError("Missing keychain data".to_owned()));
        assert_eq!(network.diagnostic_code, Some("DJI_NETWORK_TLS_FAILURE"));
        assert_eq!(malformed.diagnostic_code, Some("DJI_MALFORMED_RESPONSE"));
    }
}
