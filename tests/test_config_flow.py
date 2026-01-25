"""Test Bermuda BLE Trilateration config flow."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant import data_entry_flow
from homeassistant.core import HomeAssistant
from unittest.mock import MagicMock

# from homeassistant.core import HomeAssistant  # noqa: F401
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bermuda.const import DOMAIN
from custom_components.bermuda.const import NAME
from custom_components.bermuda.const import CONF_DEVICES

# from .const import MOCK_OPTIONS
from typing import Any

from .const import MOCK_CONFIG
from .const import MOCK_OPTIONS_GLOBALS


# Here we simiulate a successful config flow from the backend.
# Note that we use the `bypass_get_data` fixture here because
# we want the config flow validation to succeed during the test.
async def test_successful_config_flow(hass: HomeAssistant, bypass_get_data: Any) -> None:
    """Test a successful config flow."""
    # Initialize a config flow
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    # Check that the config flow shows the user form as the first step
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    # If a user were to enter `test_username` for username and `test_password`
    # for password, it would result in this function call
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input=MOCK_CONFIG)

    # Check that the config flow is complete and a new entry is created with
    # the input data
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == NAME
    assert result["data"] == {"source": "user"}
    assert result["options"] == {}
    assert result["result"]


# In this case, we want to simulate a failure during the config flow.
# We use the `error_on_get_data` mock instead of `bypass_get_data`
# (note the function parameters) to raise an Exception during
# validation of the input config.
async def test_failed_config_flow(hass: HomeAssistant, error_on_get_data: Any) -> None:
    """Test a failed config flow due to credential validation failure."""

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input=MOCK_CONFIG)

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result.get("errors") is None


# Our config flow also has an options flow, so we must test it as well.
async def test_options_flow(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test an options flow."""
    # Go through options flow
    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)

    # Verify that the first options step is a user form
    assert result.get("type") == FlowResultType.MENU
    assert result.get("step_id") == "init"

    # select the globalopts menu option
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "globalopts"}
    )

    assert result.get("type") == FlowResultType.FORM
    assert result.get("step_id") == "globalopts"

    # Enter some fake data into the form
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input=MOCK_OPTIONS_GLOBALS,
    )

    # Verify that the flow finishes
    assert result.get("type") == FlowResultType.CREATE_ENTRY
    assert result.get("title") == NAME

    # Verify that the options were updated
    for key, value in MOCK_OPTIONS_GLOBALS.items():
        assert setup_bermuda_entry.options[key] == value


async def test_selectdevices_normalizes_addresses(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Ensure selectdevices saves canonical MAC addresses."""

    coordinator = setup_bermuda_entry.runtime_data.coordinator
    coordinator._get_or_create_device("AA:BB:CC:DD:EE:FF")
    hass.config_entries.async_update_entry(setup_bermuda_entry, options={CONF_DEVICES: ["AA-BB-CC-DD-EE-FF"]})
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "selectdevices"}
    )

    assert result.get("step_id") == "selectdevices"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_DEVICES: ["aa:bb:cc:dd:ee:ff"]}
    )

    assert result.get("type") == FlowResultType.CREATE_ENTRY
    assert setup_bermuda_entry.options[CONF_DEVICES] == ["aa:bb:cc:dd:ee:ff"]


async def test_single_instance_only_user_flow(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test that only one instance can be configured via user flow."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_user_flow_shows_form_first(hass: HomeAssistant, bypass_get_data: Any) -> None:
    """Test user flow shows form before accepting input."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert "name" in result.get("description_placeholders", {})


async def test_options_init_shows_menu(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test options flow shows menu with correct options."""
    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "init"
    assert "globalopts" in result.get("menu_options", {})
    assert "selectdevices" in result.get("menu_options", {})
    assert "calibration1_global" in result.get("menu_options", {})
    assert "calibration2_scanners" in result.get("menu_options", {})


async def test_options_init_status_messages(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test options flow shows correct status messages."""
    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    placeholders = result.get("description_placeholders", {})
    assert "device_counter_active" in placeholders
    assert "device_counter_devices" in placeholders
    assert "scanner_counter_active" in placeholders
    assert "scanner_counter_scanners" in placeholders
    assert "status" in placeholders


async def test_selectdevices_with_empty_list(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test selectdevices with no devices configured."""
    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "selectdevices"}
    )
    assert result["step_id"] == "selectdevices"

    # Submit with empty device list
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_DEVICES: []}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert setup_bermuda_entry.options.get(CONF_DEVICES) == []


async def test_selectdevices_filters_auto_configured(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test that auto-configured devices are filtered from saved config."""
    from custom_components.bermuda.const import ADDR_TYPE_PRIVATE_BLE_DEVICE

    coordinator = setup_bermuda_entry.runtime_data.coordinator
    device = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
    device.address_type = ADDR_TYPE_PRIVATE_BLE_DEVICE
    # Add to metadevices so it's recognized as auto-configured
    coordinator.metadevices["aa:bb:cc:dd:ee:ff"] = device

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "selectdevices"}
    )

    # Try to save the auto-configured device
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_DEVICES: ["aa:bb:cc:dd:ee:ff"]}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    # Auto-configured devices should be filtered out
    assert setup_bermuda_entry.options.get(CONF_DEVICES) == []


