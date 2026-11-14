"""Package 1: tracker boundaries, fail-closed behavior, and local access rights."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import localcontext

import pytest

from tmc_gate.domain.enums import Direction, OperatingMode
from tmc_gate.domain.snapshot import EligibilitySnapshot, EmployeeRight

NOW = datetime(2026, 11, 5, 8, 0, 0, 123456, tzinfo=UTC)
PUBLIC_KEY = (
    b"-----BEGIN PUBLIC KEY-----\n"
    b"MCowBQYDK2VwAyEA11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo=\n"
    b"-----END PUBLIC KEY-----\n"
)
ROTATED_PUBLIC_KEY = (
    b"-----BEGIN PUBLIC KEY-----\n"
    b"MCowBQYDK2VwAyEAPUAXw+hDiVqStwqnTRt+vJyYLM8uxJaMwM1V8Sr0Zgw=\n"
    b"-----END PUBLIC KEY-----\n"
)


@pytest.fixture
def snapshot() -> EligibilitySnapshot:
    return EligibilitySnapshot(
        factoryId="BD",
        snapshotVersion=1,
        confirmedWatermark=NOW,
        clockErrorBoundSec=0.5,
        employees={
            "EMP-001": EmployeeRight(
                employeeId="EMP-001",
                active=True,
                allowedFactories={"BD", "LA"},
                allowedLanes={"LANE-BD-01"},
            ),
        },
        lanes={"LANE-BD-01": Direction.ENTRY, "LANE-BD-02": Direction.EXIT},
        verificationKeys=(PUBLIC_KEY,),
    )


@pytest.mark.parametrize("clock_bound_sec", [0, 0.5, 1, 1.999])
@pytest.mark.parametrize(
    ("age_ms", "expected"),
    [
        (0, OperatingMode.NORMAL),
        (59_999, OperatingMode.NORMAL),
        (60_000, OperatingMode.NORMAL),
        (60_001, OperatingMode.DEGRADED),
        (1_799_999, OperatingMode.DEGRADED),
        (1_800_000, OperatingMode.CANNOT_PROCESS),
        (1_800_001, OperatingMode.CANNOT_PROCESS),
        (86_400_000, OperatingMode.CANNOT_PROCESS),
    ],
)
def test_operating_mode_exact_millisecond_boundaries(
    snapshot: EligibilitySnapshot,
    age_ms: int,
    expected: OperatingMode,
    clock_bound_sec: float,
) -> None:
    snapshot.confirmedWatermark = NOW - timedelta(milliseconds=age_ms)
    snapshot.clockErrorBoundSec = clock_bound_sec

    assert snapshot.getOperatingMode(NOW) is expected


@pytest.mark.parametrize("age_ms", [0, 60_000, 60_001, 1_799_999])
@pytest.mark.parametrize(
    "invalid_bound",
    [None, 2, 2.001, 10, -0.001, float("nan"), float("inf"), -float("inf")],
    ids=["unknown", "exactly-2s", "over-2s", "10s", "negative", "nan", "inf", "-inf"],
)
def test_unusable_clock_overrides_even_fresh_or_degraded_watermark(
    snapshot: EligibilitySnapshot, age_ms: int, invalid_bound: float | None
) -> None:
    snapshot.confirmedWatermark = NOW - timedelta(milliseconds=age_ms)
    snapshot.clockErrorBoundSec = invalid_bound

    assert snapshot.getOperatingMode(NOW) is OperatingMode.CANNOT_PROCESS


def test_missing_clock_bound_defaults_to_unknown() -> None:
    snapshot = EligibilitySnapshot("BD", 1, NOW)

    assert snapshot.clockErrorBoundMs is None
    assert snapshot.getOperatingMode(NOW) is OperatingMode.CANNOT_PROCESS


@pytest.mark.parametrize("invalid_bound", [True, False, "0.5"])
def test_invalid_clock_input_does_not_become_a_known_bound(
    snapshot: EligibilitySnapshot, invalid_bound: object
) -> None:
    snapshot.clockErrorBoundSec = invalid_bound

    assert snapshot.clockErrorBoundMs is None
    assert snapshot.getOperatingMode(NOW) is OperatingMode.CANNOT_PROCESS


@pytest.mark.parametrize(
    ("seconds", "expected_ms"),
    [(0, 0), (0.001, 1), (0.0001, 1), (1.001, 1001), (1.999, 1999), (1.9991, 2000)],
)
def test_clock_conversion_uses_conservative_integer_milliseconds(
    snapshot: EligibilitySnapshot, seconds: float, expected_ms: int
) -> None:
    snapshot.clockErrorBoundSec = seconds

    assert snapshot.clockErrorBoundMs == expected_ms
    assert type(snapshot.clockErrorBoundMs) is int


def test_fractional_clock_bound_cannot_round_down_to_pass(
    snapshot: EligibilitySnapshot,
) -> None:
    snapshot.clockErrorBoundSec = 1.9991

    assert snapshot.getOperatingMode(NOW) is OperatingMode.CANNOT_PROCESS


def test_clock_conversion_does_not_depend_on_global_decimal_precision(
    snapshot: EligibilitySnapshot,
) -> None:
    snapshot.clockErrorBoundSec = 1.999

    with localcontext() as context:
        context.prec = 1
        assert snapshot.clockErrorBoundMs == 1999
        assert snapshot.getOperatingMode(NOW) is OperatingMode.NORMAL


@pytest.mark.parametrize("age_ms", [0, 60_000, 60_001, 1_799_999])
def test_incomplete_or_discontinuous_snapshot_always_cannot_process(
    snapshot: EligibilitySnapshot, age_ms: int
) -> None:
    snapshot.confirmedWatermark = NOW - timedelta(milliseconds=age_ms)
    snapshot.isCompleteAndContinuous = False

    assert snapshot.getOperatingMode(NOW) is OperatingMode.CANNOT_PROCESS


@pytest.mark.parametrize("invalid_continuity", [None, 1, "true"])
def test_continuity_must_be_explicitly_confirmed(
    snapshot: EligibilitySnapshot, invalid_continuity: object
) -> None:
    snapshot.isCompleteAndContinuous = invalid_continuity

    assert snapshot.getOperatingMode(NOW) is OperatingMode.CANNOT_PROCESS


@pytest.mark.parametrize("invalid_version", [0, -1, None, True, "1", 1.5])
def test_invalid_snapshot_version_cannot_process(
    snapshot: EligibilitySnapshot, invalid_version: object
) -> None:
    snapshot.snapshotVersion = invalid_version

    assert snapshot.getOperatingMode(NOW) is OperatingMode.CANNOT_PROCESS


def test_version_number_does_not_prove_or_disprove_continuity(
    snapshot: EligibilitySnapshot,
) -> None:
    snapshot.snapshotVersion = 42

    assert snapshot.getOperatingMode(NOW) is OperatingMode.NORMAL


@pytest.mark.parametrize(
    ("extra_us", "age_ms", "expected"),
    [
        (999, 60_000, OperatingMode.NORMAL),
        (1000, 60_000, OperatingMode.DEGRADED),
        (999, 1_799_999, OperatingMode.DEGRADED),
        (1000, 1_799_999, OperatingMode.CANNOT_PROCESS),
    ],
)
def test_submillisecond_remainder_is_discarded_without_float_rounding(
    snapshot: EligibilitySnapshot, extra_us: int, age_ms: int, expected: OperatingMode
) -> None:
    snapshot.confirmedWatermark = NOW - timedelta(
        milliseconds=age_ms, microseconds=extra_us
    )

    assert snapshot.getOperatingMode(NOW) is expected


@pytest.mark.parametrize("future_us", [1, 1000, 1_000_000, 2_000_000])
def test_future_watermark_cannot_establish_freshness(
    snapshot: EligibilitySnapshot, future_us: int
) -> None:
    snapshot.confirmedWatermark = NOW + timedelta(microseconds=future_us)

    assert snapshot.getOperatingMode(NOW) is OperatingMode.CANNOT_PROCESS


@pytest.mark.parametrize(
    "naive_now,naive_watermark", [(True, False), (False, True), (True, True)]
)
def test_timestamps_require_known_timezones(
    snapshot: EligibilitySnapshot, naive_now: bool, naive_watermark: bool
) -> None:
    now = NOW.replace(tzinfo=None) if naive_now else NOW
    if naive_watermark:
        snapshot.confirmedWatermark = NOW.replace(tzinfo=None)

    assert snapshot.getOperatingMode(now) is OperatingMode.CANNOT_PROCESS


@pytest.mark.parametrize("invalid_timestamp", [None, "2026-11-05T08:00:00Z"])
def test_missing_or_invalid_timestamp_cannot_process(
    snapshot: EligibilitySnapshot, invalid_timestamp: object
) -> None:
    assert snapshot.getOperatingMode(invalid_timestamp) is OperatingMode.CANNOT_PROCESS
    snapshot.confirmedWatermark = invalid_timestamp
    assert snapshot.getOperatingMode(NOW) is OperatingMode.CANNOT_PROCESS


@pytest.mark.parametrize(
    ("age_ms", "expected"),
    [
        (60_000, OperatingMode.NORMAL),
        (60_001, OperatingMode.DEGRADED),
        (1_800_000, OperatingMode.CANNOT_PROCESS),
    ],
)
def test_different_timezone_offsets_represent_the_same_elapsed_time(
    snapshot: EligibilitySnapshot, age_ms: int, expected: OperatingMode
) -> None:
    local_zone = timezone(timedelta(hours=7))
    snapshot.confirmedWatermark = (NOW - timedelta(milliseconds=age_ms)).astimezone(
        local_zone
    )

    assert snapshot.getOperatingMode(NOW) is expected
    assert snapshot.getOperatingMode(NOW.astimezone(local_zone)) is expected


class _RepeatedHourTimezone(tzinfo):
    """Model a repeated hour without depending on the host's timezone database."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return None if dt is None else timedelta(hours=-4 - dt.fold)


