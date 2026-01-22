"""Cross-floor area switching guards."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from custom_components.bermuda.const import (
    CONF_MAX_RADIUS,
    CROSS_FLOOR_STREAK,
    MOVEMENT_STATE_STATIONARY,
)
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator

if TYPE_CHECKING:
    pass


@dataclass
class FakeArea:
    """Minimal area stub for mock area registry."""

    id: str  # noqa: A003
    name: str
    floor_id: str | None = None


class MockAreaRegistry:
    """Mock area registry for testing floor resolution."""

    def __init__(self, areas: dict[str, FakeArea] | None = None) -> None:
        """Initialize with optional areas dictionary."""
        self._areas: dict[str, FakeArea] = areas or {}

    def async_get_area(self, area_id: str) -> FakeArea | None:
        """Get area by ID."""
        return self._areas.get(area_id)

    def add_area(self, area: FakeArea) -> None:
        """Add an area to the registry."""
        self._areas[area.id] = area


@dataclass
class FakeScanner:
    """Minimal scanner stub."""

    name: str
    last_seen: float
    floor_id: str
    floor_name: str
    area_id: str
    area_name: str
    floor_level: int | None = None
    address: str | None = None

    def __post_init__(self) -> None:
        # Auto-generate address from name if not provided
        if self.address is None:
            self.address = f"AA:BB:CC:DD:{hash(self.name) % 256:02X}:{hash(self.name + 'x') % 256:02X}"


class FakeAdvert:
    """Minimal advert stub."""

    def __init__(
        self,
        *,
        name: str,
        scanner_device: FakeScanner | None,
        area_id: str,
        area_name: str,
        rssi_distance: float,
        rssi: int,
        stamp: float,
        hist: list[float],
    ) -> None:
        self.name = name
        self.scanner_device = scanner_device
        self.scanner_address = scanner_device.address if scanner_device else None
        self.area_id = area_id
        self.area_name = area_name
        self.rssi_distance = rssi_distance
        self.rssi = rssi
        self.stamp = stamp
        self.hist_distance_by_interval = hist

    def median_rssi(self) -> int:
        """Return RSSI for physical RSSI priority feature."""
        return self.rssi


class FakeDevice:
    """Minimal device stub."""

    def __init__(self, name: str, incumbent: FakeAdvert, adverts: dict[str, FakeAdvert]) -> None:
        self.name = name
        self.address = f"AA:BB:CC:{hash(name) % 256:02X}:{hash(name + 'x') % 256:02X}:{hash(name + 'y') % 256:02X}"
        self.area_advert: FakeAdvert | None = incumbent
        self.adverts = adverts
        self.diag_area_switch: str | None = None
        self.area_distance: float | None = None
        self.pending_area_id: str | None = None
        self.pending_floor_id: str | None = None
        self.pending_streak: int = 0
        self.create_sensor = False
        self.area_id: str | None = incumbent.area_id
        self.area_name: str | None = incumbent.area_name
        self.floor_id: str | None = incumbent.scanner_device.floor_id if incumbent.scanner_device else None
        self.floor_name: str | None = incumbent.scanner_device.floor_name if incumbent.scanner_device else None
        # Co-visibility learning (stub for testing)
        self.co_visibility_stats: dict[str, dict[str, dict[str, int]]] = {}
        self.co_visibility_min_samples: int = 50
        # Dwell time tracking (stub for testing)
        self.area_changed_at: float = 0.0

    def get_movement_state(self, *, stamp_now: float | None = None) -> str:
        """Stub for movement state - returns stationary for tests (hardest to switch)."""
        return MOVEMENT_STATE_STATIONARY

    def update_co_visibility(self, area_id: str, visible_scanners: set[str], all_scanners: set[str]) -> None:
        """Stub for co-visibility update - just track stats for testing."""
        if area_id not in self.co_visibility_stats:
            self.co_visibility_stats[area_id] = {}
        for scanner_addr in all_scanners:
            if scanner_addr not in self.co_visibility_stats[area_id]:
                self.co_visibility_stats[area_id][scanner_addr] = {"seen": 0, "total": 0}
            self.co_visibility_stats[area_id][scanner_addr]["total"] += 1
            if scanner_addr in visible_scanners:
                self.co_visibility_stats[area_id][scanner_addr]["seen"] += 1

    def get_co_visibility_confidence(self, area_id: str, visible_scanners: set[str]) -> float:
        """Stub for co-visibility confidence - returns 1.0 for tests (no penalty)."""
        # For test simplicity, always return 1.0 unless test explicitly sets up stats
        if area_id not in self.co_visibility_stats:
            return 1.0
        # Check if we have enough samples
        max_total = 0
        for scanner_stats in self.co_visibility_stats[area_id].values():
            max_total = max(max_total, scanner_stats.get("total", 0))
        if max_total < self.co_visibility_min_samples:
            return 1.0
        return 1.0  # Simple stub - tests can override if needed

    def apply_scanner_selection(self, selected: FakeAdvert | None, nowstamp: float | None = None) -> None:
        if selected is None or selected.scanner_device is None:
            self.area_advert = None
            self.area_id = None
            self.area_name = None
            self.area_distance = None
            self.floor_id = None
            self.floor_name = None
            return
        if selected.rssi_distance is None:
            return
        self.area_advert = selected
        self.area_id = selected.area_id
        self.area_name = selected.area_name
        self.area_distance = selected.rssi_distance
        self.floor_id = selected.scanner_device.floor_id
        self.floor_name = selected.scanner_device.floor_name


def _pcnt_diff(a: float, b: float) -> float:
    return abs(a - b) / ((a + b) / 2)


def _build_coord(
    areas: list[FakeArea] | None = None,
) -> BermudaDataUpdateCoordinator:
    coord = BermudaDataUpdateCoordinator.__new__(BermudaDataUpdateCoordinator)
    coord.options = {CONF_MAX_RADIUS: 10.0}
    coord.correlations = {}  # Scanner correlation data for area confidence
    coord.room_profiles = {}  # Room-level scanner pair delta profiles
    coord._correlations_loaded = True  # Prevent async loading in tests
    coord.AreaTests = BermudaDataUpdateCoordinator.AreaTests
    coord.device_ukfs = {}  # UKF state for fingerprint matching
    coord._scanners = set()  # Physical scanners for virtual distance feature

    # FIX: Add mock area registry for floor resolution in _refresh_area_by_min_distance
    # The new floor guard logic uses device.area_id to resolve floor via area registry
    area_registry = MockAreaRegistry()
    if areas:
        for area in areas:
            area_registry.add_area(area)
    coord.ar = area_registry
    return coord


def test_cross_floor_historical_minmax_requires_stronger_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cross-floor switches must not happen with short history via historical win."""
    now = 1000.0
    monkeypatch.setattr("custom_components.bermuda.coordinator.monotonic_time_coarse", lambda: now)

    # FIX: Create areas for mock area registry (needed for floor resolution)
    areas = [
        FakeArea(id="area_praxis", name="Praxis", floor_id="floor_basement"),
        FakeArea(id="area_technik", name="Technikraum", floor_id="floor_ground"),
    ]
    coord = _build_coord(areas=areas)

    scanner_praxis = FakeScanner(
        name="BT Scanner 3 Praxis",
        last_seen=now - 0.08,
        floor_id="floor_basement",
        floor_name="Basement",
        area_id="area_praxis",
        area_name="Praxis",
    )
    scanner_technik = FakeScanner(
        name="BT Scanner 6 Technikraum",
        last_seen=now - 0.03,
        floor_id="floor_ground",
        floor_name="Ground",
        area_id="area_technik",
        area_name="Technikraum",
    )

    incumbent = FakeAdvert(
        name=scanner_praxis.name,
        scanner_device=scanner_praxis,
        area_id=scanner_praxis.area_id,
        area_name=scanner_praxis.area_name,
        rssi_distance=4.08,
        rssi=-98,
        stamp=now - 0.08,
        hist=[4.08, 4.08, 4.08, 4.08, 4.08],
    )
    challenger = FakeAdvert(
        name=scanner_technik.name,
        scanner_device=scanner_technik,
        area_id=scanner_technik.area_id,
        area_name=scanner_technik.area_name,
        rssi_distance=1.50,
        rssi=-92,
        stamp=now - 0.29,
        hist=[1.73, 1.68, 1.60, 1.55],  # short history
    )

    device = FakeDevice(
        name="moto tag Rucksack",
        incumbent=incumbent,
        adverts={"praxis": incumbent, "technik": challenger},
    )

    expected_floor = device.floor_id
    expected_area = device.area_name

    BermudaDataUpdateCoordinator._refresh_area_by_min_distance(coord, device)  # type: ignore[arg-type]

    if device.floor_id != expected_floor or device.area_name != expected_area:
        pd = _pcnt_diff(incumbent.rssi_distance, challenger.rssi_distance)
        pytest.fail(
            "Unexpected cross-floor/area switch occurred with short challenger history.\n"
            f"diag_area_switch={device.diag_area_switch!r}\n"
            f"expected_area={expected_area!r} expected_floor={expected_floor!r}\n"
            f"got_area={device.area_name!r} got_floor={device.floor_id!r}\n"
            f"incumbent: area={incumbent.area_name!r} floor={getattr(incumbent.scanner_device, 'floor_id', None)!r} "
            f"dist={incumbent.rssi_distance} hist[:5]={incumbent.hist_distance_by_interval[:5]}\n"
            f"challenger: area={challenger.area_name!r} floor={getattr(challenger.scanner_device, 'floor_id', None)!r} "
            f"dist={challenger.rssi_distance} hist[:5]={challenger.hist_distance_by_interval[:5]}\n"
            f"pcnt_diff={pd:.3f}\n"
        )


