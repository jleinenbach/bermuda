"""
Tests for the Reference Tracker feature.

Reference trackers are stationary BLE devices permanently placed in a specific room.
They provide ground truth for auto-learning fingerprints. The key invariant is that
N trackers in the same room produce exactly ONE learning update per cycle (via median
aggregation), ensuring the learning rate is independent of tracker count.

Test IDs reference the specification in docs/spec_reference_tracker.md.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from custom_components.bermuda.area_selection import (
    AreaSelectionHandler,
    _ReferenceTrackerProxy,
)
from custom_components.bermuda.const import (
    CONF_REFERENCE_TRACKERS,
    EVIDENCE_WINDOW_SECONDS,
    MOVEMENT_STATE_STATIONARY,
    REFERENCE_TRACKER_CONFIDENCE,
    REFERENCE_TRACKER_DEVICE_PREFIX,
)


# =============================================================================
# Fixtures
# =============================================================================


@dataclass
class FakeKalman:
    """Fake Kalman filter for testing."""

    is_initialized: bool = True
    variance: float = 4.0
    last_update_time: float = 1000.0


@dataclass
class FakeAdvert:
    """Fake advert for testing."""

    scanner_address: str | None = None
    rssi: float | None = None
    stamp: float | None = None
    rssi_distance: float = 5.0
    hist_velocity: list[float] = field(default_factory=list)
    rssi_kalman: FakeKalman = field(default_factory=FakeKalman)
    area_id: str | None = None
    area_name: str | None = None
    scanner_device: object = None
    name: str = ""
    hist_distance_by_interval: list[float] = field(default_factory=list)

    def median_rssi(self) -> float:
        """Return RSSI value."""
        return self.rssi if self.rssi is not None else 0.0

    def get_distance_variance(self, nowstamp: float | None = None) -> float:
        """Return fixed distance variance."""
        return 1.0


@dataclass
class FakeRefDevice:
    """Fake device for reference tracker tests."""

    address: str
    name: str
    is_reference_tracker: bool = True
    area_id: str | None = None
    area_name: str | None = None
    area_changed_at: float = 0.0
    adverts: dict[str, Any] = field(default_factory=dict)
    co_visibility_stats: dict[str, Any] = field(default_factory=dict)
    co_visibility_min_samples: int = 50

    def get_movement_state(self, *, stamp_now: float | None = None) -> str:
        """Always stationary."""
        return MOVEMENT_STATE_STATIONARY

    def get_dwell_time(self, *, stamp_now: float | None = None) -> float:
        """Always long dwell."""
        return 86400.0


def _make_handler() -> AreaSelectionHandler:
    """Create handler with mock coordinator."""
    coordinator = MagicMock()
    coordinator.options = {}
    coordinator.correlations = {}
    coordinator.room_profiles = {}
    coordinator.device_ukfs = {}
    coordinator._scanners = set()
    coordinator.ar = None
    coordinator.devices = {}
    handler = AreaSelectionHandler(coordinator)
    return handler


def _make_ref_device(
    address: str,
    area_id: str,
    scanner_rssi: dict[str, float],
    stamp: float = 1000.0,
    name: str | None = None,
) -> FakeRefDevice:
    """Create a FakeRefDevice with adverts populated from scanner_rssi."""
    dev = FakeRefDevice(
        address=address,
        name=name or f"Ref {address}",
        area_id=area_id,
    )
    dev.adverts = {
        addr: FakeAdvert(scanner_address=addr, rssi=rssi, stamp=stamp) for addr, rssi in scanner_rssi.items()
    }
    return dev


# =============================================================================
# TestConfiguration (T-CFG-01 to T-CFG-04)
# =============================================================================


class TestConfiguration:
    """Tests for reference tracker configuration handling."""

    def test_cfg01_empty_options_default(self) -> None:
        """T-CFG-01: Empty CONF_REFERENCE_TRACKERS means no reference trackers."""
        handler = _make_handler()
        handler.coordinator.options = {}

        # No ref trackers configured → no devices flagged
        dev = FakeRefDevice(address="aa:bb:cc:dd:ee:01", name="Dev 1")
        handler.coordinator.devices = {dev.address: dev}

        result = handler._aggregate_reference_tracker_readings(nowstamp=1001.0)
        assert result == {}

    def test_cfg02_device_in_config(self) -> None:
        """T-CFG-02: Device address in CONF_REFERENCE_TRACKERS is flagged."""
        handler = _make_handler()
        dev = _make_ref_device(
            "aa:bb:cc:dd:ee:01",
            "area.kitchen",
            {"scanner_a": -55.0},
        )
        handler.coordinator.devices = {dev.address: dev}

        result = handler._aggregate_reference_tracker_readings(nowstamp=1001.0)
        assert "area.kitchen" in result

    def test_cfg03_unknown_address_ignored(self) -> None:
        """T-CFG-03: Unknown address in config doesn't cause errors."""
        handler = _make_handler()
        handler.coordinator.options = {
            CONF_REFERENCE_TRACKERS: ["zz:zz:zz:zz:zz:zz"],
        }

        # No matching device → no crash, no aggregation
        result = handler._aggregate_reference_tracker_readings(nowstamp=1001.0)
        assert result == {}

    def test_cfg04_missing_config_key(self) -> None:
        """T-CFG-04: Missing CONF_REFERENCE_TRACKERS key → no error."""
        handler = _make_handler()
        # Options dict exists but no reference_trackers key
        handler.coordinator.options = {"some_other_key": "value"}

        diag = handler.get_reference_tracker_diagnostics()
        assert diag["configured_count"] == 0
        assert diag["configured_addresses"] == []


