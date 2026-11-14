"""[PACKAGE 4] Asynchronous stubs for cross-cutting modules M10 Identity & RBAC, M11 Audit, and M12 Monitoring."""

from tmc_gate.ports.repositories import IAuditApi, IAuthorizationApi, IHealthApi


class InMemoryAuditStub(IAuditApi):
    """Captures asynchronous audit records without blocking the 2-second scan path."""

    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def append_async(self, record: dict[str, object]) -> None:
        """Capture audit data in memory; this stub has no external transport."""
        self.records.append(record)


class InMemoryHealthStub(IHealthApi):
    """Captures operational health metrics and alerts asynchronously."""

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def report_event_async(self, metric_or_alert: dict[str, object]) -> None:
        """Capture a metric or alert in memory."""
        self.events.append(metric_or_alert)


class InMemoryAuthorizationStub(IAuthorizationApi):
    """Checks server-side guard permissions for M3 Gate Intervention (Table 7.2)."""

    def __init__(self, authorized_guards: dict[str, set[str]] | None = None) -> None:
        self._authorized_guards = (
            {"GUARD-BD-01": {"BD"}, "GUARD-LA-01": {"LA"}} if authorized_guards is None else authorized_guards
        )

    def is_authorized(self, actorId: str, role: str, action: str, factoryId: str) -> bool:
        """Allow only M3 guard actions within the actor's configured factory scope."""
        return (
            role == "SECURITY_GUARD"
            and action in {"RECORD_ASSISTED", "ASSISTED_OPEN", "EMERGENCY_RELEASE", "EMERGENCY_RESET"}
            and factoryId in self._authorized_guards.get(actorId, set())
        )