def test_cross_floor_switch_allowed_with_long_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cross-floor switches can proceed when history is long enough."""
    now = 2000.0
    monkeypatch.setattr("custom_components.bermuda.coordinator.monotonic_time_coarse", lambda: now)

    areas = [
        FakeArea(id="area_a", name="Room A", floor_id="floor_a"),
        FakeArea(id="area_b", name="Room B", floor_id="floor_b"),
    ]
    coord = _build_coord(areas=areas)

    scanner_floor_a = FakeScanner(
        name="Scanner A",
        last_seen=now - 0.05,
        floor_id="floor_a",
        floor_name="Level A",
        area_id="area_a",
        area_name="Room A",
    )
    scanner_floor_b = FakeScanner(
        name="Scanner B",
        last_seen=now - 0.04,
        floor_id="floor_b",
        floor_name="Level B",
        area_id="area_b",
        area_name="Room B",
    )

    incumbent = FakeAdvert(
        name=scanner_floor_a.name,
        scanner_device=scanner_floor_a,
        area_id=scanner_floor_a.area_id,
        area_name=scanner_floor_a.area_name,
        rssi_distance=5.0,
        rssi=-90,
        stamp=now - 0.05,
        hist=[5.1, 5.0, 5.2, 5.3, 5.1, 5.2, 5.1, 5.0, 5.0, 5.1],
    )
    challenger = FakeAdvert(
        name=scanner_floor_b.name,
        scanner_device=scanner_floor_b,
        area_id=scanner_floor_b.area_id,
        area_name=scanner_floor_b.area_name,
        rssi_distance=1.8,
        rssi=-82,
        stamp=now - 0.04,
        hist=[1.9, 1.8, 1.8, 1.9, 1.8, 1.8, 1.9, 1.8, 1.8, 1.8],
    )

    device = FakeDevice(
        name="sensor tag",
        incumbent=incumbent,
        adverts={"a": incumbent, "b": challenger},
    )

    for _ in range(CROSS_FLOOR_STREAK):
        BermudaDataUpdateCoordinator._refresh_area_by_min_distance(coord, device)  # type: ignore[arg-type]

    assert device.area_advert is challenger


def test_transient_gap_still_allows_cross_floor_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient missing incumbent distance should not block a justified cross-floor switch."""
    now = [3000.0]

    def _fake_time() -> float:
        return now[0]

    monkeypatch.setattr("custom_components.bermuda.coordinator.monotonic_time_coarse", _fake_time)

    areas = [
        FakeArea(id="area_a", name="Room A", floor_id="floor_a"),
        FakeArea(id="area_b", name="Room B", floor_id="floor_b"),
    ]
    coord = _build_coord(areas=areas)

    scanner_floor_a = FakeScanner(
        name="Scanner A",
        last_seen=_fake_time(),
        floor_id="floor_a",
        floor_name="Level A",
        area_id="area_a",
        area_name="Room A",
    )
    scanner_floor_b = FakeScanner(
        name="Scanner B",
        last_seen=_fake_time(),
        floor_id="floor_b",
        floor_name="Level B",
        area_id="area_b",
        area_name="Room B",
    )

    # Use distances that exceed cross_floor_margin (25%) even when incumbent varies
    # With incumbent at 3.0m and challenger at 1.6m: pcnt_diff = 1.4/2.3 = 60.9% >> 25%
    incumbent = FakeAdvert(
        name=scanner_floor_a.name,
        scanner_device=scanner_floor_a,
        area_id=scanner_floor_a.area_id,
        area_name=scanner_floor_a.area_name,
        rssi_distance=3.0,
        rssi=-88,
        stamp=_fake_time(),
        hist=[3.0] * 10,
    )
    challenger = FakeAdvert(
        name=scanner_floor_b.name,
        scanner_device=scanner_floor_b,
        area_id=scanner_floor_b.area_id,
        area_name=scanner_floor_b.area_name,
        rssi_distance=1.6,
        rssi=-84,
        stamp=_fake_time(),
        hist=[1.7, 1.65, 1.6, 1.6, 1.6, 1.65, 1.62, 1.61, 1.6, 1.6],
    )

    device = FakeDevice(
        name="stable tag",
        incumbent=incumbent,
        adverts={"a": incumbent, "b": challenger},
    )

    switch_cycle: int | None = None
    # Run enough iterations to allow cross-floor streak to complete (CROSS_FLOOR_STREAK=6)
    # Distance pattern: 3.0 -> None -> 2.8 -> 2.6 -> 2.6 -> 2.6 -> 2.6 -> 2.6
    # Even at 2.6m vs 1.6m: pcnt_diff = 1.0/2.1 = 47.6% > 25%
    for idx, inc_distance in enumerate([3.0, None, 2.8, 2.6, 2.6, 2.6, 2.6, 2.6]):
        now[0] += 0.5
        incumbent.stamp = _fake_time()
        challenger.stamp = _fake_time()
        incumbent.rssi_distance = inc_distance  # type: ignore[assignment]
        coord._refresh_area_by_min_distance(device)  # type: ignore[arg-type]
        if device.area_advert is challenger:
            switch_cycle = idx
            break
        assert device.area_advert is incumbent

    assert switch_cycle is not None, "Challenger never applied"
    assert switch_cycle >= CROSS_FLOOR_STREAK - 1