# =============================================================================
# TestAggregation (T-AGG-01 to T-AGG-06)
# =============================================================================


class TestAggregation:
    """Tests for RSSI aggregation across multiple reference trackers."""

    def test_agg01_single_tracker_passthrough(self) -> None:
        """T-AGG-01: Single tracker RSSI values are passed through unchanged."""
        handler = _make_handler()
        dev = _make_ref_device(
            "aa:bb:cc:dd:ee:01",
            "area.kitchen",
            {"scanner_a": -55.0, "scanner_b": -71.0},
        )
        handler.coordinator.devices = {dev.address: dev}

        result = handler._aggregate_reference_tracker_readings(nowstamp=1001.0)

        assert "area.kitchen" in result
        primary_rssi, primary_addr, other, _stamps = result["area.kitchen"]
        assert primary_rssi == -55.0
        assert primary_addr == "scanner_a"
        assert other["scanner_b"] == -71.0

    def test_agg02_three_trackers_median(self) -> None:
        """T-AGG-02: Three trackers produce per-scanner median."""
        handler = _make_handler()
        rssi_sets = [
            {"scanner_a": -52.0, "scanner_b": -71.0},
            {"scanner_a": -58.0, "scanner_b": -68.0},
            {"scanner_a": -55.0, "scanner_b": -73.0},
        ]
        devices: dict[str, Any] = {}
        for i, rssis in enumerate(rssi_sets):
            dev = _make_ref_device(
                f"aa:bb:cc:dd:ee:{i:02x}",
                "area.kitchen",
                rssis,
            )
            devices[dev.address] = dev
        handler.coordinator.devices = devices

        result = handler._aggregate_reference_tracker_readings(nowstamp=1001.0)

        primary_rssi, primary_addr, other, _stamps = result["area.kitchen"]
        # Median of [-52, -58, -55] = -55, Median of [-71, -68, -73] = -71
        assert primary_rssi == statistics.median([-52.0, -58.0, -55.0])
        assert other["scanner_b"] == statistics.median([-71.0, -68.0, -73.0])

    def test_agg03_ten_trackers_single_entry(self) -> None:
        """T-AGG-03: Ten trackers produce exactly one aggregated entry."""
        handler = _make_handler()
        devices: dict[str, Any] = {}
        for i in range(10):
            dev = _make_ref_device(
                f"aa:bb:cc:dd:{i:02x}:ff",
                "area.kitchen",
                {"scanner_a": -55.0 + i * 0.5},
            )
            devices[dev.address] = dev
        handler.coordinator.devices = devices

        result = handler._aggregate_reference_tracker_readings(nowstamp=1001.0)

        # Exactly 1 area entry (not 10)
        assert len(result) == 1
        assert "area.kitchen" in result

    def test_agg04_two_rooms_two_entries(self) -> None:
        """T-AGG-04: Two rooms with trackers produce two entries."""
        handler = _make_handler()
        devices: dict[str, Any] = {}

        for i in range(2):
            dev = _make_ref_device(
                f"aa:bb:cc:00:00:{i:02x}",
                "area.kitchen",
                {"scanner_a": -55.0},
            )
            devices[dev.address] = dev

        for i in range(2):
            dev = _make_ref_device(
                f"aa:bb:cc:11:11:{i:02x}",
                "area.office",
                {"scanner_b": -60.0},
            )
            devices[dev.address] = dev

        handler.coordinator.devices = devices
        result = handler._aggregate_reference_tracker_readings(nowstamp=1001.0)

        assert len(result) == 2
        assert "area.kitchen" in result
        assert "area.office" in result

    def test_agg05_tracker_without_area_skipped(self) -> None:
        """T-AGG-05: Tracker without area_id is skipped."""
        handler = _make_handler()
        dev = FakeRefDevice(
            address="aa:bb:cc:dd:ee:01",
            name="Ref 1",
            area_id=None,  # No area assigned
        )
        dev.adverts = {
            "scanner_a": FakeAdvert(scanner_address="scanner_a", rssi=-55.0, stamp=1000.0),
        }
        handler.coordinator.devices = {dev.address: dev}

        result = handler._aggregate_reference_tracker_readings(nowstamp=1001.0)
        assert result == {}

    def test_agg06_stale_adverts_skipped(self) -> None:
        """T-AGG-06: Trackers with stale adverts are skipped."""
        handler = _make_handler()
        # Stamp is far in the past relative to nowstamp
        stale_stamp = 100.0
        nowstamp = 100.0 + EVIDENCE_WINDOW_SECONDS + 10.0

        dev = _make_ref_device(
            "aa:bb:cc:dd:ee:01",
            "area.kitchen",
            {"scanner_a": -55.0},
            stamp=stale_stamp,
        )
        handler.coordinator.devices = {dev.address: dev}

        result = handler._aggregate_reference_tracker_readings(nowstamp=nowstamp)
        assert result == {}

    def test_agg_primary_scanner_is_strongest(self) -> None:
        """Primary scanner is the one with strongest (least negative) median RSSI."""
        handler = _make_handler()
        dev = _make_ref_device(
            "aa:bb:cc:dd:ee:01",
            "area.kitchen",
            {"scanner_a": -70.0, "scanner_b": -55.0, "scanner_c": -80.0},
        )
        handler.coordinator.devices = {dev.address: dev}

        result = handler._aggregate_reference_tracker_readings(nowstamp=1001.0)

        _primary_rssi, primary_addr, _other, _stamps = result["area.kitchen"]
        assert primary_addr == "scanner_b"  # Strongest signal

    def test_agg_null_rssi_skipped(self) -> None:
        """Adverts with None RSSI are skipped in aggregation."""
        handler = _make_handler()
        dev = FakeRefDevice(
            address="aa:bb:cc:dd:ee:01",
            name="Ref 1",
            area_id="area.kitchen",
        )
        dev.adverts = {
            "scanner_a": FakeAdvert(scanner_address="scanner_a", rssi=None, stamp=1000.0),
        }
        handler.coordinator.devices = {dev.address: dev}

        result = handler._aggregate_reference_tracker_readings(nowstamp=1001.0)
        assert result == {}

    def test_agg_null_stamp_skipped(self) -> None:
        """Adverts with None stamp are skipped in aggregation."""
        handler = _make_handler()
        dev = FakeRefDevice(
            address="aa:bb:cc:dd:ee:01",
            name="Ref 1",
            area_id="area.kitchen",
        )
        dev.adverts = {
            "scanner_a": FakeAdvert(scanner_address="scanner_a", rssi=-55.0, stamp=None),
        }
        handler.coordinator.devices = {dev.address: dev}

        result = handler._aggregate_reference_tracker_readings(nowstamp=1001.0)
        assert result == {}


