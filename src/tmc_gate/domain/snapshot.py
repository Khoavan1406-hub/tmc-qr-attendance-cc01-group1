"""[PACKAGE 1] Factory-local EligibilitySnapshot entity and freshness rules (§4.1.6, §5.7.2, I-10, ADR-06)."""

from dataclasses import dataclass, field
from datetime import datetime

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
    """Versioned local copy of access rights, lane configuration, and watermark at a factory (§4.1.6, §5.7.2).

    Thresholds from Table 5.5 (I-10, ADR-06):
    - Normal age of access-right data: <= 60 seconds from confirmedWatermark -> NORMAL
    - Degraded-mode limit: > 60 seconds and <= 30 minutes (1800 seconds) -> DEGRADED
    - Past 30 minutes, missing continuity, or clock error bound > 2.0s -> CANNOT_PROCESS
    """

    factoryId: str
    snapshotVersion: int
    confirmedWatermark: datetime
    clockErrorBoundSec: float = 0.5
    isCompleteAndContinuous: bool = True
    publicKeyPem: bytes = b""
    employees: dict[str, EmployeeRight] = field(default_factory=dict)
    lanes: dict[str, Direction] = field(default_factory=dict)

    NORMAL_WATERMARK_LIMIT_SEC: float = 60.0
    DEGRADED_WATERMARK_LIMIT_SEC: float = 1800.0
    MAX_CLOCK_ERROR_BOUND_SEC: float = 2.0

    def getOperatingMode(self, now: datetime) -> OperatingMode:
        """Determine whether the factory operates in NORMAL, DEGRADED, or CANNOT_PROCESS mode (§5.7.2).

        TODO [Package 1]:
        1. If `not self.isCompleteAndContinuous` or `self.snapshotVersion <= 0`, return `OperatingMode.CANNOT_PROCESS`.
        2. If `self.clockErrorBoundSec < 0` or `self.clockErrorBoundSec > self.MAX_CLOCK_ERROR_BOUND_SEC`,
           return `OperatingMode.CANNOT_PROCESS`.
        3. Compute effective watermark age:
           `age_seconds = (now - self.confirmedWatermark).total_seconds() + self.clockErrorBoundSec`
        4. If `age_seconds <= self.NORMAL_WATERMARK_LIMIT_SEC` (60s), return `OperatingMode.NORMAL`.
        5. If `age_seconds <= self.DEGRADED_WATERMARK_LIMIT_SEC` (1800s), return `OperatingMode.DEGRADED`.
        6. Otherwise return `OperatingMode.CANNOT_PROCESS`.
        """
        raise NotImplementedError("TODO [Package 1]: Implement EligibilitySnapshot.getOperatingMode")

    def isEmployeeEligible(self, employeeId: str, laneId: str) -> tuple[bool, str]:
        """Check whether the employee is active and permitted to use the given lane at this factory.

        TODO [Package 1]:
        1. If `laneId not in self.lanes`, return `(False, "UNKNOWN_LANE")`.
        2. Look up `right = self.employees.get(employeeId)`. If `None`, return `(False, "EMPLOYEE_NOT_FOUND")`.
        3. If `not right.active`, return `(False, "EMPLOYEE_SUSPENDED_OR_INACTIVE")`.
        4. If `self.factoryId not in right.allowedFactories`, return `(False, "FACTORY_NOT_PERMITTED")`.
        5. If `right.allowedLanes` is non-empty and `laneId not in right.allowedLanes`,
           return `(False, "LANE_NOT_PERMITTED")`.
        6. Otherwise return `(True, "OK")`.
        """
        raise NotImplementedError("TODO [Package 1]: Implement EligibilitySnapshot.isEmployeeEligible")