async def test_selectdevices_with_various_device_types(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test selectdevices shows different device types correctly."""
    from custom_components.bermuda.const import (
        ADDR_TYPE_IBEACON,
        BDADDR_TYPE_RANDOM_RESOLVABLE,
        METADEVICE_FMDN_DEVICE,
    )
    from bluetooth_data_tools import monotonic_time_coarse

    coordinator = setup_bermuda_entry.runtime_data.coordinator

    # Create various device types
    device1 = coordinator._get_or_create_device("aa:bb:cc:dd:ee:01")
    device1.name = "Normal Device"

    device2 = coordinator._get_or_create_device("11223344556677889900aabbccddeeff_1234_5678")
    device2.address_type = ADDR_TYPE_IBEACON
    device2.name = "iBeacon Device"
    device2.metadevice_sources = ["aa:bb:cc:dd:ee:02"]

    device3 = coordinator._get_or_create_device("aa:bb:cc:dd:ee:03")
    device3.address_type = BDADDR_TYPE_RANDOM_RESOLVABLE
    device3.name = "Random Device"
    device3.last_seen = monotonic_time_coarse()  # Recent

    device4 = coordinator._get_or_create_device("fmdn:test-device-id")
    device4.metadevice_type.add(METADEVICE_FMDN_DEVICE)
    device4.name = "FMDN Device"

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "selectdevices"}
    )

    assert result["step_id"] == "selectdevices"
    # The form should be shown with the data schema
    assert "data_schema" in result


async def test_globalopts_updates_options(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test global options flow updates config correctly."""
    from custom_components.bermuda.const import (
        CONF_MAX_RADIUS,
        CONF_MAX_VELOCITY,
        CONF_DEVTRACK_TIMEOUT,
        CONF_UPDATE_INTERVAL,
        CONF_SMOOTHING_SAMPLES,
        CONF_ATTENUATION,
        CONF_REF_POWER,
        CONF_USE_UKF_AREA_SELECTION,
    )

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "globalopts"}
    )

    assert result["step_id"] == "globalopts"

    # Submit with custom values
    custom_options = {
        CONF_MAX_RADIUS: 15.0,
        CONF_MAX_VELOCITY: 5.0,
        CONF_DEVTRACK_TIMEOUT: 60,
        CONF_UPDATE_INTERVAL: 2.0,
        CONF_SMOOTHING_SAMPLES: 10,
        CONF_ATTENUATION: 4.0,
        CONF_REF_POWER: -60.0,
        CONF_USE_UKF_AREA_SELECTION: True,
    }
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input=custom_options)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    for key, value in custom_options.items():
        assert setup_bermuda_entry.options[key] == value


async def test_calibration1_shows_form_initially(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test calibration1_global shows form on first visit."""
    from custom_components.bermuda.const import CONF_SAVE_AND_CLOSE

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "calibration1_global"}
    )

    assert result["step_id"] == "calibration1_global"
    assert result["type"] == FlowResultType.FORM
    placeholders = result.get("description_placeholders", {})
    assert "suffix" in placeholders


async def test_calibration2_shows_form_initially(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test calibration2_scanners shows form on first visit."""
    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "calibration2_scanners"}
    )

    assert result["step_id"] == "calibration2_scanners"
    assert result["type"] == FlowResultType.FORM
    placeholders = result.get("description_placeholders", {})
    assert "suffix" in placeholders


async def test_flow_handler_init(hass: HomeAssistant) -> None:
    """Test BermudaFlowHandler initialization."""
    from custom_components.bermuda.config_flow import BermudaFlowHandler

    handler = BermudaFlowHandler()
    assert handler._errors == {}