# =============================================================================
# TestFilterBehavior (T-FLT-01 to T-FLT-06)
# =============================================================================


class TestFilterBehavior:
    """Tests for quality filter interaction with reference trackers."""

    def test_flt01_confidence_passes_gate(self) -> None:
        """T-FLT-01: Reference confidence 0.80 passes the 0.50 gate."""
        assert REFERENCE_TRACKER_CONFIDENCE >= 0.50

    def test_flt02_filter3_bypassed_for_ref_prefix(self) -> None:
        """T-FLT-02: Movement state filter is bypassed for ref: prefix devices."""
        handler = _make_handler()

        # Create a ref: proxy device that would normally fail movement check
        proxy = _ReferenceTrackerProxy(
            address=f"{REFERENCE_TRACKER_DEVICE_PREFIX}area.kitchen",
            name="Reference Tracker (Kitchen)",
            area_id="area.kitchen",
        )

        # The proxy address starts with ref: → movement check skipped
        assert proxy.address.startswith(REFERENCE_TRACKER_DEVICE_PREFIX)
        assert proxy.get_movement_state() == MOVEMENT_STATE_STATIONARY

    def test_flt03_normal_device_not_bypassed(self) -> None:
        """T-FLT-03: Normal devices still go through movement state filter."""
        handler = _make_handler()

        # Track whether _check_movement_state_for_learning was called
        with patch.object(
            handler,
            "_check_movement_state_for_learning",
            return_value="not_stationary",
        ) as mock_check:
            # A normal device (no ref: prefix) should hit the movement check
            device = FakeRefDevice(
                address="aa:bb:cc:dd:ee:01",
                name="Normal Device",
                is_reference_tracker=False,
            )
            device.adverts = {
                "scanner_a": FakeAdvert(scanner_address="scanner_a", rssi=-55.0, stamp=1000.0),
                "scanner_b": FakeAdvert(scanner_address="scanner_b", rssi=-60.0, stamp=1000.0),
            }
            handler._update_device_correlations(
                device=device,  # type: ignore[arg-type]
                area_id="area.kitchen",
                primary_rssi=-55.0,
                primary_scanner_addr="scanner_a",
                other_readings={"scanner_b": -60.0},
                nowstamp=1000.0,
                confidence=0.9,
            )
            mock_check.assert_called_once()

    def test_flt03_ref_device_skips_movement_check(self) -> None:
        """Movement state check is NOT called for ref: prefix devices."""
        handler = _make_handler()

        with patch.object(
            handler,
            "_check_movement_state_for_learning",
            return_value="not_stationary",
        ) as mock_check:
            # A device with ref: prefix should skip the movement check
            proxy = _ReferenceTrackerProxy(
                address=f"{REFERENCE_TRACKER_DEVICE_PREFIX}area.kitchen",
                name="Reference Tracker (Kitchen)",
                area_id="area.kitchen",
            )
            handler._update_device_correlations(
                device=proxy,  # type: ignore[arg-type]
                area_id="area.kitchen",
                primary_rssi=-55.0,
                primary_scanner_addr="scanner_a",
                other_readings={"scanner_b": -60.0},
                nowstamp=1000.0,
                confidence=REFERENCE_TRACKER_CONFIDENCE,
            )
            mock_check.assert_not_called()

    def test_flt_low_confidence_blocks_learning(self) -> None:
        """Low confidence blocks learning even for reference trackers."""
        handler = _make_handler()

        proxy = _ReferenceTrackerProxy(
            address=f"{REFERENCE_TRACKER_DEVICE_PREFIX}area.kitchen",
            name="Reference Tracker (Kitchen)",
            area_id="area.kitchen",
        )

        # Patch the method that would be called if learning proceeds past filters
        with patch.object(handler, "_is_signal_ambiguous", return_value=False):
            # Low confidence should be blocked
            handler._update_device_correlations(
                device=proxy,  # type: ignore[arg-type]
                area_id="area.kitchen",
                primary_rssi=-55.0,
                primary_scanner_addr="scanner_a",
                other_readings={"scanner_b": -60.0},
                nowstamp=1000.0,
                confidence=0.1,  # Below AUTO_LEARNING_MIN_CONFIDENCE
            )

        # No correlations should have been created
        assert f"{REFERENCE_TRACKER_DEVICE_PREFIX}area.kitchen" not in handler.coordinator.correlations