@pytest.mark.parametrize(
    ("watermark_minute", "watermark_second", "now_minute", "now_second", "expected"),
    [
        # 01:59:30 -> the repeated 01:00:30 is one elapsed minute.
        (59, 30, 0, 30, OperatingMode.NORMAL),
        # Identical wall times occur an hour apart (fold=0/1).
        (15, 0, 15, 0, OperatingMode.CANNOT_PROCESS),
    ],
)
def test_elapsed_time_is_correct_across_repeated_dst_hour(
    snapshot: EligibilitySnapshot,
    watermark_minute: int,
    watermark_second: int,
    now_minute: int,
    now_second: int,
    expected: OperatingMode,
) -> None:
    zone = _RepeatedHourTimezone()
    snapshot.confirmedWatermark = datetime(
        2026, 11, 1, 1, watermark_minute, watermark_second, tzinfo=zone, fold=0
    )
    now = datetime(2026, 11, 1, 1, now_minute, now_second, tzinfo=zone, fold=1)

    assert snapshot.getOperatingMode(now) is expected


def test_very_old_watermark_uses_full_elapsed_days(
    snapshot: EligibilitySnapshot,
) -> None:
    snapshot.confirmedWatermark = datetime.min.replace(tzinfo=UTC)

    assert (
        snapshot.getOperatingMode(datetime.max.replace(tzinfo=UTC))
        is OperatingMode.CANNOT_PROCESS
    )


