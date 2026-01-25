"""Tests for button training persistence (BUG 17 investigation).

This test verifies that button training data survives the serialization/deserialization
round-trip through the AreaProfile -> ScannerAbsoluteRssi -> KalmanFilter chain.
"""

import pytest

from custom_components.bermuda.correlation.area_profile import AreaProfile
from custom_components.bermuda.correlation.scanner_absolute import ScannerAbsoluteRssi
from custom_components.bermuda.correlation.scanner_pair import ScannerPairCorrelation
from custom_components.bermuda.filters.kalman import KalmanFilter


class TestKalmanFilterPersistence:
    """Test KalmanFilter state persistence."""

    def test_update_sets_initialized(self) -> None:
        """KalmanFilter.update() should set _initialized to True."""
        kf = KalmanFilter()
        assert not kf.is_initialized
        assert kf.sample_count == 0

        kf.update(-80.0)

        assert kf.is_initialized
        assert kf.sample_count == 1

    def test_restore_state_with_samples(self) -> None:
        """restore_state() with sample_count > 0 should set _initialized."""
        kf = KalmanFilter()
        assert not kf.is_initialized

        kf.restore_state(estimate=-80.0, variance=4.0, sample_count=20)

        assert kf.is_initialized
        assert kf.sample_count == 20
        assert kf.estimate == -80.0

    def test_restore_state_with_zero_samples(self) -> None:
        """restore_state() with sample_count = 0 should NOT set _initialized."""
        kf = KalmanFilter()

        kf.restore_state(estimate=0.0, variance=4.0, sample_count=0)

        assert not kf.is_initialized
        assert kf.sample_count == 0


class TestScannerAbsoluteRssiPersistence:
    """Test ScannerAbsoluteRssi button training persistence."""

    def test_update_button_sets_has_button_training(self) -> None:
        """update_button() should set has_button_training to True."""
        profile = ScannerAbsoluteRssi(scanner_address="AA:BB:CC:DD:EE:FF")
        assert not profile.has_button_training

        profile.update_button(-80.0)

        assert profile.has_button_training
        assert profile.button_sample_count == 1

    def test_multiple_update_button_accumulates(self) -> None:
        """Multiple update_button() calls should accumulate samples."""
        profile = ScannerAbsoluteRssi(scanner_address="AA:BB:CC:DD:EE:FF")

        for i in range(20):
            profile.update_button(-80.0 + i * 0.1)

        assert profile.has_button_training
        assert profile.button_sample_count == 20

    def test_serialization_round_trip_preserves_button_training(self) -> None:
        """Button training should survive to_dict() -> from_dict() round-trip."""
        # Create profile and train it
        profile = ScannerAbsoluteRssi(scanner_address="AA:BB:CC:DD:EE:FF")
        for _ in range(20):
            profile.update_button(-80.0)

        assert profile.has_button_training
        assert profile.button_sample_count == 20

        # Serialize
        data = profile.to_dict()
        print(f"Serialized data: {data}")  # Debug output

        # Check serialized values
        assert data["button_samples"] == 20
        assert data["button_estimate"] == pytest.approx(-80.0, abs=1.0)

        # Deserialize
        restored = ScannerAbsoluteRssi.from_dict(data)

        # Verify button training is preserved
        assert restored.has_button_training, (
            f"has_button_training should be True after deserialization! "
            f"button_sample_count={restored.button_sample_count}, "
            f"_kalman_button.is_initialized={restored._kalman_button.is_initialized}"
        )
        assert restored.button_sample_count == 20


class TestScannerPairCorrelationPersistence:
    """Test ScannerPairCorrelation button training persistence."""

    def test_serialization_round_trip_preserves_button_training(self) -> None:
        """Button training should survive to_dict() -> from_dict() round-trip."""
        corr = ScannerPairCorrelation(scanner_address="AA:BB:CC:DD:EE:FF")
        for _ in range(20):
            corr.update_button(5.0)

        assert corr.has_button_training
        assert corr.button_sample_count == 20

        # Serialize and deserialize
        data = corr.to_dict()
        restored = ScannerPairCorrelation.from_dict(data)

        assert restored.has_button_training
        assert restored.button_sample_count == 20


class TestAreaProfilePersistence:
    """Test AreaProfile button training persistence."""

    def test_update_button_sets_has_button_training(self) -> None:
        """update_button() on AreaProfile should set has_button_training."""
        profile = AreaProfile(area_id="lagerraum")
        assert not profile.has_button_training

        profile.update_button(
            primary_rssi=-70.0,
            other_readings={"scanner2": -75.0, "scanner3": -80.0},
            primary_scanner_addr="scanner1",
        )

        assert profile.has_button_training
        # Check that absolute profiles were created with button training
        for addr in ["scanner1", "scanner2", "scanner3"]:
            abs_profile = profile._absolute_profiles.get(addr)
            assert abs_profile is not None, f"Absolute profile for {addr} should exist"
            assert abs_profile.has_button_training, f"Absolute profile for {addr} should have button training"

    def test_serialization_round_trip_preserves_button_training(self) -> None:
        """Button training on AreaProfile should survive serialization round-trip."""
        # Create and train profile
        profile = AreaProfile(area_id="lagerraum")
        for _ in range(20):
            profile.update_button(
                primary_rssi=-70.0,
                other_readings={"scanner2": -75.0, "scanner3": -80.0},
                primary_scanner_addr="scanner1",
            )

        assert profile.has_button_training

        # Serialize
        data = profile.to_dict()
        print(f"Serialized AreaProfile: {data}")  # Debug

        # Check that absolute_profiles have button_samples > 0
        for abs_data in data.get("absolute_profiles", []):
            print(f"  Scanner {abs_data['scanner']}: button_samples={abs_data.get('button_samples', 'MISSING')}")
            assert (
                abs_data.get("button_samples", 0) > 0
            ), f"Scanner {abs_data['scanner']} should have button_samples > 0"

        # Deserialize
        restored = AreaProfile.from_dict(data)

        # Verify button training is preserved
        assert restored.has_button_training, (
            f"AreaProfile should have button training after deserialization! "
            f"Absolute profiles: {list(restored._absolute_profiles.keys())}"
        )

        # Check individual absolute profiles
        for addr, abs_profile in restored._absolute_profiles.items():
            assert abs_profile.has_button_training, (
                f"Absolute profile {addr} should have button training! "
                f"button_sample_count={abs_profile.button_sample_count}"
            )


