"""
Tests for correlation persistence.

These tests verify that learned correlations survive serialization
and can be correctly restored after Home Assistant restarts.
"""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.core import HomeAssistant

from custom_components.bermuda.correlation.area_profile import AreaProfile
from custom_components.bermuda.correlation.store import (
    STORAGE_KEY,
    STORAGE_VERSION,
    CorrelationStore,
)


def _create_trained_profile(area_id: str, num_samples: int = 50) -> AreaProfile:
    """Create a profile with learned correlation data for testing."""
    profile = AreaProfile(area_id=area_id)
    for _ in range(num_samples):
        profile.update(
            primary_rssi=-50.0,
            other_readings={
                "scanner_a": -60.0,
                "scanner_b": -70.0,
            },
        )
    return profile


class TestCorrelationStoreRoundtrip:
    """Tests for save/load roundtrip."""

    @pytest.mark.asyncio
    async def test_save_and_load_roundtrip(self, hass: HomeAssistant) -> None:
        """Data survives a save/load cycle without corruption."""
        store = CorrelationStore(hass)

        # Create test data
        original_data = {
            "device_aa:bb:cc:dd:ee:ff": {
                "area.living_room": _create_trained_profile("area.living_room"),
                "area.kitchen": _create_trained_profile("area.kitchen"),
            },
            "device_11:22:33:44:55:66": {
                "area.bedroom": _create_trained_profile("area.bedroom"),
            },
        }

        # Save
        await store.async_save(original_data)

        # Create new store instance to simulate restart
        store2 = CorrelationStore(hass)
        loaded_data = await store2.async_load()

        # Verify structure preserved
        assert set(loaded_data.keys()) == set(original_data.keys()), (
            f"Device addresses changed in roundtrip: "
            f"{set(original_data.keys())} -> {set(loaded_data.keys())}. "
            f"Correlations would be orphaned or associated with wrong devices."
        )

        # Verify profile data preserved
        for device_addr, areas in original_data.items():
            assert device_addr in loaded_data, (
                f"Device {device_addr} missing after load. All device correlations would be lost for this device."
            )
            for area_id, original_profile in areas.items():
                assert area_id in loaded_data[device_addr], f"Area {area_id} missing for device {device_addr}."
                loaded_profile = loaded_data[device_addr][area_id]

                assert loaded_profile.area_id == original_profile.area_id
                assert loaded_profile.mature_correlation_count == original_profile.mature_correlation_count, (
                    f"Correlation count changed for {device_addr}/{area_id}: "
                    f"{original_profile.mature_correlation_count} -> "
                    f"{loaded_profile.mature_correlation_count}."
                )

    @pytest.mark.asyncio
    async def test_load_empty_storage_returns_empty_dict(self, hass: HomeAssistant) -> None:
        """Fresh install (no stored data) returns empty dict, not error."""
        store = CorrelationStore(hass)
        loaded = await store.async_load()

        assert loaded == {}, (
            f"Empty storage returned {loaded}, expected empty dict. "
            f"First-run installations would crash if None is not handled."
        )


class TestCorrelationStorePrecision:
    """Tests for data precision preservation."""

    @pytest.mark.asyncio
    async def test_learned_estimates_preserved_exactly(self, hass: HomeAssistant) -> None:
        """Kalman filter estimates are preserved with full precision."""
        store = CorrelationStore(hass)

        # Create profile with specific learned values
        profile = AreaProfile(area_id="area.test")
        for _ in range(100):
            profile.update(
                primary_rssi=-47.3,
                other_readings={"scanner_x": -59.7},  # Delta = 12.4
            )

        original_estimate = profile._correlations["scanner_x"].expected_delta  # noqa: SLF001
        original_variance = profile._correlations["scanner_x"].variance  # noqa: SLF001

        data: dict[str, dict[str, AreaProfile]] = {"device_test": {"area.test": profile}}

        await store.async_save(data)

        # Create new store instance to simulate restart
        store2 = CorrelationStore(hass)
        loaded = await store2.async_load()

        loaded_corr = loaded["device_test"]["area.test"]._correlations["scanner_x"]  # noqa: SLF001

        assert loaded_corr.expected_delta == original_estimate, (
            f"Estimate changed: {original_estimate} -> {loaded_corr.expected_delta}. "
            f"Precision loss in learned values would accumulate over restarts, "
            f"degrading correlation accuracy."
        )
        assert loaded_corr.variance == original_variance, (
            f"Variance changed: {original_variance} -> {loaded_corr.variance}. "
            f"Variance is used for z-score calculation; changes affect confidence."
        )


class TestCorrelationStoreConfiguration:
    """Tests for store configuration."""

    def test_storage_key_constant(self) -> None:
        """Storage key has expected value."""
        assert STORAGE_KEY == "bermuda.scanner_correlations", (
            f"Storage key is '{STORAGE_KEY}', expected 'bermuda.scanner_correlations'. "
            f"Wrong key would cause data to be stored in wrong location or conflict "
            f"with other integrations."
        )

    def test_storage_version_constant(self) -> None:
        """Storage version is set."""
        assert STORAGE_VERSION == 2, (
            f"Storage version is {STORAGE_VERSION}, expected 2. "
            f"Version 2 includes room_profiles in addition to device_profiles. "
            f"Version is used for migration; unexpected value could cause issues."
        )
