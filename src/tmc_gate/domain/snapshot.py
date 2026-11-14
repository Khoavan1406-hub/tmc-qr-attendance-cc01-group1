"""[PACKAGE 1] Factory-local EligibilitySnapshot entity and freshness rules (§4.1.6, §5.7.2, I-10, ADR-06)."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import ClassVar

from tmc_gate.domain.enums import Direction, OperatingMode


@dataclass
class EmployeeRight:
    """Local access right entry for an employee at a factory."""

    employeeId: str
    active: bool
    allowedFactories: set[str] = field(default_factory=set)
    allowedLanes: set[str] = field(default_factory=set)


@dataclass
class EligibilitySnapshot:
    """Factory-local rights and freshness rules (Package 1 tracker, I-10, ADR-06).

    A usable snapshot is NORMAL through 60_000 ms, DEGRADED below 1_800_000 ms,
    and CANNOT_PROCESS from 1_800_000 ms onward. Continuity and a known clock
    error bound below 2_000 ms are prerequisites, independent of watermark age.

    ``verificationKeys`` is a tuple of public-key PEM bytes, including any keys
    retained during rotation. No private keys belong here. ``publicKeyPem`` is
    retained for existing callers: construction with one key populates the
    tuple, and construction with the tuple alone exposes its first key through
    the legacy field. Publish a replacement snapshot when changing keys.

    The existing seconds-based clock input remains compatible with the demo.
    It defaults to unknown; callers must supply a measured bound explicitly.
    Consumers can read its conservative integer form via ``clockErrorBoundMs``.
    """

    factoryId: str
    snapshotVersion: int
    confirmedWatermark: datetime
    clockErrorBoundSec: float | None = None
    isCompleteAndContinuous: bool = True
    publicKeyPem: bytes = b""
    employees: dict[str, EmployeeRight] = field(default_factory=dict)
    lanes: dict[str, Direction] = field(default_factory=dict)
    verificationKeys: tuple[bytes, ...] = field(default_factory=tuple, kw_only=True)

    NORMAL_WATERMARK_LIMIT_MS: ClassVar[int] = 60_000
    DEGRADED_WATERMARK_LIMIT_MS: ClassVar[int] = 1_800_000
    MAX_CLOCK_ERROR_BOUND_MS: ClassVar[int] = 2_000

    # Legacy units remain available to callers, but are not dataclass fields.
    NORMAL_WATERMARK_LIMIT_SEC: ClassVar[int] = 60
    DEGRADED_WATERMARK_LIMIT_SEC: ClassVar[int] = 1800
    MAX_CLOCK_ERROR_BOUND_SEC: ClassVar[int] = 2

    def __post_init__(self) -> None:
        self.verificationKeys = tuple(self.verificationKeys)
        if self.publicKeyPem:
            if not self.verificationKeys:
                self.verificationKeys = (self.publicKeyPem,)
            elif self.publicKeyPem not in self.verificationKeys:
                raise ValueError("publicKeyPem must be one of verificationKeys")
        elif self.verificationKeys:
            self.publicKeyPem = self.verificationKeys[0]

    @property
    def clockErrorBoundMs(self) -> int | None:
        """Convert the legacy bound to integer ms; unknown/invalid stays unknown.

        Fractional milliseconds round upward so precision loss never understates
        the error bound. Decimal conversion avoids binary-float boundary errors.
        """
        bound = self.clockErrorBoundSec
        if isinstance(bound, bool) or not isinstance(bound, (int, float)):
            return None
        seconds = Decimal(str(bound))
        if not seconds.is_finite() or seconds < 0:
            return None
        numerator, denominator = seconds.as_integer_ratio()
        return (numerator * 1000 + denominator - 1) // denominator

    def getOperatingMode(self, now: datetime) -> OperatingMode:
        """Evaluate freshness without I/O, reading the system clock, or mutation.

        Watermark age is elapsed UTC time in whole integer milliseconds; any
        sub-millisecond remainder is discarded. Clock uncertainty is checked
        separately and is not added to that age. Missing/naive timestamps and
        future watermarks cannot establish a trustworthy age, so fail closed.
        """
        if (
            self.isCompleteAndContinuous is not True
            or type(self.snapshotVersion) is not int
            or self.snapshotVersion <= 0
        ):
            return OperatingMode.CANNOT_PROCESS

        clock_error_ms = self.clockErrorBoundMs
        if clock_error_ms is None or clock_error_ms >= self.MAX_CLOCK_ERROR_BOUND_MS:
            return OperatingMode.CANNOT_PROCESS

        watermark = self.confirmedWatermark
        if not isinstance(now, datetime) or not isinstance(watermark, datetime):
            return OperatingMode.CANNOT_PROCESS
        if now.utcoffset() is None or watermark.utcoffset() is None:
            return OperatingMode.CANNOT_PROCESS

        try:
            # Normalize first: subtraction in one DST timezone otherwise uses
            # wall time and can ignore offset changes and the datetime fold.
            age = now.astimezone(UTC) - watermark.astimezone(UTC)
        except OverflowError:
            return OperatingMode.CANNOT_PROCESS
        age_us = (age.days * 86_400 + age.seconds) * 1_000_000 + age.microseconds
        if age_us < 0:
            return OperatingMode.CANNOT_PROCESS
        age_ms = age_us // 1000

        if age_ms <= self.NORMAL_WATERMARK_LIMIT_MS:
            return OperatingMode.NORMAL
        if age_ms < self.DEGRADED_WATERMARK_LIMIT_MS:
            return OperatingMode.DEGRADED
        return OperatingMode.CANNOT_PROCESS

    def isEmployeeEligible(self, employeeId: str, laneId: str) -> tuple[bool, str]:
        """Check whether the employee is active and permitted to use the given lane at this factory.

        Keep the contract's lane-first reason precedence. Empty allowedLanes
        means all configured lanes at an explicitly permitted factory; empty
        allowedFactories grants no factories. This check is independent of
        freshness and QR verification, which AccessDecision coordinates.
        """
        if laneId not in self.lanes:
            return False, "UNKNOWN_LANE"
        right = self.employees.get(employeeId)
        if right is None or right.employeeId != employeeId:
            return False, "EMPLOYEE_NOT_FOUND"
        if right.active is not True:
            return False, "EMPLOYEE_SUSPENDED_OR_INACTIVE"
        if self.factoryId not in right.allowedFactories:
            return False, "FACTORY_NOT_PERMITTED"
        if right.allowedLanes and laneId not in right.allowedLanes:
            return False, "LANE_NOT_PERMITTED"
        return True, "OK"