# =============================================================================
# TestLearningRateInvariance (T-RATE)
# =============================================================================


class TestLearningRateInvariance:
    """Tests ensuring N trackers produce same learning rate as 1 tracker.

    The key invariant: median aggregation produces exactly ONE learning update
    per area per cycle, regardless of how many trackers are in the room.
    """

    def test_rate_one_tracker_one_update(self) -> None:
        """T-RATE-01: Single tracker produces exactly one aggregated entry."""
        handler = _make_handler()
        dev = _make_ref_device(
            "aa:bb:cc:dd:ee:01",
            "area.kitchen",
            {"scanner_a": -55.0, "scanner_b": -70.0},
        )
        handler.coordinator.devices = {dev.address: dev}

        result = handler._aggregate_reference_tracker_readings(nowstamp=1001.0)
        assert len(result) == 1

    def test_rate_five_trackers_one_update(self) -> None:
        """T-RATE-02: Five trackers still produce exactly one aggregated entry."""
        handler = _make_handler()
        devices: dict[str, Any] = {}
        for i in range(5):
            dev = _make_ref_device(
                f"aa:bb:cc:dd:{i:02x}:ff",
                "area.kitchen",
                {"scanner_a": -55.0 + i, "scanner_b": -70.0 + i},
            )
            devices[dev.address] = dev
        handler.coordinator.devices = devices

        result = handler._aggregate_reference_tracker_readings(nowstamp=1001.0)

        # Still exactly 1 entry
        assert len(result) == 1
        assert "area.kitchen" in result

    def test_rate_ten_trackers_one_update(self) -> None:
        """T-RATE-03: Ten trackers still produce exactly one aggregated entry."""
        handler = _make_handler()
        devices: dict[str, Any] = {}
        for i in range(10):
            dev = _make_ref_device(
                f"aa:bb:cc:dd:{i:02x}:ff",
                "area.kitchen",
                {"scanner_a": -55.0 + i * 0.3},
            )
            devices[dev.address] = dev
        handler.coordinator.devices = devices

        result = handler._aggregate_reference_tracker_readings(nowstamp=1001.0)
        assert len(result) == 1

    def test_rate_two_rooms_independent(self) -> None:
        """T-RATE-04: Room A (5 trackers) and Room B (1 tracker) both get 1 update."""
        handler = _make_handler()
        devices: dict[str, Any] = {}

        # Room A: 5 trackers
        for i in range(5):
            dev = _make_ref_device(
                f"aa:bb:cc:aa:{i:02x}:ff",
                "area.kitchen",
                {"scanner_a": -55.0 + i},
            )
            devices[dev.address] = dev

        # Room B: 1 tracker
        dev_b = _make_ref_device(
            "aa:bb:cc:bb:00:ff",
            "area.office",
            {"scanner_b": -60.0},
        )
        devices[dev_b.address] = dev_b

        handler.coordinator.devices = devices
        result = handler._aggregate_reference_tracker_readings(nowstamp=1001.0)

        assert len(result) == 2
        assert "area.kitchen" in result
        assert "area.office" in result


