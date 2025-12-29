"""Cross-floor area switching guards."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from custom_components.bermuda.const import CONF_MAX_RADIUS, CROSS_FLOOR_STREAK
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator


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

    def update_co_visibility(
        self, area_id: str, visible_scanners: set[str], all_scanners: set[str]
    ) -> None:
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


def _build_coord() -> BermudaDataUpdateCoordinator:
    coord = BermudaDataUpdateCoordinator.__new__(BermudaDataUpdateCoordinator)
    coord.options = {CONF_MAX_RADIUS: 10.0}
    coord.AreaTests = BermudaDataUpdateCoordinator.AreaTests
    return coord


def test_cross_floor_historical_minmax_requires_stronger_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cross-floor switches must not happen with short history via historical win."""
    now = 1000.0
    monkeypatch.setattr("custom_components.bermuda.coordinator.monotonic_time_coarse", lambda: now)

    coord = _build_coord()

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

    coord = _build_coord()

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
    coord = _build_coord()

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

    incumbent = FakeAdvert(
        name=scanner_floor_a.name,
        scanner_device=scanner_floor_a,
        area_id=scanner_floor_a.area_id,
        area_name=scanner_floor_a.area_name,
        rssi_distance=2.2,
        rssi=-88,
        stamp=_fake_time(),
        hist=[2.2] * 10,
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
    for idx, inc_distance in enumerate([2.2, None, 2.1, 2.0]):
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
    coord = _build_coord()

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
    coord = _build_coord()

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
    coord = _build_coord()

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
        f"Expected challenger to win with strong evidence. "
        f"Got area={device.area_name!r}, floor={device.floor_id!r}"
    )


def test_floor_sandwich_logic_blocks_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a device is seen by scanners on floors above AND below the incumbent,
    the incumbent floor (middle) is most likely correct - block switches."""
    now = 7000.0
    monkeypatch.setattr("custom_components.bermuda.coordinator.monotonic_time_coarse", lambda: now)
    coord = _build_coord()

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
        f"Unexpected switch from sandwiched floor. "
        f"Expected floor={original_floor!r}, got={device.floor_id!r}"
    )


def test_non_adjacent_floor_requires_stronger_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Switching to a non-adjacent floor (skipping a floor) should require
    much stronger evidence since BLE rarely skips floors cleanly."""
    now = 8000.0
    monkeypatch.setattr("custom_components.bermuda.coordinator.monotonic_time_coarse", lambda: now)
    coord = _build_coord()

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
        f"Unexpected switch to non-adjacent floor. "
        f"Expected floor={original_floor!r}, got={device.floor_id!r}"
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
        confidence = device.get_co_visibility_confidence(
            "area_test", {"scanner_a", "scanner_b"}
        )
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
