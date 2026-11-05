"""Automated Architectural Fitness Functions verifying SOLID and Clean Architecture Dependency Rules.

These tests inspect AST imports and interface contracts directly, so they run and PASS on the
base skeleton as well as on every feature branch Pull Request.
"""

import ast
from pathlib import Path

from tmc_gate.ports.inbound import (
    GateCoreApi,
    IFactoryUpdates,
    IInterventionRequest,
    IScanSubmission,
)
from tmc_gate.ports.turnstile import (
    ICloseGate,
    IControllerStatusQuery,
    IEmergencyGate,
    IOpenGate,
    IPassageEvents,
    ITurnstileControl,
)

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "tmc_gate"


def _collect_imports(folder: Path) -> list[tuple[str, str]]:
    """Return list of (filename, imported_module) for all .py files in folder."""
    imports: list[tuple[str, str]] = []
    for py_file in folder.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append((py_file.name, alias.name))
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append((py_file.name, node.module))
    return imports


def test_domain_layer_has_zero_outward_dependencies() -> None:
    """Dependency Rule: domain/ must not import ports, application, adapters, api, sqlite3, or fastapi."""
    forbidden_prefixes = (
        "tmc_gate.ports",
        "tmc_gate.application",
        "tmc_gate.adapters",
        "tmc_gate.api",
        "sqlite3",
        "fastapi",
    )
    for filename, mod in _collect_imports(SRC_ROOT / "domain"):
        assert not mod.startswith(forbidden_prefixes), (
            f"Architecture violation in domain/{filename}: forbidden import '{mod}'"
        )


def test_ports_layer_depends_only_on_domain() -> None:
    """Dependency Rule: ports/ may only import from domain/ (never application, adapters, api, sqlite3, fastapi)."""
    forbidden_prefixes = (
        "tmc_gate.application",
        "tmc_gate.adapters",
        "tmc_gate.api",
        "sqlite3",
        "fastapi",
    )
    for filename, mod in _collect_imports(SRC_ROOT / "ports"):
        assert not mod.startswith(forbidden_prefixes), (
            f"Architecture violation in ports/{filename}: forbidden import '{mod}'"
        )


def test_application_layer_respects_dip_and_never_imports_adapters_or_api() -> None:
    """DIP (§7.1.5): application/ must depend only on domain/ and ports/, never on adapters/, api/, sqlite3, or fastapi."""
    forbidden_prefixes = (
        "tmc_gate.adapters",
        "tmc_gate.api",
        "sqlite3",
        "fastapi",
    )
    for filename, mod in _collect_imports(SRC_ROOT / "application"):
        assert not mod.startswith(forbidden_prefixes), (
            f"DIP violation in application/{filename}: forbidden import '{mod}'"
        )


def test_interface_segregation_principle_contracts() -> None:
    """ISP (§7.1.4): Verify that GateCoreApi and ITurnstileControl are segregated by consumer role."""
    kiosk_methods = set(dir(IScanSubmission))
    assert "submitScan" in kiosk_methods
    assert "getScanStatus" in kiosk_methods
    assert "requestEmergencyRelease" not in kiosk_methods
    assert "requestEmergencyReset" not in kiosk_methods
    assert "recordIntervention" not in kiosk_methods
    assert "applyEligibilityChanges" not in kiosk_methods

    assert "requestEmergencyReset" in set(dir(IInterventionRequest))

    # IPassageEvents has ONLY onPassage and onLaneClear (§7.1.4)
    passage_methods = {m for m in dir(IPassageEvents) if not m.startswith("_")}
    assert passage_methods == {"onPassage", "onLaneClear"}

    assert issubclass(GateCoreApi, IScanSubmission)
    assert issubclass(GateCoreApi, IInterventionRequest)
    assert issubclass(GateCoreApi, IFactoryUpdates)

    assert issubclass(ITurnstileControl, IOpenGate)
    assert issubclass(ITurnstileControl, ICloseGate)
    assert issubclass(ITurnstileControl, IEmergencyGate)
    assert not issubclass(ITurnstileControl, IPassageEvents)
    assert not issubclass(ITurnstileControl, IControllerStatusQuery)