class TestAddressNormalization:
    """Test that address normalization doesn't cause key mismatches (BUG 17)."""

    def test_uppercase_vs_lowercase_address(self) -> None:
        """
        Verify that correlations stored with different case addresses work correctly.

        BUG 17 root cause: Training used raw `device_address` parameter, but lookup used
        `device.address` (normalized to lowercase). If the entity passed an uppercase
        address, the training data would be stored under a different key than what
        lookup uses, causing has_button_training=False.
        """
        correlations: dict[str, dict[str, AreaProfile]] = {}

        # Simulate training with UPPERCASE address (raw parameter from entity)
        raw_address = "AA:BB:CC:DD:EE:FF"
        # Simulate what BermudaDevice.address would be (normalized to lowercase)
        normalized_address = "aa:bb:cc:dd:ee:ff"
        target_area_id = "lagerraum"

        # BEFORE FIX: Training would use raw_address as key
        # This simulates the OLD buggy behavior
        correlations[raw_address] = {}
        correlations[raw_address][target_area_id] = AreaProfile(area_id=target_area_id)
        for _ in range(20):
            correlations[raw_address][target_area_id].update_button(
                primary_rssi=-70.0,
                other_readings={"scanner2": -75.0},
                primary_scanner_addr="scanner1",
            )

        # Training data exists under uppercase key
        assert correlations[raw_address][target_area_id].has_button_training

        # But lookup uses normalized (lowercase) key - would fail to find training!
        lookup_profiles = correlations.get(normalized_address, {})
        # This was the BUG: lookup_profiles would be empty because keys don't match
        assert len(lookup_profiles) == 0, "Bug 17: Uppercase key not found with lowercase lookup"

        # AFTER FIX: Training should use normalized_address as key
        correlations_fixed: dict[str, dict[str, AreaProfile]] = {}
        correlations_fixed[normalized_address] = {}
        correlations_fixed[normalized_address][target_area_id] = AreaProfile(area_id=target_area_id)
        for _ in range(20):
            correlations_fixed[normalized_address][target_area_id].update_button(
                primary_rssi=-70.0,
                other_readings={"scanner2": -75.0},
                primary_scanner_addr="scanner1",
            )

        # Now lookup with normalized key finds the training data
        lookup_profiles_fixed = correlations_fixed.get(normalized_address, {})
        assert len(lookup_profiles_fixed) == 1
        assert lookup_profiles_fixed[target_area_id].has_button_training


class TestCoordinatorIntegration:
    """Test the full coordinator training flow (without actual coordinator)."""

    def test_simulated_training_flow(self) -> None:
        """Simulate the training flow that coordinator performs."""
        # This simulates what async_train_fingerprint does
        correlations: dict[str, dict[str, AreaProfile]] = {}
        device_address = "11:22:33:44:55:66"
        target_area_id = "lagerraum"

        # Ensure device entry exists
        if device_address not in correlations:
            correlations[device_address] = {}

        # Create AreaProfile
        if target_area_id not in correlations[device_address]:
            correlations[device_address][target_area_id] = AreaProfile(area_id=target_area_id)

        # Simulate 20 training samples (like TRAINING_SAMPLE_COUNT)
        for _ in range(20):
            primary_rssi = -70.0
            other_readings = {"scanner2": -75.0, "scanner3": -80.0}
            primary_scanner_addr = "scanner1"

            correlations[device_address][target_area_id].update_button(
                primary_rssi=primary_rssi,
                other_readings=other_readings,
                primary_scanner_addr=primary_scanner_addr,
            )

        # Check training succeeded
        profile = correlations[device_address][target_area_id]
        assert profile.has_button_training

        # Simulate save (serialize)
        serialized = {
            device_addr: {area_id: p.to_dict() for area_id, p in areas.items()}
            for device_addr, areas in correlations.items()
        }

        # Simulate load (deserialize)
        loaded_correlations: dict[str, dict[str, AreaProfile]] = {}
        for device_addr, areas in serialized.items():
            loaded_correlations[device_addr] = {}
            for area_id, profile_data in areas.items():
                loaded_correlations[device_addr][area_id] = AreaProfile.from_dict(profile_data)

        # Check button training survived
        loaded_profile = loaded_correlations[device_address][target_area_id]
        assert loaded_profile.has_button_training, "Button training should survive coordinator-style save/load cycle!"

        # Simulate the check in _get_virtual_distances_for_scannerless_rooms
        device_profiles = loaded_correlations.get(device_address, {})
        for area_id, area_profile in device_profiles.items():
            has_btn = area_profile.has_button_training
            print(f"Profile {area_id}: has_button_training={has_btn}")
            assert has_btn, f"Profile {area_id} should have button training"
