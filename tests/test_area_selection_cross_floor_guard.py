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
        self.area_id = incumbent.area_id
        self.area_name = incumbent.area_name
        self.floor_id = incumbent.scanner_device.floor_id
        self.floor_name = incumbent.scanner_device.floor_name

    def apply_scanner_selection(self, selected: FakeAdvert | None) -> None:
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


def test_cross_floor_historical_minmax_requires_stronger_history(monkeypatch):
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

    BermudaDataUpdateCoordinator._refresh_area_by_min_distance(coord, device)

    if device.floor_id != expected_floor or device.area_name != expected_area:
        pd = _pcnt_diff(incumbent.rssi_distance, challenger.rssi_distance)
        pytest.fail(
            "Unexpected cross-floor/area switch occurred with short challenger history.\n"
            f"diag_area_switch={device.diag_area_switch!r}\n"
            f"expected_area={expected_area!r} expected_floor={expected_floor!r}\n"
            f"got_area={device.area_name!r} got_floor={device.floor_id!r}\n"
            f"incumbent: area={incumbent.area_name!r} floor={incumbent.scanner_device.floor_id!r} "
            f"dist={incumbent.rssi_distance} hist[:5]={incumbent.hist_distance_by_interval[:5]}\n"
            f"challenger: area={challenger.area_name!r} floor={challenger.scanner_device.floor_id!r} "
            f"dist={challenger.rssi_distance} hist[:5]={challenger.hist_distance_by_interval[:5]}\n"
            f"pcnt_diff={pd:.3f}\n"
        )


def test_cross_floor_switch_allowed_with_long_history(monkeypatch):
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
        BermudaDataUpdateCoordinator._refresh_area_by_min_distance(coord, device)

    assert device.area_advert is challenger


def test_transient_gap_still_allows_cross_floor_switch(monkeypatch):
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
        incumbent.rssi_distance = inc_distance
        coord._refresh_area_by_min_distance(device)
        if device.area_advert is challenger:
            switch_cycle = idx
            break
        assert device.area_advert is incumbent

    assert switch_cycle is not None, "Challenger never applied"
    assert switch_cycle >= CROSS_FLOOR_STREAK - 1


def test_missing_scanner_device_does_not_crash(monkeypatch):
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
        scanner_device=None,  # type: ignore[arg-type]
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

    BermudaDataUpdateCoordinator._refresh_area_by_min_distance(coord, device)

    assert device.area_advert is incumbent
