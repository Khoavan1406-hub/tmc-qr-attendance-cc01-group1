"""[PACKAGE 6] Simulated guard sign-in for the M2 demo (IGuardIdentityApi).

Real guard authentication belongs to the central identity provider, which is outside the M2 prototype.
This stub only maps fixed demo session tokens to guard ids so the API can reject anonymous calls.
"""

from tmc_gate.ports.repositories import IGuardIdentityApi

DEMO_GUARD_SESSIONS: dict[str, str] = {
    "demo-session-guard-bd-01": "GUARD-BD-01",
    "demo-session-guard-la-01": "GUARD-LA-01",
}


class DemoGuardSessionStub(IGuardIdentityApi):
    """Resolves fixed demo session tokens; any other token is treated as not signed in."""

    def __init__(self, sessions: dict[str, str] | None = None) -> None:
        self._sessions = dict(DEMO_GUARD_SESSIONS if sessions is None else sessions)

    def resolve_guard(self, sessionToken: str) -> str | None:
        return self._sessions.get(sessionToken)