# =============================================================================
# TestErrorScenarios (T-ERR)
# =============================================================================


class TestErrorScenarios:
    """Tests for error and edge case handling."""

    def test_err03_tracker_no_adverts(self) -> None:
        """T-ERR-03: Tracker with no adverts (battery dead) is skipped."""
        handler = _make_handler()
        dev = FakeRefDevice(
            address="aa:bb:cc:dd:ee:01",
            name="Dead Tracker",
            area_id="area.kitchen",
        )
        dev.adverts = {}  # No adverts at all
        handler.coordinator.devices = {dev.address: dev}

        result = handler._aggregate_reference_tracker_readings(nowstamp=1001.0)
        assert result == {}

    def test_err04_all_trackers_removed(self) -> None:
        """T-ERR-04: No reference trackers in devices → empty aggregation."""
        handler = _make_handler()
        # Regular device, not a reference tracker
        dev = FakeRefDevice(
            address="aa:bb:cc:dd:ee:01",
            name="Regular Device",
            is_reference_tracker=False,
            area_id="area.kitchen",
        )
        dev.adverts = {
            "scanner_a": FakeAdvert(scanner_address="scanner_a", rssi=-55.0, stamp=1000.0),
        }
        handler.coordinator.devices = {dev.address: dev}

        result = handler._aggregate_reference_tracker_readings(nowstamp=1001.0)
        assert result == {}

    def test_err05_tracker_only_null_adverts(self) -> None:
        """T-ERR-05: Tracker with only null RSSI adverts is skipped."""
        handler = _make_handler()
        dev = FakeRefDevice(
            address="aa:bb:cc:dd:ee:01",
            name="Null Tracker",
            area_id="area.kitchen",
        )
        dev.adverts = {
            "scanner_a": FakeAdvert(scanner_address="scanner_a", rssi=None, stamp=1000.0),
            "scanner_b": FakeAdvert(scanner_address="scanner_b", rssi=None, stamp=1000.0),
        }
        handler.coordinator.devices = {dev.address: dev}

        result = handler._aggregate_reference_tracker_readings(nowstamp=1001.0)
        assert result == {}

    def test_err06_invalid_config_entries(self) -> None:
        """T-ERR-06: Invalid entries in CONF_REFERENCE_TRACKERS don't crash."""
        handler = _make_handler()
        handler.coordinator.options = {
            CONF_REFERENCE_TRACKERS: [None, 123, "", "valid:addr:00:11:22:33"],
        }

        # Should not raise
        diag = handler.get_reference_tracker_diagnostics()
        # Invalid entries still show in diagnostics as configured
        assert diag["configured_count"] == 4


# =============================================================================
# TestBackwardCompatibility (T-BWC)
# =============================================================================


class TestBackwardCompatibility:
    """Tests ensuring backward compatibility with existing installations."""

    def test_bwc01_no_config_key(self) -> None:
        """T-BWC-01: System works identically without CONF_REFERENCE_TRACKERS."""
        handler = _make_handler()
        handler.coordinator.options = {}

        # _update_reference_tracker_learning should be a no-op
        handler._update_reference_tracker_learning(nowstamp=1000.0)

        # No aggregation cached
        assert handler._last_ref_tracker_aggregation == {}

    def test_bwc04_no_trackers_is_noop(self) -> None:
        """T-BWC-04: No reference trackers configured → learning is no-op."""
        handler = _make_handler()
        # Add a regular (non-reference) device
        dev = FakeRefDevice(
            address="aa:bb:cc:dd:ee:01",
            name="Regular Device",
            is_reference_tracker=False,
            area_id="area.kitchen",
        )
        dev.adverts = {
            "scanner_a": FakeAdvert(scanner_address="scanner_a", rssi=-55.0, stamp=1000.0),
        }
        handler.coordinator.devices = {dev.address: dev}

        handler._update_reference_tracker_learning(nowstamp=1001.0)
        assert handler._last_ref_tracker_aggregation == {}


# =============================================================================
# TestProxy (Proxy dataclass behavior)
# =============================================================================