def test_unrepresentable_utc_timestamp_cannot_process(
    snapshot: EligibilitySnapshot,
) -> None:
    snapshot.confirmedWatermark = datetime.min.replace(
        tzinfo=timezone(timedelta(hours=7))
    )

    assert snapshot.getOperatingMode(NOW) is OperatingMode.CANNOT_PROCESS


def test_mode_recovers_only_after_clock_and_continuity_are_known_good(
    snapshot: EligibilitySnapshot,
) -> None:
    snapshot.clockErrorBoundSec = None
    snapshot.isCompleteAndContinuous = False
    assert snapshot.getOperatingMode(NOW) is OperatingMode.CANNOT_PROCESS

    snapshot.clockErrorBoundSec = 1.999
    assert snapshot.getOperatingMode(NOW) is OperatingMode.CANNOT_PROCESS

    snapshot.isCompleteAndContinuous = True
    assert snapshot.getOperatingMode(NOW) is OperatingMode.NORMAL


@pytest.mark.parametrize(
    ("employee_id", "lane_id", "expected"),
    [
        ("EMP-001", "LANE-BD-01", (True, "OK")),
        ("EMP-001", "LANE-BD-02", (False, "LANE_NOT_PERMITTED")),
        ("EMP-001", "UNKNOWN", (False, "UNKNOWN_LANE")),
        ("UNKNOWN", "LANE-BD-01", (False, "EMPLOYEE_NOT_FOUND")),
        ("UNKNOWN", "UNKNOWN", (False, "UNKNOWN_LANE")),
        ("", "LANE-BD-01", (False, "EMPLOYEE_NOT_FOUND")),
        ("EMP-001", "", (False, "UNKNOWN_LANE")),
        ("emp-001", "LANE-BD-01", (False, "EMPLOYEE_NOT_FOUND")),
        ("EMP-001", "lane-bd-01", (False, "UNKNOWN_LANE")),
    ],
)
def test_employee_and_lane_lookup_returns_contract_reasons(
    snapshot: EligibilitySnapshot,
    employee_id: str,
    lane_id: str,
    expected: tuple[bool, str],
) -> None:
    assert snapshot.isEmployeeEligible(employee_id, lane_id) == expected


