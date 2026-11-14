"""Domain enumerations matching Table 4.23a, Table 4.23b, and Section 5.7.2 of the M2 report."""

from enum import StrEnum


class ScanState(StrEnum):
    """Scan states defined in Table 4.23a (Scan States)."""

    RECEIVED = "RECEIVED"
    LANE_BUSY = "LANE_BUSY"
    REJECTED = "REJECTED"
    CANNOT_PROCESS = "CANNOT_PROCESS"
    ACCEPTED = "ACCEPTED"
    OPENED = "OPENED"
    CONTROLLER_ERROR = "CONTROLLER_ERROR"
    UNDETERMINED = "UNDETERMINED"
    PENDING_RECONCILIATION = "PENDING_RECONCILIATION"
    RECONCILED = "RECONCILED"


class PassageStatus(StrEnum):
    """Passage track states inside GateCycle defined in Table 4.23b."""

    WAITING = "WAITING"
    PASSED = "PASSED"
    NO_PASSAGE = "NO_PASSAGE"


class ClosureStatus(StrEnum):
    """Closure and emergency track states inside GateCycle defined in Table 4.23b."""

    OPENED = "OPENED"
    CLEAR_TO_CLOSE = "CLEAR_TO_CLOSE"
    CLOSE_SENT = "CLOSE_SENT"
    CLOSED = "CLOSED"
    CLOSE_FAILED = "CLOSE_FAILED"
    CLOSE_UNCERTAIN = "CLOSE_UNCERTAIN"
    EMERGENCY_HOLD = "EMERGENCY_HOLD"


class OperatingMode(StrEnum):
    """Factory eligibility operating modes defined in Section 5.7.2 (I-10, ADR-06)."""

    NORMAL = "NORMAL"
    DEGRADED = "DEGRADED"
    CANNOT_PROCESS = "CANNOT_PROCESS"


class Direction(StrEnum):
    """Gate lane movement direction."""

    ENTRY = "ENTRY"
    EXIT = "EXIT"


class CommandType(StrEnum):
    """Types of commands issued to the turnstile controller port."""

    OPEN = "OPEN"
    CLOSE = "CLOSE"
    EMERGENCY_RELEASE = "EMERGENCY_RELEASE"
    RESET = "RESET"


class CommandOutcome(StrEnum):
    """Controller command outcomes distinguishing receipt, confirmed opening/closing, failure, and uncertainty (§7.1.3 LSP)."""

    # Stored states of a GateCommand only; a port never returns these two values.
    SENT = "SENT"
    NOT_SENT = "NOT_SENT"
    OPEN_CONFIRMED = "OPEN_CONFIRMED"
    CLOSED = "CLOSED"
    EMERGENCY_RELEASED = "EMERGENCY_RELEASED"
    RESET_CONFIRMED = "RESET_CONFIRMED"
    CONTROLLER_ERROR = "CONTROLLER_ERROR"
    UNDETERMINED = "UNDETERMINED"


class DedupStatus(StrEnum):
    """Inbound event deduplication outcomes keyed by (consumer, eventId) (ADR-05)."""

    APPLIED = "APPLIED"
    DUPLICATE_IGNORED = "DUPLICATE_IGNORED"
    CONFLICT_FLAGGED = "CONFLICT_FLAGGED"