class TestProxy:
    """Tests for _ReferenceTrackerProxy dataclass."""

    def test_proxy_address_prefix(self) -> None:
        """Proxy address starts with ref: prefix."""
        proxy = _ReferenceTrackerProxy(
            address=f"{REFERENCE_TRACKER_DEVICE_PREFIX}area.kitchen",
            name="Reference Tracker (Kitchen)",
        )
        assert proxy.address.startswith(REFERENCE_TRACKER_DEVICE_PREFIX)

    def test_proxy_always_stationary(self) -> None:
        """Proxy always returns STATIONARY movement state."""
        proxy = _ReferenceTrackerProxy(
            address="ref:area.kitchen",
            name="Test",
        )
        assert proxy.get_movement_state() == MOVEMENT_STATE_STATIONARY
        assert proxy.get_movement_state(stamp_now=1000.0) == MOVEMENT_STATE_STATIONARY

    def test_proxy_always_long_dwell(self) -> None:
        """Proxy always returns 24h dwell time."""
        proxy = _ReferenceTrackerProxy(
            address="ref:area.kitchen",
            name="Test",
        )
        assert proxy.get_dwell_time() == 86400.0
        assert proxy.get_dwell_time(stamp_now=1000.0) == 86400.0

    def test_proxy_default_area(self) -> None:
        """Proxy has configurable area_id."""
        proxy = _ReferenceTrackerProxy(
            address="ref:area.kitchen",
            name="Test",
            area_id="area.kitchen",
        )
        assert proxy.area_id == "area.kitchen"

    def test_proxy_empty_adverts_by_default(self) -> None:
        """Proxy starts with empty adverts dict."""
        proxy = _ReferenceTrackerProxy(address="ref:test", name="Test")
        assert proxy.adverts == {}


# =============================================================================
# TestDiagnostics (T-INT-04)
# =============================================================================


class TestDiagnostics:
    """Tests for reference tracker diagnostic output."""

    def test_int04_diagnostics_shows_status(self) -> None:
        """T-INT-04: Diagnostics shows reference tracker information."""
        handler = _make_handler()
        handler.coordinator.options = {
            CONF_REFERENCE_TRACKERS: ["aa:bb:cc:dd:ee:01"],
        }

        diag = handler.get_reference_tracker_diagnostics()

        assert diag["configured_count"] == 1
        assert "aa:bb:cc:dd:ee:01" in diag["configured_addresses"]
        assert "aggregation_by_area" in diag

    def test_diagnostics_with_aggregation_data(self) -> None:
        """Diagnostics includes last aggregation results."""
        handler = _make_handler()
        dev = _make_ref_device(
            "aa:bb:cc:dd:ee:01",
            "area.kitchen",
            {"scanner_a": -55.0, "scanner_b": -70.0},
        )
        handler.coordinator.devices = {dev.address: dev}

        # Run aggregation to populate cache
        handler._update_reference_tracker_learning(nowstamp=1001.0)

        diag = handler.get_reference_tracker_diagnostics()
        assert "area.kitchen" in diag["aggregation_by_area"]
        area_diag = diag["aggregation_by_area"]["area.kitchen"]
        assert area_diag["primary_scanner"] == "scanner_a"
        assert area_diag["primary_rssi"] == -55.0

    def test_diagnostics_empty_when_no_trackers(self) -> None:
        """Diagnostics is empty when no reference trackers configured."""
        handler = _make_handler()
        handler.coordinator.options = {}

        diag = handler.get_reference_tracker_diagnostics()
        assert diag["configured_count"] == 0
        assert diag["aggregation_by_area"] == {}


# =============================================================================
# TestLearningIntegration
# =============================================================================


class TestLearningIntegration:
    """Integration tests for the learning pipeline."""

    def test_update_learning_calls_update_device_correlations(self) -> None:
        """_update_reference_tracker_learning calls _update_device_correlations."""
        handler = _make_handler()
        dev = _make_ref_device(
            "aa:bb:cc:dd:ee:01",
            "area.kitchen",
            {"scanner_a": -55.0, "scanner_b": -70.0},
        )
        handler.coordinator.devices = {dev.address: dev}

        with patch.object(handler, "_update_device_correlations") as mock_update:
            handler._update_reference_tracker_learning(nowstamp=1001.0)
            mock_update.assert_called_once()

            # Verify the call arguments
            call_kwargs = mock_update.call_args
            assert call_kwargs.kwargs["area_id"] == "area.kitchen"
            assert call_kwargs.kwargs["primary_rssi"] == -55.0
            assert call_kwargs.kwargs["primary_scanner_addr"] == "scanner_a"
            assert call_kwargs.kwargs["confidence"] == REFERENCE_TRACKER_CONFIDENCE
            assert "scanner_b" in call_kwargs.kwargs["other_readings"]

    def test_update_learning_two_rooms_two_calls(self) -> None:
        """Two rooms with ref trackers produce two _update_device_correlations calls."""
        handler = _make_handler()
        devices: dict[str, Any] = {}
        dev_k = _make_ref_device(
            "aa:bb:cc:dd:ee:01",
            "area.kitchen",
            {"scanner_a": -55.0},
        )
        devices[dev_k.address] = dev_k

        dev_o = _make_ref_device(
            "aa:bb:cc:dd:ee:02",
            "area.office",
            {"scanner_b": -60.0},
        )
        devices[dev_o.address] = dev_o

        handler.coordinator.devices = devices

        with patch.object(handler, "_update_device_correlations") as mock_update:
            handler._update_reference_tracker_learning(nowstamp=1001.0)
            assert mock_update.call_count == 2

    def test_update_learning_device_key_format(self) -> None:
        """Proxy device address follows ref:<area_id> format."""
        handler = _make_handler()
        dev = _make_ref_device(
            "aa:bb:cc:dd:ee:01",
            "area.kitchen",
            {"scanner_a": -55.0, "scanner_b": -70.0},
        )
        handler.coordinator.devices = {dev.address: dev}

        with patch.object(handler, "_update_device_correlations") as mock_update:
            handler._update_reference_tracker_learning(nowstamp=1001.0)

            call_args = mock_update.call_args
            device_arg = call_args.kwargs["device"]
            assert device_arg.address == "ref:area.kitchen"

    def test_update_learning_no_trackers_is_noop(self) -> None:
        """No reference trackers → no calls to _update_device_correlations."""
        handler = _make_handler()
        handler.coordinator.devices = {}

        with patch.object(handler, "_update_device_correlations") as mock_update:
            handler._update_reference_tracker_learning(nowstamp=1001.0)
            mock_update.assert_not_called()

    def test_update_learning_caches_aggregation(self) -> None:
        """Learning updates the diagnostic aggregation cache."""
        handler = _make_handler()
        dev = _make_ref_device(
            "aa:bb:cc:dd:ee:01",
            "area.kitchen",
            {"scanner_a": -55.0},
        )
        handler.coordinator.devices = {dev.address: dev}

        handler._update_reference_tracker_learning(nowstamp=1001.0)

        assert "area.kitchen" in handler._last_ref_tracker_aggregation