@pytest.mark.parametrize("active", [False, None, 1, "true"])
def test_only_explicitly_active_employee_is_eligible(
    snapshot: EligibilitySnapshot, active: object
) -> None:
    snapshot.employees["EMP-001"].active = active

    assert snapshot.isEmployeeEligible("EMP-001", "LANE-BD-01") == (
        False,
        "EMPLOYEE_SUSPENDED_OR_INACTIVE",
    )


@pytest.mark.parametrize("factories", [set(), {"LA"}, {"bd"}])
def test_factory_must_be_explicitly_permitted(
    snapshot: EligibilitySnapshot, factories: set[str]
) -> None:
    snapshot.employees["EMP-001"].allowedFactories = factories

    assert snapshot.isEmployeeEligible("EMP-001", "LANE-BD-01") == (
        False,
        "FACTORY_NOT_PERMITTED",
    )


def test_lane_permission_cannot_override_factory_denial(
    snapshot: EligibilitySnapshot,
) -> None:
    snapshot.factoryId = "OTHER-FACTORY"

    assert snapshot.isEmployeeEligible("EMP-001", "LANE-BD-01") == (
        False,
        "FACTORY_NOT_PERMITTED",
    )


def test_empty_lane_permissions_allow_all_configured_lanes_only(
    snapshot: EligibilitySnapshot,
) -> None:
    snapshot.employees["EMP-001"].allowedLanes.clear()

    assert snapshot.isEmployeeEligible("EMP-001", "LANE-BD-01") == (True, "OK")
    assert snapshot.isEmployeeEligible("EMP-001", "LANE-BD-02") == (True, "OK")
    assert snapshot.isEmployeeEligible("EMP-001", "UNKNOWN") == (False, "UNKNOWN_LANE")


def test_unrestricted_lanes_still_require_factory_permission(
    snapshot: EligibilitySnapshot,
) -> None:
    snapshot.employees["EMP-001"].allowedLanes.clear()
    snapshot.employees["EMP-001"].allowedFactories.clear()

    assert snapshot.isEmployeeEligible("EMP-001", "LANE-BD-01") == (
        False,
        "FACTORY_NOT_PERMITTED",
    )


