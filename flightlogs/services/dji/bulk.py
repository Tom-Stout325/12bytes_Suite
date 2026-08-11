from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from flightlogs.models import FlightLogSource
from flightlogs.services.matching import MatchType

from .errors import ERROR_DETAILS
from .importer import DJIImportResult, import_dji_upload


class BulkDJIClassification(StrEnum):
    IMPORTED_NEW = "imported_new"
    LINKED_EXISTING = "linked_existing"
    REVIEW_PARTIAL = "review_partial"
    REVIEW_AMBIGUOUS = "review_ambiguous"
    DUPLICATE = "duplicate"
    FAILED = "failed"


@dataclass(frozen=True)
class BulkDJIFileResult:
    filename: str
    classification: BulkDJIClassification
    label: str
    source: FlightLogSource | None = None
    flight_log_id: int | None = None
    flight_datetime: datetime | None = None
    aircraft: str = ""
    safe_error_code: str = ""
    safe_error_message: str = ""


@dataclass(frozen=True)
class BulkDJIImportResult:
    files: tuple[BulkDJIFileResult, ...]
    counts: dict[str, int]


def _classify_result(filename: str, result: DJIImportResult) -> BulkDJIFileResult:
    source = result.source
    flight = source.flight_log
    if result.duplicate:
        classification = BulkDJIClassification.DUPLICATE
        label = "Duplicate Source"
    elif source.status == FlightLogSource.Status.FAILED:
        classification = BulkDJIClassification.FAILED
        label = "Failed"
    elif result.match_type == MatchType.NO_MATCH:
        classification = BulkDJIClassification.IMPORTED_NEW
        label = "Imported New Flight"
    elif result.match_type in {
        MatchType.HIGH_CONFIDENCE,
        MatchType.HIGH_CONFIDENCE_LOCATION_VARIANCE,
    }:
        classification = BulkDJIClassification.LINKED_EXISTING
        label = "Linked Existing Flight"
    elif result.match_type == MatchType.REVIEW_PARTIAL_AIRDATA:
        classification = BulkDJIClassification.REVIEW_PARTIAL
        label = "Review Required — Partial Existing Record"
    else:
        classification = BulkDJIClassification.REVIEW_AMBIGUOUS
        label = "Review Required — Ambiguous Match"

    error_code = source.safe_error_code
    aircraft = (
        flight.drone_name or flight.drone_type or flight.drone_serial
        if flight
        else source.aircraft_serial or source.aircraft_serial_header
    )
    return BulkDJIFileResult(
        filename=filename,
        classification=classification,
        label=label,
        source=source,
        flight_log_id=source.flight_log_id,
        flight_datetime=flight.takeoff_datetime if flight else None,
        aircraft=aircraft,
        safe_error_code=error_code,
        safe_error_message=ERROR_DETAILS.get(error_code, source.safe_error_detail),
    )


def import_dji_batch(*, business, user, uploads) -> BulkDJIImportResult:
    """Import a bounded batch sequentially; each file owns its transaction lifecycle."""
    files = []
    for uploaded in uploads:
        filename = (getattr(uploaded, "name", "") or "DJIFlightRecord.txt")[:255]
        try:
            result = import_dji_upload(
                business=business,
                user=user,
                uploaded=uploaded,
            )
            files.append(_classify_result(filename, result))
        except Exception:
            files.append(
                BulkDJIFileResult(
                    filename=filename,
                    classification=BulkDJIClassification.FAILED,
                    label="Failed",
                    safe_error_code="DJI_PARSER_WORKER_FAILURE",
                    safe_error_message=ERROR_DETAILS["DJI_PARSER_WORKER_FAILURE"],
                )
            )

    counts = Counter(item.classification.value for item in files)
    counts["total"] = len(files)
    counts["review"] = (
        counts[BulkDJIClassification.REVIEW_PARTIAL.value]
        + counts[BulkDJIClassification.REVIEW_AMBIGUOUS.value]
    )
    return BulkDJIImportResult(tuple(files), dict(counts))