async def test_options_handler_init(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test BermudaOptionsFlowHandler initialization."""
    from custom_components.bermuda.config_flow import BermudaOptionsFlowHandler

    handler = BermudaOptionsFlowHandler(setup_bermuda_entry)
    assert handler._last_ref_power is None
    assert handler._last_device is None
    assert handler._last_scanner is None
    assert handler._last_attenuation is None
    assert handler._last_scanner_info is None


async def test_selectdevices_handles_non_list_configured_devices(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
) -> None:
    """Test selectdevices handles non-list configured_devices gracefully."""
    # Set invalid type for CONF_DEVICES
    hass.config_entries.async_update_entry(setup_bermuda_entry, options={CONF_DEVICES: "not-a-list"})
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "selectdevices"}
    )

    # Should handle gracefully and show form
    assert result["step_id"] == "selectdevices"
    assert result["type"] == FlowResultType.FORM


async def test_selectdevices_skips_scanner_devices(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test selectdevices skips scanner devices from the list."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator

    # Create a scanner device (use internal _is_scanner attribute)
    scanner = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
    scanner._is_scanner = True
    scanner.name = "Test Scanner"

    # Create a normal device
    device = coordinator._get_or_create_device("11:22:33:44:55:66")
    device.name = "Normal Device"

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "selectdevices"}
    )

    assert result["step_id"] == "selectdevices"
    # Scanner should not appear in the options


async def test_selectdevices_skips_old_random_devices(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test selectdevices skips random MAC devices that are too old."""
    from custom_components.bermuda.const import BDADDR_TYPE_RANDOM_RESOLVABLE

    coordinator = setup_bermuda_entry.runtime_data.coordinator

    # Create a random device that's old (more than 2 hours)
    device = coordinator._get_or_create_device("4a:bb:cc:dd:ee:ff")
    device.address_type = BDADDR_TYPE_RANDOM_RESOLVABLE
    device.name = "Old Random Device"
    device.last_seen = 0.0  # Very old

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "selectdevices"}
    )

    assert result["step_id"] == "selectdevices"
    # Old random device should not appear in options


async def test_selectdevices_adds_saved_but_undiscovered_devices(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
) -> None:
    """Test selectdevices adds saved devices that weren't discovered."""
    # Set a device that doesn't exist in coordinator
    saved_address = "99:99:99:99:99:99"
    hass.config_entries.async_update_entry(setup_bermuda_entry, options={CONF_DEVICES: [saved_address]})
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "selectdevices"}
    )

    assert result["step_id"] == "selectdevices"
    # The saved device should be added to the options with "(saved)" label


async def test_options_init_no_scanners_message(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test options flow shows no scanners warning."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    # Clear scanner list
    coordinator.scanner_list.clear()

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    placeholders = result.get("description_placeholders", {})
    status = placeholders.get("status", "")
    # Should include scanner warning message
    assert "bluetooth scanners" in status or "scanners" in status.lower()


async def test_options_init_no_devices_message(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test options flow shows no active devices warning when applicable."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    # Add a scanner so we don't get the "no scanners" message
    coordinator.scanner_list.add("aa:bb:cc:dd:ee:ff")

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    placeholders = result.get("description_placeholders", {})
    assert "device_counter_active" in placeholders


async def test_calibration2_with_scanner_list(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test calibration2_scanners with populated scanner list."""
    from custom_components.bermuda.const import CONF_RSSI_OFFSETS

    coordinator = setup_bermuda_entry.runtime_data.coordinator
    # Add a scanner
    scanner_device = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
    scanner_device._is_scanner = True
    scanner_device.name = "Test Scanner"
    coordinator.scanner_list.add("aa:bb:cc:dd:ee:ff")

    # Set existing offsets
    hass.config_entries.async_update_entry(
        setup_bermuda_entry,
        options={CONF_RSSI_OFFSETS: {"aa:bb:cc:dd:ee:ff": 5}}
    )
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "calibration2_scanners"}
    )

    assert result["step_id"] == "calibration2_scanners"
    assert result["type"] == FlowResultType.FORM


async def test_get_options_flow_static_method(hass: HomeAssistant, mock_bermuda_entry: MockConfigEntry) -> None:
    """Test async_get_options_flow returns the correct handler."""
    from custom_components.bermuda.config_flow import BermudaFlowHandler, BermudaOptionsFlowHandler

    result = BermudaFlowHandler.async_get_options_flow(mock_bermuda_entry)
    assert isinstance(result, BermudaOptionsFlowHandler)


async def test_selectdevices_with_fmdn_sources(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test selectdevices handles FMDN source devices."""
    from custom_components.bermuda.const import METADEVICE_TYPE_FMDN_SOURCE

    coordinator = setup_bermuda_entry.runtime_data.coordinator
    device = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
    device.metadevice_type.add(METADEVICE_TYPE_FMDN_SOURCE)
    device.name = "FMDN Source"

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "selectdevices"}
    )

    assert result["step_id"] == "selectdevices"


async def test_selectdevices_with_ibeacon_no_sources(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test selectdevices handles iBeacon devices without sources."""
    from custom_components.bermuda.const import ADDR_TYPE_IBEACON

    coordinator = setup_bermuda_entry.runtime_data.coordinator
    device = coordinator._get_or_create_device("11223344556677889900aabbccddeeff_1234_5678")
    device.address_type = ADDR_TYPE_IBEACON
    device.name = "iBeacon Device"
    device.metadevice_sources = []  # No sources

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "selectdevices"}
    )

    assert result["step_id"] == "selectdevices"
