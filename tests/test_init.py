"""Test Bermuda BLE Trilateration setup process."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

# from homeassistant.exceptions import ConfigEntryNotReady
from pytest_homeassistant_custom_component.common import MockConfigEntry

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
    hass: HomeAssistant, bypass_get_data, setup_bermuda_entry: MockConfigEntry
):
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


async def test_setup_entry_exception(hass, error_on_get_data):
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


def test_sanitize_timedelta_values() -> None:
    """Test that _sanitize_timedelta_values converts timedelta to seconds.

    This is a regression test for the bug where timedelta objects in config entry
    options caused JSON serialization errors.
    """
    from datetime import timedelta

    from custom_components.bermuda import _sanitize_timedelta_values

    # Test with timedelta values
    data_with_timedelta = {
        "update_interval": timedelta(seconds=30),
        "normal_value": 42,
        "string_value": "test",
        "nested": {
            "inner_timedelta": timedelta(minutes=5),
            "inner_int": 100,
        },
    }

    result = _sanitize_timedelta_values(data_with_timedelta)

    # Verify timedelta values are converted to seconds (float)
    assert result["update_interval"] == 30.0
    assert result["normal_value"] == 42
    assert result["string_value"] == "test"
    assert result["nested"]["inner_timedelta"] == 300.0  # 5 minutes = 300 seconds
    assert result["nested"]["inner_int"] == 100


def test_sanitize_timedelta_values_no_changes() -> None:
    """Test that _sanitize_timedelta_values returns unchanged data when no timedelta present."""
    from custom_components.bermuda import _sanitize_timedelta_values

    data_without_timedelta = {
        "update_interval": 30,
        "max_radius": 20.5,
        "devices": ["aa:bb:cc:dd:ee:ff"],
        "nested": {"value": 100},
    }

    result = _sanitize_timedelta_values(data_without_timedelta)

    # Verify data is unchanged
    assert result == data_without_timedelta