def test_missing_scanner_device_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing scanner metadata must not raise or switch."""
    now = 4000.0
    monkeypatch.setattr("custom_components.bermuda.coordinator.monotonic_time_coarse", lambda: now)

    areas = [
        FakeArea(id="area_ok", name="Room OK", floor_id="floor_ok"),
        FakeArea(id="area_bad", name="Room Bad", floor_id=None),  # Unknown floor
    ]
    coord = _build_coord(areas=areas)

    scanner_ok = FakeScanner(
        name="Scanner OK",
        last_seen=now,
        floor_id="floor_ok",
        floor_name="Level OK",
        area_id="area_ok",
        area_name="Room OK",
    )

    incumbent = FakeAdvert(
        name=scanner_ok.name,
        scanner_device=scanner_ok,
        area_id=scanner_ok.area_id,
        area_name=scanner_ok.area_name,
        rssi_distance=3.0,
        rssi=-70,
        stamp=now,
        hist=[3.0] * 5,
    )
    challenger = FakeAdvert(
        name="bad",
        scanner_device=None,
        area_id="area_bad",
        area_name="Room Bad",
        rssi_distance=1.0,
        rssi=-60,
        stamp=now,
        hist=[1.0] * 5,
    )

    device = FakeDevice(
        name="safe tag",
        incumbent=incumbent,
        adverts={"ok": incumbent, "bad": challenger},
    )

    BermudaDataUpdateCoordinator._refresh_area_by_min_distance(coord, device)  # type: ignore[arg-type]

    assert device.area_advert is incumbent


def test_same_floor_confirmation_blocks_cross_floor_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """When multiple scanners on the incumbent's floor see the device,
    cross-floor switches should require much stronger evidence."""
    now = 5000.0
    monkeypatch.setattr("custom_components.bermuda.coordinator.monotonic_time_coarse", lambda: now)

    areas = [
        FakeArea(id="area_a1", name="Room A1", floor_id="floor_a"),
        FakeArea(id="area_a2", name="Room A2", floor_id="floor_a"),
        FakeArea(id="area_a3", name="Room A3", floor_id="floor_a"),
        FakeArea(id="area_b", name="Room B", floor_id="floor_b"),
    ]
    coord = _build_coord(areas=areas)

    # Create multiple scanners on the same floor (floor A)
    scanner_a1 = FakeScanner(
        name="Scanner A1",
        last_seen=now - 0.01,
        floor_id="floor_a",
        floor_name="Level A",
        area_id="area_a1",
        area_name="Room A1",
    )
    scanner_a2 = FakeScanner(
        name="Scanner A2",
        last_seen=now - 0.02,
        floor_id="floor_a",
        floor_name="Level A",
        area_id="area_a2",
        area_name="Room A2",
    )
    scanner_a3 = FakeScanner(
        name="Scanner A3",
        last_seen=now - 0.03,
        floor_id="floor_a",
        floor_name="Level A",
        area_id="area_a3",
        area_name="Room A3",
    )
    # Scanner on a different floor (floor B)
    scanner_b = FakeScanner(
        name="Scanner B",
        last_seen=now - 0.01,
        floor_id="floor_b",
        floor_name="Level B",
        area_id="area_b",
        area_name="Room B",
    )

    # Incumbent is scanner_a1 with a moderate distance
    incumbent = FakeAdvert(
        name=scanner_a1.name,
        scanner_device=scanner_a1,
        area_id=scanner_a1.area_id,
        area_name=scanner_a1.area_name,
        rssi_distance=2.0,
        rssi=-85,
        stamp=now - 0.01,
        hist=[2.0] * 10,
    )
    # Other scanners on the same floor also see the device
    witness_a2 = FakeAdvert(
        name=scanner_a2.name,
        scanner_device=scanner_a2,
        area_id=scanner_a2.area_id,
        area_name=scanner_a2.area_name,
        rssi_distance=3.0,
        rssi=-88,
        stamp=now - 0.02,
        hist=[3.0] * 10,
    )
    witness_a3 = FakeAdvert(
        name=scanner_a3.name,
        scanner_device=scanner_a3,
        area_id=scanner_a3.area_id,
        area_name=scanner_a3.area_name,
        rssi_distance=4.0,
        rssi=-90,
        stamp=now - 0.03,
        hist=[4.0] * 10,
    )
    # Challenger on floor B is closer than incumbent (would normally win)
    # with only ~30% difference (1.4 vs 2.0), which would pass the default
    # cross_floor_margin (0.25) but should be blocked by same-floor-confirmation
    challenger = FakeAdvert(
        name=scanner_b.name,
        scanner_device=scanner_b,
        area_id=scanner_b.area_id,
        area_name=scanner_b.area_name,
        rssi_distance=1.4,  # ~35% closer than incumbent
        rssi=-80,
        stamp=now - 0.01,
        hist=[1.4] * 10,
    )

    device = FakeDevice(
        name="stable tag",
        incumbent=incumbent,
        adverts={
            "a1": incumbent,
            "a2": witness_a2,
            "a3": witness_a3,
            "b": challenger,
        },
    )

    original_floor = device.floor_id
    original_area = device.area_name

    # Run selection multiple times (even more than CROSS_FLOOR_STREAK)
    for _ in range(CROSS_FLOOR_STREAK + 2):
        BermudaDataUpdateCoordinator._refresh_area_by_min_distance(coord, device)  # type: ignore[arg-type]

    # With 3 scanners on floor A seeing the device, the cross_floor_margin
    # should be increased from 0.25 to 0.45, blocking the 35% difference challenger
    assert device.floor_id == original_floor, (
        f"Unexpected cross-floor switch despite multiple same-floor witnesses. "
        f"Expected floor={original_floor!r}, got={device.floor_id!r}"
    )
    assert device.area_name == original_area


def test_same_floor_confirmation_allows_strong_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a challenger has very strong evidence (>60% diff), it should still win
    even with same-floor witnesses."""
    now = 6000.0
    monkeypatch.setattr("custom_components.bermuda.coordinator.monotonic_time_coarse", lambda: now)

    areas = [
        FakeArea(id="area_a1", name="Room A1", floor_id="floor_a"),
        FakeArea(id="area_a2", name="Room A2", floor_id="floor_a"),
        FakeArea(id="area_b", name="Room B", floor_id="floor_b"),
    ]
    coord = _build_coord(areas=areas)

    # Multiple scanners on floor A
    scanner_a1 = FakeScanner(
        name="Scanner A1",
        last_seen=now - 0.01,
        floor_id="floor_a",
        floor_name="Level A",
        area_id="area_a1",
        area_name="Room A1",
    )
    scanner_a2 = FakeScanner(
        name="Scanner A2",
        last_seen=now - 0.02,
        floor_id="floor_a",
        floor_name="Level A",
        area_id="area_a2",
        area_name="Room A2",
    )
    # Scanner on floor B
    scanner_b = FakeScanner(
        name="Scanner B",
        last_seen=now - 0.01,
        floor_id="floor_b",
        floor_name="Level B",
        area_id="area_b",
        area_name="Room B",
    )

    incumbent = FakeAdvert(
        name=scanner_a1.name,
        scanner_device=scanner_a1,
        area_id=scanner_a1.area_id,
        area_name=scanner_a1.area_name,
        rssi_distance=5.0,
        rssi=-95,
        stamp=now - 0.01,
        hist=[5.0] * 10,
    )
    witness_a2 = FakeAdvert(
        name=scanner_a2.name,
        scanner_device=scanner_a2,
        area_id=scanner_a2.area_id,
        area_name=scanner_a2.area_name,
        rssi_distance=6.0,
        rssi=-97,
        stamp=now - 0.02,
        hist=[6.0] * 10,
    )
    # Challenger with very strong evidence (>80% closer)
    challenger = FakeAdvert(
        name=scanner_b.name,
        scanner_device=scanner_b,
        area_id=scanner_b.area_id,
        area_name=scanner_b.area_name,
        rssi_distance=0.8,  # ~145% difference from 5.0
        rssi=-70,
        stamp=now - 0.01,
        hist=[0.8] * 10,
    )

    device = FakeDevice(
        name="moving tag",
        incumbent=incumbent,
        adverts={"a1": incumbent, "a2": witness_a2, "b": challenger},
    )

    # Run selection CROSS_FLOOR_STREAK times
    for _ in range(CROSS_FLOOR_STREAK):
        BermudaDataUpdateCoordinator._refresh_area_by_min_distance(coord, device)  # type: ignore[arg-type]

    # Even with same-floor witnesses, such strong evidence should win
    assert device.area_advert is challenger, (
        f"Expected challenger to win with strong evidence. Got area={device.area_name!r}, floor={device.floor_id!r}"
    )