# =============================================================================
# TestMedianEdgeCases
# =============================================================================


class TestMedianEdgeCases:
    """Edge cases for median aggregation with varied tracker counts."""

    def test_even_number_of_trackers(self) -> None:
        """Even number of trackers produces correct median (average of middle two)."""
        handler = _make_handler()
        devices: dict[str, Any] = {}
        rssi_values = [-52.0, -58.0, -55.0, -60.0]
        for i, rssi in enumerate(rssi_values):
            dev = _make_ref_device(
                f"aa:bb:cc:dd:ee:{i:02x}",
                "area.kitchen",
                {"scanner_a": rssi},
            )
            devices[dev.address] = dev
        handler.coordinator.devices = devices

        result = handler._aggregate_reference_tracker_readings(nowstamp=1001.0)
        primary_rssi, _, _, _ = result["area.kitchen"]
        assert primary_rssi == statistics.median(rssi_values)

    def test_mixed_scanner_coverage(self) -> None:
        """Trackers with different scanner visibility produce correct medians."""
        handler = _make_handler()
        devices: dict[str, Any] = {}

        # Tracker 1 sees scanner_a and scanner_b
        dev1 = _make_ref_device(
            "aa:bb:cc:dd:ee:01",
            "area.kitchen",
            {"scanner_a": -55.0, "scanner_b": -70.0},
        )
        devices[dev1.address] = dev1

        # Tracker 2 sees only scanner_a
        dev2 = _make_ref_device(
            "aa:bb:cc:dd:ee:02",
            "area.kitchen",
            {"scanner_a": -58.0},
        )
        devices[dev2.address] = dev2

        # Tracker 3 sees scanner_a and scanner_b
        dev3 = _make_ref_device(
            "aa:bb:cc:dd:ee:03",
            "area.kitchen",
            {"scanner_a": -52.0, "scanner_b": -73.0},
        )
        devices[dev3.address] = dev3

        handler.coordinator.devices = devices
        result = handler._aggregate_reference_tracker_readings(nowstamp=1001.0)

        primary_rssi, _, other, _ = result["area.kitchen"]
        # scanner_a: median of [-55, -58, -52] = -55
        # scanner_b: median of [-70, -73] = -71.5 (only 2 trackers see it)
        assert primary_rssi == statistics.median([-55.0, -58.0, -52.0])
        assert other["scanner_b"] == statistics.median([-70.0, -73.0])

    def test_scanner_stamps_tracked(self) -> None:
        """Scanner stamps are correctly tracked (max stamp per scanner)."""
        handler = _make_handler()
        devices: dict[str, Any] = {}

        dev1 = _make_ref_device(
            "aa:bb:cc:dd:ee:01",
            "area.kitchen",
            {"scanner_a": -55.0},
            stamp=1000.0,
        )
        devices[dev1.address] = dev1

        dev2 = _make_ref_device(
            "aa:bb:cc:dd:ee:02",
            "area.kitchen",
            {"scanner_a": -58.0},
            stamp=1002.0,  # Newer stamp
        )
        devices[dev2.address] = dev2

        handler.coordinator.devices = devices
        result = handler._aggregate_reference_tracker_readings(nowstamp=1003.0)

        _, _, _, stamps = result["area.kitchen"]
        # Should use the maximum stamp (1002.0)
        assert stamps["scanner_a"] == 1002.0


# =============================================================================
# TestEndToEndLearning — Verifies data actually reaches AreaProfile
# =============================================================================


