"""Tests for RSSI offset persistence across reloads and reboots.

This test module verifies that Per-Scanner RSSI Offsets are correctly:
1. Saved to the config entry when changed via the options flow
2. Loaded from the config entry when the integration starts
3. Available to the coordinator after a reload
4. Persist across entry unload/load cycles (simulating a reboot)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry  # type: ignore[import-untyped]

from custom_components.bermuda.const import (
    CONF_ATTENUATION,
    CONF_DEVTRACK_TIMEOUT,
    CONF_MAX_RADIUS,
    CONF_MAX_VELOCITY,
    CONF_REF_POWER,
    CONF_RSSI_OFFSETS,
    CONF_SMOOTHING_SAMPLES,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
    NAME,
)

from .const import MOCK_CONFIG


@pytest.fixture
def mock_rssi_offsets() -> dict[str, int]:
    """Create mock RSSI offsets for testing."""
    return {
        "aa:bb:cc:dd:ee:ff": 5,
        "11:22:33:44:55:66": -10,
        "00:11:22:33:44:55": 20,
    }


@pytest.fixture
def mock_options_with_rssi_offsets(mock_rssi_offsets: dict[str, int]) -> dict[str, Any]:
    """Create mock options including RSSI offsets."""
    return {
        CONF_MAX_RADIUS: 20.0,
        CONF_MAX_VELOCITY: 3.0,
        CONF_DEVTRACK_TIMEOUT: 30,
        CONF_UPDATE_INTERVAL: 10.0,
        CONF_SMOOTHING_SAMPLES: 20,
        CONF_ATTENUATION: 3.0,
        CONF_REF_POWER: -55.0,
        CONF_RSSI_OFFSETS: mock_rssi_offsets,
    }


class TestRssiOffsetPersistence:
    """Tests for RSSI offset persistence."""

    async def test_rssi_offsets_loaded_from_config_entry(
        self,
        hass: HomeAssistant,
        mock_options_with_rssi_offsets: dict[str, Any],
        mock_rssi_offsets: dict[str, int],
    ) -> None:
        """Test that RSSI offsets are loaded from config entry on startup.

        This is a regression test for the bug where RSSI offsets were not
        persisting after a reboot.
        """
        # Create config entry with RSSI offsets already saved
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data=MOCK_CONFIG,
            options=mock_options_with_rssi_offsets,
            entry_id="test_rssi_persistence",
            title=NAME,
        )
        config_entry.add_to_hass(hass)

        # Setup the integration
        await async_setup_component(hass, DOMAIN, {})
        assert config_entry.state == ConfigEntryState.LOADED

        # Verify the coordinator loaded the RSSI offsets
        coordinator = config_entry.runtime_data.coordinator
        assert CONF_RSSI_OFFSETS in coordinator.options
        assert coordinator.options[CONF_RSSI_OFFSETS] == mock_rssi_offsets

    async def test_rssi_offsets_persist_after_reload(
        self,
        hass: HomeAssistant,
        mock_options_with_rssi_offsets: dict[str, Any],
        mock_rssi_offsets: dict[str, int],
    ) -> None:
        """Test that RSSI offsets persist after integration reload.

        Simulates what happens when the user changes options and the
        integration reloads without a full restart.
        """
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data=MOCK_CONFIG,
            options=mock_options_with_rssi_offsets,
            entry_id="test_rssi_reload",
            title=NAME,
        )
        config_entry.add_to_hass(hass)

        await async_setup_component(hass, DOMAIN, {})
        assert config_entry.state == ConfigEntryState.LOADED

        coordinator = config_entry.runtime_data.coordinator
        original_offsets = coordinator.options[CONF_RSSI_OFFSETS].copy()

        # Simulate reload_options being called (as happens after options change)
        coordinator.reload_options()

        # Verify offsets are still there
        assert coordinator.options[CONF_RSSI_OFFSETS] == original_offsets

    async def test_rssi_offsets_persist_after_unload_and_load(
        self,
        hass: HomeAssistant,
        mock_options_with_rssi_offsets: dict[str, Any],
        mock_rssi_offsets: dict[str, int],
    ) -> None:
        """Test that RSSI offsets persist after unload and load cycle.

        This simulates a reboot scenario where the integration is fully
        unloaded and then loaded again.
        """
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data=MOCK_CONFIG,
            options=mock_options_with_rssi_offsets,
            entry_id="test_rssi_unload_load",
            title=NAME,
        )
        config_entry.add_to_hass(hass)

        # Initial setup
        await async_setup_component(hass, DOMAIN, {})
        assert config_entry.state == ConfigEntryState.LOADED

        # Verify initial state
        coordinator = config_entry.runtime_data.coordinator
        assert coordinator.options[CONF_RSSI_OFFSETS] == mock_rssi_offsets

        # Unload the entry
        await hass.config_entries.async_unload(config_entry.entry_id)
        assert config_entry.state == ConfigEntryState.NOT_LOADED

        # Verify the options are still in the config entry
        assert config_entry.options.get(CONF_RSSI_OFFSETS) == mock_rssi_offsets

        # Reload the entry
        await hass.config_entries.async_setup(config_entry.entry_id)
        assert config_entry.state == ConfigEntryState.LOADED

        # Verify the coordinator has the RSSI offsets after reload
        new_coordinator = config_entry.runtime_data.coordinator
        assert CONF_RSSI_OFFSETS in new_coordinator.options
        assert new_coordinator.options[CONF_RSSI_OFFSETS] == mock_rssi_offsets

    async def test_config_entry_options_contain_rssi_offsets(
        self,
        hass: HomeAssistant,
        mock_options_with_rssi_offsets: dict[str, Any],
        mock_rssi_offsets: dict[str, int],
    ) -> None:
        """Verify that config entry options actually contain RSSI offsets.

        This test ensures that when we save RSSI offsets, they are properly
        stored in the config entry's options dictionary.
        """
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data=MOCK_CONFIG,
            options=mock_options_with_rssi_offsets,
            entry_id="test_config_entry_options",
            title=NAME,
        )
        config_entry.add_to_hass(hass)

        # Directly check the config entry options
        assert CONF_RSSI_OFFSETS in config_entry.options
        assert config_entry.options[CONF_RSSI_OFFSETS] == mock_rssi_offsets

        # After setup, should still be there
        await async_setup_component(hass, DOMAIN, {})
        assert config_entry.options[CONF_RSSI_OFFSETS] == mock_rssi_offsets

    async def test_empty_rssi_offsets_handled_correctly(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test that empty RSSI offsets dict is handled correctly."""
        options_with_empty_offsets = {
            CONF_MAX_RADIUS: 20.0,
            CONF_MAX_VELOCITY: 3.0,
            CONF_DEVTRACK_TIMEOUT: 30,
            CONF_UPDATE_INTERVAL: 10.0,
            CONF_SMOOTHING_SAMPLES: 20,
            CONF_ATTENUATION: 3.0,
            CONF_REF_POWER: -55.0,
            CONF_RSSI_OFFSETS: {},
        }

        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data=MOCK_CONFIG,
            options=options_with_empty_offsets,
            entry_id="test_empty_offsets",
            title=NAME,
        )
        config_entry.add_to_hass(hass)

        await async_setup_component(hass, DOMAIN, {})
        assert config_entry.state == ConfigEntryState.LOADED

        coordinator = config_entry.runtime_data.coordinator
        assert coordinator.options[CONF_RSSI_OFFSETS] == {}

    async def test_missing_rssi_offsets_defaults_to_empty(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test that missing RSSI offsets key defaults to empty dict."""
        options_without_offsets = {
            CONF_MAX_RADIUS: 20.0,
            CONF_MAX_VELOCITY: 3.0,
            CONF_DEVTRACK_TIMEOUT: 30,
            CONF_UPDATE_INTERVAL: 10.0,
            CONF_SMOOTHING_SAMPLES: 20,
            CONF_ATTENUATION: 3.0,
            CONF_REF_POWER: -55.0,
            # CONF_RSSI_OFFSETS intentionally missing
        }

        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data=MOCK_CONFIG,
            options=options_without_offsets,
            entry_id="test_missing_offsets",
            title=NAME,
        )
        config_entry.add_to_hass(hass)

        await async_setup_component(hass, DOMAIN, {})
        assert config_entry.state == ConfigEntryState.LOADED

        coordinator = config_entry.runtime_data.coordinator
        # Should default to empty dict
        assert coordinator.options[CONF_RSSI_OFFSETS] == {}

    async def test_rssi_offsets_update_via_hass_api(
        self,
        hass: HomeAssistant,
        mock_rssi_offsets: dict[str, int],
    ) -> None:
        """Test updating RSSI offsets via Home Assistant config entry API.

        This simulates what happens when the options flow saves new values.
        """
        initial_options = {
            CONF_MAX_RADIUS: 20.0,
            CONF_MAX_VELOCITY: 3.0,
            CONF_DEVTRACK_TIMEOUT: 30,
            CONF_UPDATE_INTERVAL: 10.0,
            CONF_SMOOTHING_SAMPLES: 20,
            CONF_ATTENUATION: 3.0,
            CONF_REF_POWER: -55.0,
            CONF_RSSI_OFFSETS: {},
        }

        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data=MOCK_CONFIG,
            options=initial_options,
            entry_id="test_update_offsets",
            title=NAME,
        )
        config_entry.add_to_hass(hass)

        await async_setup_component(hass, DOMAIN, {})
        assert config_entry.state == ConfigEntryState.LOADED

        # Initially empty
        coordinator = config_entry.runtime_data.coordinator
        assert coordinator.options[CONF_RSSI_OFFSETS] == {}

        # Update options via Home Assistant API (simulates options flow save)
        new_options = {**initial_options, CONF_RSSI_OFFSETS: mock_rssi_offsets}
        hass.config_entries.async_update_entry(config_entry, options=new_options)
        await hass.async_block_till_done()

        # Verify config entry was updated
        assert config_entry.options[CONF_RSSI_OFFSETS] == mock_rssi_offsets

        # After reload_options, coordinator should have new values
        coordinator.reload_options()
        assert coordinator.options[CONF_RSSI_OFFSETS] == mock_rssi_offsets