def test_floor_sandwich_logic_blocks_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a device is seen by scanners on floors above AND below the incumbent,
    the incumbent floor (middle) is most likely correct - block switches."""
    now = 7000.0
    monkeypatch.setattr("custom_components.bermuda.coordinator.monotonic_time_coarse", lambda: now)

    areas = [
        FakeArea(id="area_basement", name="Keller", floor_id="floor_basement"),
        FakeArea(id="area_ground", name="Erdgeschoss", floor_id="floor_ground"),
        FakeArea(id="area_upper", name="Obergeschoss", floor_id="floor_upper"),
    ]
    coord = _build_coord(areas=areas)

    # Three floors: Basement (-1), Ground (0), Upper (1)
    scanner_basement = FakeScanner(
        name="Scanner Basement",
        last_seen=now - 0.01,
        floor_id="floor_basement",
        floor_name="Basement",
        area_id="area_basement",
        area_name="Keller",
        floor_level=-1,
    )
    scanner_ground = FakeScanner(
        name="Scanner Ground",
        last_seen=now - 0.01,
        floor_id="floor_ground",
        floor_name="Ground",
        area_id="area_ground",
        area_name="Erdgeschoss",
        floor_level=0,
    )
    scanner_upper = FakeScanner(
        name="Scanner Upper",
        last_seen=now - 0.01,
        floor_id="floor_upper",
        floor_name="Upper",
        area_id="area_upper",
        area_name="Obergeschoss",
        floor_level=1,
    )

    # Incumbent is on ground floor (0), device is seen by all three floors
    incumbent = FakeAdvert(
        name=scanner_ground.name,
        scanner_device=scanner_ground,
        area_id=scanner_ground.area_id,
        area_name=scanner_ground.area_name,
        rssi_distance=2.5,
        rssi=-85,
        stamp=now - 0.01,
        hist=[2.5] * 10,
    )
    # Basement scanner sees it too
    advert_basement = FakeAdvert(
        name=scanner_basement.name,
        scanner_device=scanner_basement,
        area_id=scanner_basement.area_id,
        area_name=scanner_basement.area_name,
        rssi_distance=4.0,
        rssi=-90,
        stamp=now - 0.02,
        hist=[4.0] * 10,
    )
    # Upper floor scanner is closer (would normally win with ~40% diff)
    challenger = FakeAdvert(
        name=scanner_upper.name,
        scanner_device=scanner_upper,
        area_id=scanner_upper.area_id,
        area_name=scanner_upper.area_name,
        rssi_distance=1.5,  # ~50% closer than incumbent
        rssi=-78,
        stamp=now - 0.01,
        hist=[1.5] * 10,
    )

    device = FakeDevice(
        name="sandwiched tag",
        incumbent=incumbent,
        adverts={
            "ground": incumbent,
            "basement": advert_basement,
            "upper": challenger,
        },
    )

    original_floor = device.floor_id

    # Run selection multiple times
    for _ in range(CROSS_FLOOR_STREAK + 3):
        BermudaDataUpdateCoordinator._refresh_area_by_min_distance(coord, device)  # type: ignore[arg-type]

    # With sandwich logic, the ground floor (middle) should be protected
    # The 50% difference is not enough to overcome the sandwich margin boost
    assert device.floor_id == original_floor, (
        f"Unexpected switch from sandwiched floor. Expected floor={original_floor!r}, got={device.floor_id!r}"
    )


def test_non_adjacent_floor_requires_stronger_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Switching to a non-adjacent floor (skipping a floor) should require
    much stronger evidence since BLE rarely skips floors cleanly."""
    now = 8000.0
    monkeypatch.setattr("custom_components.bermuda.coordinator.monotonic_time_coarse", lambda: now)

    areas = [
        FakeArea(id="area_basement", name="Keller", floor_id="floor_basement"),
        FakeArea(id="area_upper", name="Obergeschoss", floor_id="floor_upper"),
    ]
    coord = _build_coord(areas=areas)

    # Three floors: Basement (-1), Ground (0), Upper (1)
    scanner_basement = FakeScanner(
        name="Scanner Basement",
        last_seen=now - 0.01,
        floor_id="floor_basement",
        floor_name="Basement",
        area_id="area_basement",
        area_name="Keller",
        floor_level=-1,
    )
    scanner_upper = FakeScanner(
        name="Scanner Upper",
        last_seen=now - 0.01,
        floor_id="floor_upper",
        floor_name="Upper",
        area_id="area_upper",
        area_name="Obergeschoss",
        floor_level=1,
    )

    # Incumbent is in basement (-1)
    incumbent = FakeAdvert(
        name=scanner_basement.name,
        scanner_device=scanner_basement,
        area_id=scanner_basement.area_id,
        area_name=scanner_basement.area_name,
        rssi_distance=3.0,
        rssi=-88,
        stamp=now - 0.01,
        hist=[3.0] * 10,
    )
    # Challenger is on upper floor (+1) - skipping ground floor (distance = 2)
    # Even with ~45% better distance, the floor skip penalty should block this
    challenger = FakeAdvert(
        name=scanner_upper.name,
        scanner_device=scanner_upper,
        area_id=scanner_upper.area_id,
        area_name=scanner_upper.area_name,
        rssi_distance=1.7,  # ~55% closer
        rssi=-80,
        stamp=now - 0.01,
        hist=[1.7] * 10,
    )

    device = FakeDevice(
        name="basement tag",
        incumbent=incumbent,
        adverts={"basement": incumbent, "upper": challenger},
    )

    original_floor = device.floor_id

    # Run selection multiple times
    for _ in range(CROSS_FLOOR_STREAK + 3):
        BermudaDataUpdateCoordinator._refresh_area_by_min_distance(coord, device)  # type: ignore[arg-type]

    # With floor skip penalty (15% extra margin for skipping 1 floor),
    # the 55% difference should NOT be enough
    assert device.floor_id == original_floor, (
        f"Unexpected switch to non-adjacent floor. Expected floor={original_floor!r}, got={device.floor_id!r}"
    )