def test_explicit_right_cannot_grant_an_unconfigured_lane(
    snapshot: EligibilitySnapshot,
) -> None:
    snapshot.employees["EMP-001"].allowedLanes.add("UNKNOWN")

    assert snapshot.isEmployeeEligible("EMP-001", "UNKNOWN") == (False, "UNKNOWN_LANE")


def test_entry_and_exit_lanes_use_the_same_permission_rules(
    snapshot: EligibilitySnapshot,
) -> None:
    snapshot.employees["EMP-001"].allowedLanes.add("LANE-BD-02")

    assert snapshot.isEmployeeEligible("EMP-001", "LANE-BD-02") == (True, "OK")


def test_denial_reason_precedence_matches_the_existing_contract(
    snapshot: EligibilitySnapshot,
) -> None:
    right = snapshot.employees["EMP-001"]
    right.active = False
    right.allowedFactories.clear()
    right.allowedLanes = {"ANOTHER-LANE"}

    assert snapshot.isEmployeeEligible("EMP-001", "UNKNOWN") == (False, "UNKNOWN_LANE")
    assert snapshot.isEmployeeEligible("EMP-001", "LANE-BD-01") == (
        False,
        "EMPLOYEE_SUSPENDED_OR_INACTIVE",
    )
    right.active = True
    assert snapshot.isEmployeeEligible("EMP-001", "LANE-BD-01") == (
        False,
        "FACTORY_NOT_PERMITTED",
    )
    right.allowedFactories.add("BD")
    assert snapshot.isEmployeeEligible("EMP-001", "LANE-BD-01") == (
        False,
        "LANE_NOT_PERMITTED",
    )


def test_right_cannot_be_reused_under_another_employee_id(
    snapshot: EligibilitySnapshot,
) -> None:
    snapshot.employees["OTHER"] = snapshot.employees["EMP-001"]

    assert snapshot.isEmployeeEligible("OTHER", "LANE-BD-01") == (
        False,
        "EMPLOYEE_NOT_FOUND",
    )


def test_empty_snapshot_grants_no_permissions() -> None:
    snapshot = EligibilitySnapshot("BD", 1, NOW, clockErrorBoundSec=0)

    assert snapshot.isEmployeeEligible("EMP-001", "LANE-BD-01") == (
        False,
        "UNKNOWN_LANE",
    )
    snapshot.lanes["LANE-BD-01"] = Direction.ENTRY
    assert snapshot.isEmployeeEligible("EMP-001", "LANE-BD-01") == (
        False,
        "EMPLOYEE_NOT_FOUND",
    )


def test_eligibility_and_freshness_are_separate_checks(
    snapshot: EligibilitySnapshot,
) -> None:
    snapshot.clockErrorBoundSec = None
    snapshot.confirmedWatermark = NOW - timedelta(hours=1)

    assert snapshot.getOperatingMode(NOW) is OperatingMode.CANNOT_PROCESS
    assert snapshot.isEmployeeEligible("EMP-001", "LANE-BD-01") == (True, "OK")


def test_mode_does_not_replace_the_separate_qr_signature_check(
    snapshot: EligibilitySnapshot,
) -> None:
    snapshot.verificationKeys = ()
    snapshot.publicKeyPem = b""

    assert snapshot.getOperatingMode(NOW) is OperatingMode.NORMAL


def test_snapshot_and_employee_default_collections_are_independent() -> None:
    first = EligibilitySnapshot("BD", 1, NOW)
    second = EligibilitySnapshot("BD", 2, NOW)
    first_right = EmployeeRight("EMP-001", True)
    second_right = EmployeeRight("EMP-002", True)

    first_right.allowedFactories.add("BD")
    first_right.allowedLanes.add("LANE-BD-01")
    first.employees["EMP-001"] = first_right
    first.lanes["LANE-BD-01"] = Direction.ENTRY

    assert second.employees == {}
    assert second.lanes == {}
    assert second_right.allowedFactories == set()
    assert second_right.allowedLanes == set()


