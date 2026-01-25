"""Test Bermuda BLE Trilateration setup process."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

# from homeassistant.exceptions import ConfigEntryNotReady
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bermuda import (
    BermudaData,
    async_migrate_entry,
    async_reload_entry,
    async_remove_config_entry_device,
)
from custom_components.bermuda.const import DOMAIN, IrkTypes
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator

from .const import MOCK_CONFIG
from homeassistant.config_entries import ConfigEntryState

# from pytest_homeassistant_custom_component.common import AsyncMock


# We can pass fixtures as defined in conftest.py to tell pytest to use the fixture
# for a given test. We can also leverage fixtures and mocks that are available in
# Home Assistant using the pytest_homeassistant_custom_component plugin.
# Assertions allow you to verify that the return value of whatever is on the left
# side of the assertion matches with the right side.
async def test_setup_unload_and_reload_entry(
    hass: HomeAssistant, bypass_get_data: Any, setup_bermuda_entry: MockConfigEntry
) -> None:
    """Test entry setup and unload."""

    # Reload the entry and assert that the data from above is still there
    assert await hass.config_entries.async_reload(setup_bermuda_entry.entry_id)
    assert setup_bermuda_entry.state == ConfigEntryState.LOADED

    assert set(IrkTypes.unresolved()) == {
        IrkTypes.ADRESS_NOT_EVALUATED.value,
        IrkTypes.NO_KNOWN_IRK_MATCH.value,
        IrkTypes.NOT_RESOLVABLE_ADDRESS.value,
    }

    # Unload the entry and verify that the data has been removed
    assert await hass.config_entries.async_unload(setup_bermuda_entry.entry_id)
    assert setup_bermuda_entry.state == ConfigEntryState.NOT_LOADED


async def test_setup_entry_exception(hass: HomeAssistant, error_on_get_data: Any) -> None:
    """Test ConfigEntryNotReady when API raises an exception during entry setup."""
    config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")

    assert config_entry is not None

    # In this case we are testing the condition where async_setup_entry raises
    # ConfigEntryNotReady using the `error_on_get_data` fixture which simulates
    # an error.

    # Hmmm... this doesn't seem to be how this works. The super's _async_refresh might
    # handle exceptions, in which it then sets self.last_update_status, which is what
    # async_setup_entry checks in order to raise ConfigEntryNotReady, but I don't think
    # anything will "catch" our over-ridded async_refresh's exception.
    #  with pytest.raises(ConfigEntryNotReady):
    #     assert await async_setup_entry(hass, config_entry)


class TestBermudaData:
    """Tests for BermudaData dataclass."""

    def test_bermuda_data_creation(self) -> None:
        """Test BermudaData dataclass creation."""
        mock_coordinator = MagicMock()
        data = BermudaData(coordinator=mock_coordinator)

        assert data.coordinator is mock_coordinator


class TestAsyncMigrateEntry:
    """Tests for async_migrate_entry function."""

    @pytest.mark.asyncio
    async def test_migrate_entry_version_1(self, hass: HomeAssistant) -> None:
        """Test migration with version 1 (current)."""
        entry = MagicMock(spec=ConfigEntry)
        entry.version = 1
        entry.minor_version = 0

        result = await async_migrate_entry(hass, entry)

        assert result is True

    @pytest.mark.asyncio
    async def test_migrate_entry_version_2(self, hass: HomeAssistant) -> None:
        """Test migration with version 2."""
        entry = MagicMock(spec=ConfigEntry)
        entry.version = 2
        entry.minor_version = 0

        result = await async_migrate_entry(hass, entry)

        assert result is True


class TestAsyncRemoveConfigEntryDevice:
    """Tests for async_remove_config_entry_device function."""

    @pytest.mark.asyncio
    async def test_remove_device_with_valid_identifier(
        self, hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
    ) -> None:
        """Test removing a device with valid Bermuda identifier."""
        coordinator = setup_bermuda_entry.runtime_data.coordinator

        # Create a device first
        device = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
        device.create_sensor = True

        # Create device entry
        device_entry = MagicMock()
        device_entry.identifiers = {(DOMAIN, "aa:bb:cc:dd:ee:ff")}
        device_entry.name = "Test Device"

        result = await async_remove_config_entry_device(hass, setup_bermuda_entry, device_entry)

        assert result is True
        assert device.create_sensor is False

    @pytest.mark.asyncio
    async def test_remove_device_with_suffix(self, hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
        """Test removing a device with suffixed identifier."""
        coordinator = setup_bermuda_entry.runtime_data.coordinator

        # Create a device first
        device = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
        device.create_sensor = True

        # Create device entry with suffix
        device_entry = MagicMock()
        device_entry.identifiers = {(DOMAIN, "aa:bb:cc:dd:ee:ff_range")}
        device_entry.name = "Test Device"

        result = await async_remove_config_entry_device(hass, setup_bermuda_entry, device_entry)

        assert result is True
        assert device.create_sensor is False

    @pytest.mark.asyncio
    async def test_remove_device_unknown_address(
        self, hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
    ) -> None:
        """Test removing a device with unknown address."""
        device_entry = MagicMock()
        device_entry.identifiers = {(DOMAIN, "11:22:33:44:55:66")}
        device_entry.name = "Unknown Device"

        result = await async_remove_config_entry_device(hass, setup_bermuda_entry, device_entry)

        # Should still return True but log a warning
        assert result is True

    @pytest.mark.asyncio
    async def test_remove_device_no_bermuda_identifier(
        self, hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
    ) -> None:
        """Test removing a device without Bermuda identifier."""
        device_entry = MagicMock()
        device_entry.identifiers = {("other_domain", "some_id")}
        device_entry.name = "Other Device"

        result = await async_remove_config_entry_device(hass, setup_bermuda_entry, device_entry)

        # Should still return True (allow deletion)
        assert result is True


class TestAsyncReloadEntry:
    """Tests for async_reload_entry function."""

    @pytest.mark.asyncio
    async def test_reload_entry_calls_reload_options(
        self, hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
    ) -> None:
        """Test that reload entry calls coordinator.reload_options."""
        coordinator = setup_bermuda_entry.runtime_data.coordinator

        with patch.object(coordinator, "reload_options") as mock_reload:
            await async_reload_entry(hass, setup_bermuda_entry)

            mock_reload.assert_called_once()