class TestEndToEndLearning:
    """End-to-end tests that verify learning data reaches AreaProfile/RoomProfile.

    These tests do NOT mock _update_device_correlations — they call the real
    pipeline and assert on the resulting AreaProfile state.  This catches bugs
    where the call is made but the data is silently rejected (e.g., Feature 1
    new-data check rejecting empty current_stamps from proxy's empty adverts).
    """

    def test_learning_writes_area_profile(self) -> None:
        """Reference tracker learning must produce a non-empty AreaProfile.

        Regression test for Bug #1: proxy's empty adverts caused Feature 1
        (new-data check) to always reject updates because current_stamps was
        an empty dict, and any() over an empty iterable returns False.
        """
        handler = _make_handler()
        dev = _make_ref_device(
            "aa:bb:cc:dd:ee:01",
            "area.kitchen",
            {"scanner_a": -55.0, "scanner_b": -70.0},
        )
        handler.coordinator.devices = {dev.address: dev}

        # Run learning (calls _update_device_correlations without mocking it)
        handler._update_reference_tracker_learning(nowstamp=1001.0)

        device_key = f"{REFERENCE_TRACKER_DEVICE_PREFIX}area.kitchen"

        # AreaProfile must exist and have been updated
        assert device_key in handler.correlations, "BUG: ref tracker correlations entry was never created"
        assert "area.kitchen" in handler.correlations[device_key], "BUG: AreaProfile for area.kitchen was never created"
        profile = handler.correlations[device_key]["area.kitchen"]
        assert profile.sample_count > 0, "BUG: AreaProfile has zero samples — learning was silently rejected"

    def test_learning_writes_room_profile(self) -> None:
        """Reference tracker learning must also update the RoomProfile."""
        handler = _make_handler()
        dev = _make_ref_device(
            "aa:bb:cc:dd:ee:01",
            "area.kitchen",
            {"scanner_a": -55.0, "scanner_b": -70.0},
        )
        handler.coordinator.devices = {dev.address: dev}

        handler._update_reference_tracker_learning(nowstamp=1001.0)

        assert "area.kitchen" in handler.room_profiles, "BUG: RoomProfile for area.kitchen was never created"
        room_profile = handler.room_profiles["area.kitchen"]
        assert room_profile.total_samples > 0, "BUG: RoomProfile has zero samples — learning was silently rejected"

    def test_learning_respects_min_interval(self) -> None:
        """Consecutive calls within AUTO_LEARNING_MIN_INTERVAL are throttled."""
        handler = _make_handler()
        dev = _make_ref_device(
            "aa:bb:cc:dd:ee:01",
            "area.kitchen",
            {"scanner_a": -55.0, "scanner_b": -70.0},
        )
        handler.coordinator.devices = {dev.address: dev}

        # First call at t=1001 — succeeds
        handler._update_reference_tracker_learning(nowstamp=1001.0)
        device_key = f"{REFERENCE_TRACKER_DEVICE_PREFIX}area.kitchen"
        profile = handler.correlations[device_key]["area.kitchen"]
        count_after_first = profile.sample_count

        # Second call at t=1002 (1s later, < 5s MIN_INTERVAL) — throttled
        handler._update_reference_tracker_learning(nowstamp=1002.0)
        assert profile.sample_count == count_after_first, "Learning should be throttled by minimum interval"

        # Third call at t=1007 (6s after first, > 5s MIN_INTERVAL) — succeeds
        handler._update_reference_tracker_learning(nowstamp=1007.0)
        assert profile.sample_count > count_after_first, "Learning should proceed after minimum interval elapsed"

    def test_learning_two_areas_independent_profiles(self) -> None:
        """Two areas with reference trackers get independent AreaProfiles."""
        handler = _make_handler()
        devices: dict[str, Any] = {}

        dev_k = _make_ref_device(
            "aa:bb:cc:dd:ee:01",
            "area.kitchen",
            {"scanner_a": -55.0, "scanner_b": -70.0},
        )
        devices[dev_k.address] = dev_k

        dev_o = _make_ref_device(
            "aa:bb:cc:dd:ee:02",
            "area.office",
            {"scanner_b": -60.0, "scanner_c": -75.0},
        )
        devices[dev_o.address] = dev_o

        handler.coordinator.devices = devices
        handler._update_reference_tracker_learning(nowstamp=1001.0)

        key_k = f"{REFERENCE_TRACKER_DEVICE_PREFIX}area.kitchen"
        key_o = f"{REFERENCE_TRACKER_DEVICE_PREFIX}area.office"

        assert key_k in handler.correlations
        assert key_o in handler.correlations
        assert "area.kitchen" in handler.correlations[key_k]
        assert "area.office" in handler.correlations[key_o]

        # Each has its own profile with data
        assert handler.correlations[key_k]["area.kitchen"].sample_count > 0
        assert handler.correlations[key_o]["area.office"].sample_count > 0
