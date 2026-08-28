class DJIImportError(Exception):
    """A bounded, user-safe DJI import failure."""

    def __init__(self, code, detail):
        self.code = code
        self.detail = detail[:255]
        super().__init__(self.detail)


ERROR_DETAILS = {
    "DJI_IO_ERROR": "The DJI flight record could not be read.",
    "DJI_INVALID_FILE": "The upload is not a valid supported DJI flight record.",
    "DJI_UNSUPPORTED_LOG": "This DJI flight-record version is not supported.",
    "DJI_KEYCHAIN_UNAVAILABLE": "DJI decryption information is temporarily unavailable. Please try again later.",
    "DJI_KEYCHAIN_RESPONSE_INVALID": "DJI returned invalid decryption information. Please try again later.",
    "DJI_PARSE_ERROR": "The DJI parser rejected or could not decode this flight record.",
    "DJI_PARSER_PANIC": "The log could not be decoded by the installed DJI parser and can be retried after a parser update.",
    "DJI_PARSER_TIMEOUT": "The DJI parser timed out. Please try again.",
    "DJI_API_KEY_MISSING": "DJI decryption is not configured on this server.",
    "DJI_PARSER_WORKER_FAILURE": "An unexpected internal parsing failure occurred.",
    "DJI_PARSER_MISSING": "The DJI parser executable is missing or is not executable.",
    "DJI_PARSER_OUTPUT_INVALID": "The DJI parser returned an invalid result.",
    "DJI_EQUIPMENT_AMBIGUOUS": "Multiple aircraft equipment records have this serial number; select the correct equipment during review.",
}


def import_error(code):
    safe_code = code if code in ERROR_DETAILS else "DJI_PARSER_WORKER_FAILURE"
    return DJIImportError(safe_code, ERROR_DETAILS[safe_code])
