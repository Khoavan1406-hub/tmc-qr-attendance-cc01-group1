"""[PACKAGE 4] Asynchronous stubs for cross-cutting modules M10 Identity & RBAC, M11 Audit, and M12 Monitoring."""

from tmc_gate.ports.repositories import IAuditApi, IAuthorizationApi, IHealthApi


class InMemoryAuditStub(IAuditApi):
    """Captures asynchronous audit records without blocking the 2-second scan path."""

    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def append_async(self, record: dict[str, object]) -> None:
        """TODO [Package 4]: Append `record` to `self.records`."""
        raise NotImplementedError("TODO [Package 4]: Implement InMemoryAuditStub.append_async")


class InMemoryHealthStub(IHealthApi):
    """Captures operational health metrics and alerts asynchronously."""

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def report_event_async(self, metric_or_alert: dict[str, object]) -> None:
        """TODO [Package 4]: Append `metric_or_alert` to `self.events`."""
        raise NotImplementedError("TODO [Package 4]: Implement InMemoryHealthStub.report_event_async")


class InMemoryAuthorizationStub(IAuthorizationApi):
    """Checks server-side guard permissions for M3 Gate Intervention (Table 7.2)."""

    def __init__(self, authorized_guards: dict[str, set[str]] | None = None) -> None:
        self._authorized_guards = authorized_guards or {"GUARD-BD-01": {"BD"}, "GUARD-LA-01": {"LA"}}

    def is_authorized(self, actorId: str, role: str, action: str, factoryId: str) -> bool:
        """TODO [Package 4]: Verify `role == "SECURITY_GUARD"` and `factoryId` is in `self._authorized_guards.get(actorId, set())`."""
        raise NotImplementedError("TODO [Package 4]: Implement InMemoryAuthorizationStub.is_authorized")