def test_verification_keys_support_multiple_public_keys() -> None:
    snapshot = EligibilitySnapshot(
        "BD", 1, NOW, verificationKeys=(PUBLIC_KEY, ROTATED_PUBLIC_KEY)
    )

    assert snapshot.verificationKeys == (PUBLIC_KEY, ROTATED_PUBLIC_KEY)
    assert snapshot.publicKeyPem == PUBLIC_KEY


def test_legacy_single_key_constructor_populates_verification_keys() -> None:
    # Preserve the positional constructor used by existing consumers as well.
    snapshot = EligibilitySnapshot("BD", 1, NOW, 0.5, True, PUBLIC_KEY)

    assert snapshot.verificationKeys == (PUBLIC_KEY,)
    assert snapshot.publicKeyPem == PUBLIC_KEY
    assert snapshot.getOperatingMode(NOW) is OperatingMode.NORMAL


def test_explicit_legacy_key_can_select_a_key_in_the_rotation_set() -> None:
    snapshot = EligibilitySnapshot(
        "BD",
        1,
        NOW,
        publicKeyPem=ROTATED_PUBLIC_KEY,
        verificationKeys=(PUBLIC_KEY, ROTATED_PUBLIC_KEY),
    )

    assert snapshot.publicKeyPem == ROTATED_PUBLIC_KEY
    assert snapshot.verificationKeys == (PUBLIC_KEY, ROTATED_PUBLIC_KEY)


def test_conflicting_legacy_key_and_verification_keys_are_rejected() -> None:
    with pytest.raises(
        ValueError, match="publicKeyPem must be one of verificationKeys"
    ):
        EligibilitySnapshot(
            "BD",
            1,
            NOW,
            publicKeyPem=ROTATED_PUBLIC_KEY,
            verificationKeys=(PUBLIC_KEY,),
        )


def test_verification_keys_do_not_alias_a_supplied_mutable_collection() -> None:
    keys = [PUBLIC_KEY]
    snapshot = EligibilitySnapshot("BD", 1, NOW, verificationKeys=keys)
    keys.append(ROTATED_PUBLIC_KEY)

    assert snapshot.verificationKeys == (PUBLIC_KEY,)


def test_snapshot_with_no_public_keys_holds_an_empty_collection() -> None:
    snapshot = EligibilitySnapshot("BD", 1, NOW)

    assert snapshot.verificationKeys == ()
    assert snapshot.publicKeyPem == b""


def test_checks_are_repeatable_and_do_not_mutate_snapshot(
    snapshot: EligibilitySnapshot,
) -> None:
    before = deepcopy(snapshot)

    for age_ms, expected in [
        (0, OperatingMode.NORMAL),
        (60_001, OperatingMode.DEGRADED),
        (1_800_000, OperatingMode.CANNOT_PROCESS),
        (0, OperatingMode.NORMAL),
    ]:
        assert (
            snapshot.getOperatingMode(NOW + timedelta(milliseconds=age_ms)) is expected
        )
        assert snapshot.isEmployeeEligible("EMP-001", "LANE-BD-01") == (True, "OK")
        assert snapshot.isEmployeeEligible("EMP-001", "UNKNOWN") == (
            False,
            "UNKNOWN_LANE",
        )
        assert snapshot == before


def test_replacing_snapshot_preserves_public_keys_and_leaves_original_unchanged(
    snapshot: EligibilitySnapshot,
) -> None:
    updated = replace(
        snapshot, snapshotVersion=2, confirmedWatermark=NOW + timedelta(seconds=1)
    )

    assert updated.verificationKeys == snapshot.verificationKeys
    assert updated.publicKeyPem == snapshot.publicKeyPem
    assert updated.getOperatingMode(NOW + timedelta(seconds=1)) is OperatingMode.NORMAL
    assert snapshot.snapshotVersion == 1
    assert snapshot.confirmedWatermark == NOW