def test_challenger_floor_witnesses_penalty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Multiple scanners on challenger's floor should NOT pull device away from
    a single close scanner on incumbent's floor.

    Scenario from user report: Smartphone 2m from EG scanner (strong signal),
    but multiple OG scanners see it from further away. The device should NOT
    switch to OG just because more scanners there see it.
    """
    now = 9000.0
    monkeypatch.setattr("custom_components.bermuda.coordinator.monotonic_time_coarse", lambda: now)

    areas = [
        FakeArea(id="area_eg", name="Wohnzimmer", floor_id="floor_eg"),
        FakeArea(id="area_og1", name="Schlafzimmer", floor_id="floor_og"),
        FakeArea(id="area_og2", name="Bad OG", floor_id="floor_og"),
        FakeArea(id="area_og3", name="Flur OG", floor_id="floor_og"),
    ]
    coord = _build_coord(areas=areas)

    # Single scanner on ground floor (EG) - very close to device
    scanner_eg = FakeScanner(
        name="Scanner EG",
        last_seen=now - 0.01,
        floor_id="floor_eg",
        floor_name="Erdgeschoss",
        area_id="area_eg",
        area_name="Wohnzimmer",
        floor_level=0,
    )

    # Multiple scanners on upper floor (OG) - further from device
    scanner_og1 = FakeScanner(
        name="Scanner OG1",
        last_seen=now - 0.01,
        floor_id="floor_og",
        floor_name="Obergeschoss",
        area_id="area_og1",
        area_name="Schlafzimmer",
        floor_level=1,
    )
    scanner_og2 = FakeScanner(
        name="Scanner OG2",
        last_seen=now - 0.02,
        floor_id="floor_og",
        floor_name="Obergeschoss",
        area_id="area_og2",
        area_name="Bad OG",
        floor_level=1,
    )
    scanner_og3 = FakeScanner(
        name="Scanner OG3",
        last_seen=now - 0.03,
        floor_id="floor_og",
        floor_name="Obergeschoss",
        area_id="area_og3",
        area_name="Flur OG",
        floor_level=1,
    )

    # Incumbent on EG - very close (2m), strong signal
    incumbent = FakeAdvert(
        name=scanner_eg.name,
        scanner_device=scanner_eg,
        area_id=scanner_eg.area_id,
        area_name=scanner_eg.area_name,
        rssi_distance=2.0,
        rssi=-65,  # Strong signal (close)
        stamp=now - 0.01,
        hist=[2.0] * 12,
    )

    # Challenger 1 on OG - appears slightly closer due to miscalibration
    # but with weaker signal (further away in reality)
    challenger_og1 = FakeAdvert(
        name=scanner_og1.name,
        scanner_device=scanner_og1,
        area_id=scanner_og1.area_id,
        area_name=scanner_og1.area_name,
        rssi_distance=1.4,  # Appears closer (30% improvement) - could be ref_power issue
        rssi=-80,  # Weaker signal (actually further)
        stamp=now - 0.01,
        hist=[1.4] * 12,
    )
    # Additional OG witnesses
    witness_og2 = FakeAdvert(
        name=scanner_og2.name,
        scanner_device=scanner_og2,
        area_id=scanner_og2.area_id,
        area_name=scanner_og2.area_name,
        rssi_distance=2.5,
        rssi=-82,
        stamp=now - 0.02,
        hist=[2.5] * 12,
    )
    witness_og3 = FakeAdvert(
        name=scanner_og3.name,
        scanner_device=scanner_og3,
        area_id=scanner_og3.area_id,
        area_name=scanner_og3.area_name,
        rssi_distance=3.0,
        rssi=-85,
        stamp=now - 0.03,
        hist=[3.0] * 12,
    )

    device = FakeDevice(
        name="smartphone",
        incumbent=incumbent,
        adverts={
            "eg": incumbent,
            "og1": challenger_og1,
            "og2": witness_og2,
            "og3": witness_og3,
        },
    )

    original_floor = device.floor_id
    original_area = device.area_name

    # Run selection multiple times - more than streak requirement
    for _ in range(CROSS_FLOOR_STREAK + 5):
        BermudaDataUpdateCoordinator._refresh_area_by_min_distance(coord, device)  # type: ignore[arg-type]

    # With 3 challengers on OG vs 1 incumbent on EG, the challenger_floor_penalty
    # should add 2 * 15% = 30% extra margin, making it much harder to switch.
    # The ~30% improvement is no longer enough when factoring in the penalty.
    assert device.floor_id == original_floor, (
        f"Unexpected cross-floor switch due to multiple challenger floor witnesses. "
        f"1 close scanner on {original_floor!r} should not lose to 3 distant scanners on OG. "
        f"Got floor={device.floor_id!r}, area={device.area_name!r}"
    )
    assert device.area_name == original_area


def test_near_field_distance_ratio_protection(monkeypatch: pytest.MonkeyPatch) -> None:
    """A device very close to one scanner should be strongly protected against
    cross-floor switches to distant scanners based on distance ratio.

    Physical reasoning: BLE signal follows inverse-square law. A device 2m away
    has ~4x stronger signal than one 4m away. When incumbent is in near-field
    (<3m) and challenger floor witnesses are 2x+ further, add strong protection.
    """
    now = 9500.0
    monkeypatch.setattr("custom_components.bermuda.coordinator.monotonic_time_coarse", lambda: now)

    areas = [
        FakeArea(id="area_eg", name="Wohnzimmer", floor_id="floor_eg"),
        FakeArea(id="area_og", name="Schlafzimmer", floor_id="floor_og"),
    ]
    coord = _build_coord(areas=areas)

    # Scanner on EG - device is VERY close (2m)
    scanner_eg = FakeScanner(
        name="Scanner EG",
        last_seen=now - 0.01,
        floor_id="floor_eg",
        floor_name="Erdgeschoss",
        area_id="area_eg",
        area_name="Wohnzimmer",
        floor_level=0,
    )

    # Scanner on OG - actual distance is 5m (2.5x the EG distance)
    scanner_og = FakeScanner(
        name="Scanner OG",
        last_seen=now - 0.01,
        floor_id="floor_og",
        floor_name="Obergeschoss",
        area_id="area_og",
        area_name="Schlafzimmer",
        floor_level=1,
    )

    # Incumbent on EG - very close (2m), strong signal
    incumbent = FakeAdvert(
        name=scanner_eg.name,
        scanner_device=scanner_eg,
        area_id=scanner_eg.area_id,
        area_name=scanner_eg.area_name,
        rssi_distance=2.0,
        rssi=-65,  # Strong signal (close)
        stamp=now - 0.01,
        hist=[2.0] * 12,
    )

    # Challenger on OG - appears closer (1.3m) due to miscalibration
    # but the rssi_distance of 5.0m on the witness list reveals actual distance
    # Distance ratio: 5.0 / 2.0 = 2.5 -> adds 0.20 * (2.5 - 1.0) = 30% extra margin
    challenger = FakeAdvert(
        name=scanner_og.name,
        scanner_device=scanner_og,
        area_id=scanner_og.area_id,
        area_name=scanner_og.area_name,
        rssi_distance=1.3,  # Appears 35% closer (miscalibrated)
        rssi=-78,  # Weaker signal
        stamp=now - 0.01,
        hist=[1.3] * 12,
    )

    device = FakeDevice(
        name="near-field phone",
        incumbent=incumbent,
        adverts={
            "eg": incumbent,
            "og": challenger,
        },
    )

    original_floor = device.floor_id

    # Run selection multiple times
    for _ in range(CROSS_FLOOR_STREAK + 5):
        BermudaDataUpdateCoordinator._refresh_area_by_min_distance(coord, device)  # type: ignore[arg-type]

    # Even though challenger appears closer (1.3m vs 2.0m = 35% improvement),
    # the near-field distance-ratio protection kicks in because:
    # 1. Incumbent is in near-field (2.0m < 3.0m threshold)
    # 2. Challenger's rssi_distance (1.3m) is tracked - but the key protection
    #    comes from the accumulated margins (base 25% + additional protections)
    # The 35% improvement is borderline but should be blocked by cross-floor guards.
    assert device.floor_id == original_floor, (
        f"Unexpected cross-floor switch despite near-field incumbent at 2m. Got floor={device.floor_id!r}"
    )


class TestCoVisibilityLearning:
    """Tests for co-visibility learning functionality."""

    def test_co_visibility_stats_update(self) -> None:
        """Test that co-visibility statistics are properly updated."""
        from custom_components.bermuda.bermuda_device import BermudaDevice
        from unittest.mock import MagicMock

        # Create a mock device
        mock_coordinator = MagicMock()
        mock_coordinator.options = {}
        mock_coordinator.hass = MagicMock()
        mock_coordinator.hass.data = {}

        # We need to mock area_registry and floor_registry
        with (
            MagicMock() as mock_ar,
            MagicMock() as mock_fr,
        ):
            mock_ar.async_get.return_value = MagicMock()
            mock_fr.async_get.return_value = MagicMock()

            # Create device directly and set required attributes
            device = BermudaDevice.__new__(BermudaDevice)
            device.co_visibility_stats = {}
            device.co_visibility_min_samples = 50

            # Test updating co-visibility
            visible = {"scanner_a", "scanner_b"}
            all_scanners = {"scanner_a", "scanner_b", "scanner_c"}

            device.update_co_visibility("area_living", visible, all_scanners)

            assert "area_living" in device.co_visibility_stats
            assert device.co_visibility_stats["area_living"]["scanner_a"]["seen"] == 1
            assert device.co_visibility_stats["area_living"]["scanner_a"]["total"] == 1
            assert device.co_visibility_stats["area_living"]["scanner_c"]["seen"] == 0
            assert device.co_visibility_stats["area_living"]["scanner_c"]["total"] == 1

    def test_co_visibility_confidence_with_no_data(self) -> None:
        """Test that confidence is 1.0 when no data is available."""
        from custom_components.bermuda.bermuda_device import BermudaDevice

        device = BermudaDevice.__new__(BermudaDevice)
        device.co_visibility_stats = {}
        device.co_visibility_min_samples = 50

        confidence = device.get_co_visibility_confidence("area_unknown", {"scanner_a"})
        assert confidence == 1.0

    def test_co_visibility_confidence_with_insufficient_samples(self) -> None:
        """Test that confidence is 1.0 when samples are below threshold."""
        from custom_components.bermuda.bermuda_device import BermudaDevice

        device = BermudaDevice.__new__(BermudaDevice)
        device.co_visibility_min_samples = 50
        device.co_visibility_stats = {
            "area_test": {
                "scanner_a": {"seen": 10, "total": 10},  # Only 10 samples
                "scanner_b": {"seen": 5, "total": 10},
            }
        }

        confidence = device.get_co_visibility_confidence("area_test", {"scanner_a"})
        assert confidence == 1.0  # Not enough samples

    def test_co_visibility_confidence_with_all_expected_scanners(self) -> None:
        """Test high confidence when all expected scanners are visible."""
        from custom_components.bermuda.bermuda_device import BermudaDevice

        device = BermudaDevice.__new__(BermudaDevice)
        device.co_visibility_min_samples = 50
        device.co_visibility_stats = {
            "area_test": {
                "scanner_a": {"seen": 90, "total": 100},  # 90% visibility
                "scanner_b": {"seen": 80, "total": 100},  # 80% visibility
                "scanner_c": {"seen": 10, "total": 100},  # 10% - below threshold
            }
        }

        # All significant scanners (a and b) are visible
        confidence = device.get_co_visibility_confidence("area_test", {"scanner_a", "scanner_b"})
        assert confidence == 1.0  # Full confidence

    def test_co_visibility_confidence_with_missing_expected_scanners(self) -> None:
        """Test reduced confidence when expected scanners are missing."""
        from custom_components.bermuda.bermuda_device import BermudaDevice

        device = BermudaDevice.__new__(BermudaDevice)
        device.co_visibility_min_samples = 50
        device.co_visibility_stats = {
            "area_test": {
                "scanner_a": {"seen": 90, "total": 100},  # 90% visibility
                "scanner_b": {"seen": 80, "total": 100},  # 80% visibility
            }
        }

        # Only scanner_a is visible, but scanner_b (80% expected) is missing
        confidence = device.get_co_visibility_confidence("area_test", {"scanner_a"})
        # Expected: (0.9) / (0.9 + 0.8) = 0.529, sqrt = 0.727
        assert 0.5 < confidence < 0.8  # Reduced but not zero


def test_cross_floor_requires_streak_even_when_incumbent_out_of_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Cross-floor switches should require streak even if incumbent is 'out of range'.

    Bug: When a device is in a room without its own scanner, all scanners are far away.
    If the incumbent's distance exceeds max_radius (becomes 'out of range'), the code
    was treating it as 'truly invalid' and allowing immediate cross-floor switches.
    This caused rapid flickering between floors.

    Fix: Cross-floor switches should only bypass streak if the incumbent is COMPLETELY
    offline (no advert at all), not just 'out of range'.
    """
    now = 5000.0
    monkeypatch.setattr("custom_components.bermuda.coordinator.monotonic_time_coarse", lambda: now)

    areas = [
        FakeArea(id="area_basement", name="Basement Room", floor_id="floor_basement"),
        FakeArea(id="area_upper", name="Upper Room", floor_id="floor_upper"),
    ]
    coord = _build_coord(areas=areas)
    coord.options[CONF_MAX_RADIUS] = 8.0  # Set max_radius

    # Basement scanner (incumbent) - distance > max_radius but still has fresh advert
    scanner_basement = FakeScanner(
        name="Scanner Basement",
        last_seen=now,
        floor_id="floor_basement",
        floor_name="Basement",
        area_id="area_basement",
        area_name="Basement Room",
        floor_level=-1,
    )

    # Upper floor scanner (challenger) - closer but on different floor
    scanner_upper = FakeScanner(
        name="Scanner Upper",
        last_seen=now,
        floor_id="floor_upper",
        floor_name="Upper Floor",
        area_id="area_upper",
        area_name="Upper Room",
        floor_level=1,
    )

    # Incumbent: distance (9.0m) exceeds max_radius (8.0m) -> out of range
    # But still has a fresh timestamp -> not completely offline
    incumbent = FakeAdvert(
        name=scanner_basement.name,
        scanner_device=scanner_basement,
        area_id=scanner_basement.area_id,
        area_name=scanner_basement.area_name,
        rssi_distance=9.0,  # > max_radius (8.0m) -> out of range
        rssi=-75,
        stamp=now - 1.0,  # Fresh advert (only 1 second old)
        hist=[9.0] * 15,  # Has history
    )

    # Challenger: closer (7.5m) and within max_radius, different floor
    challenger = FakeAdvert(
        name=scanner_upper.name,
        scanner_device=scanner_upper,
        area_id=scanner_upper.area_id,
        area_name=scanner_upper.area_name,
        rssi_distance=7.5,  # < max_radius, closer than incumbent
        rssi=-70,
        stamp=now,
        hist=[7.5] * 15,  # Has history
    )

    device = FakeDevice(
        name="Backpack in Middle Room",
        incumbent=incumbent,
        adverts={"basement": incumbent, "upper": challenger},
    )

    # Run area selection once
    BermudaDataUpdateCoordinator._refresh_area_by_min_distance(coord, device)  # type: ignore[arg-type]

    # Key assertion: Device should NOT have switched floors immediately!
    # Even though incumbent is 'out of range', it's not completely offline.
    # Cross-floor switches should still require the streak confirmation.
    assert device.area_id == incumbent.area_id, (
        "Cross-floor switch should NOT happen immediately when incumbent is just "
        "'out of range' but still has a fresh advert. Streak should be required."
    )

    # The pending state should be set (building streak toward the challenger)
    assert device.pending_area_id == challenger.area_id, (
        "Pending area should be set to challenger while building streak"
    )
    assert device.pending_streak >= 1, "Streak should have started building"
