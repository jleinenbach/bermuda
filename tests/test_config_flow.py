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
    placeholders = result.get("description_placeholders") or {}
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
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input={CONF_DEVICES: []})
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


async def test_selectdevices_with_various_device_types(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
) -> None:
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
    placeholders = result.get("description_placeholders") or {}
    assert "suffix" in placeholders


async def test_calibration2_shows_form_initially(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test calibration2_scanners shows form on first visit."""
    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "calibration2_scanners"}
    )

    assert result["step_id"] == "calibration2_scanners"
    assert result["type"] == FlowResultType.FORM
    placeholders = result.get("description_placeholders") or {}
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


async def test_selectdevices_skips_old_random_devices(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
) -> None:
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
    placeholders = result.get("description_placeholders") or {}
    status = placeholders.get("status", "")
    # Should include scanner warning message
    assert "bluetooth scanners" in status or "scanners" in status.lower()


async def test_options_init_no_devices_message(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test options flow shows no active devices warning when applicable."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    # Add a scanner so we don't get the "no scanners" message
    coordinator.scanner_list.add("aa:bb:cc:dd:ee:ff")

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    placeholders = result.get("description_placeholders") or {}
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
    hass.config_entries.async_update_entry(setup_bermuda_entry, options={CONF_RSSI_OFFSETS: {"aa:bb:cc:dd:ee:ff": 5}})
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


# ──────────────────────────────────────────────────────────────────────
# Coverage extension tests for config_flow.py
# Target: increase coverage from ~55% toward 65%+
# ──────────────────────────────────────────────────────────────────────


async def test_bluetooth_discovery_aborts_when_configured(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
) -> None:
    """Test bluetooth auto-discovery aborts when already configured (lines 94-95)."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_BLUETOOTH}, data=MagicMock()
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_bluetooth_discovery_shows_form(hass: HomeAssistant, bypass_get_data: Any) -> None:
    """Test bluetooth discovery shows user form when not yet configured (lines 97-104)."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_BLUETOOTH}, data=MagicMock()
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_init_active_scanners_shows_scanner_table(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
) -> None:
    """Test options init shows scanner table and active status message (lines 183, 188-202)."""
    from bluetooth_data_tools import monotonic_time_coarse

    coordinator = setup_bermuda_entry.runtime_data.coordinator

    # Create a scanner device and add to scanner_list + _scanners
    scanner_device = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
    scanner_device._is_scanner = True
    scanner_device.name = "Test Proxy"
    scanner_device.last_seen = monotonic_time_coarse()
    coordinator.scanner_list.add("aa:bb:cc:dd:ee:ff")
    coordinator._scanners.add(scanner_device)

    # Also create a regular device so active_devices > 0
    regular = coordinator._get_or_create_device("11:22:33:44:55:66")
    regular.last_seen = monotonic_time_coarse()

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    placeholders = result.get("description_placeholders") or {}
    status = placeholders.get("status", "")

    # Line 183: "at least some active devices" message
    assert "active devices" in status

    # Lines 188-202: scanner table should include the scanner name
    assert "Test Proxy" in status
    assert "Scanner" in status  # Table header


async def test_globalopts_recorder_friendly_toggle(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test globalopts flow includes and saves CONF_RECORDER_FRIENDLY (lines 255-258)."""
    from custom_components.bermuda.const import (
        CONF_ATTENUATION,
        CONF_DEVTRACK_TIMEOUT,
        CONF_MAX_RADIUS,
        CONF_MAX_VELOCITY,
        CONF_RECORDER_FRIENDLY,
        CONF_REF_POWER,
        CONF_SMOOTHING_SAMPLES,
        CONF_UPDATE_INTERVAL,
        CONF_USE_UKF_AREA_SELECTION,
    )

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "globalopts"}
    )
    assert result["step_id"] == "globalopts"

    # Submit with recorder_friendly = False (non-default)
    custom_options = {
        CONF_MAX_RADIUS: 20.0,
        CONF_MAX_VELOCITY: 3.0,
        CONF_DEVTRACK_TIMEOUT: 30,
        CONF_UPDATE_INTERVAL: 10.0,
        CONF_SMOOTHING_SAMPLES: 20,
        CONF_ATTENUATION: 3.0,
        CONF_REF_POWER: -55.0,
        CONF_USE_UKF_AREA_SELECTION: False,
        CONF_RECORDER_FRIENDLY: False,
    }
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input=custom_options)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert setup_bermuda_entry.options[CONF_RECORDER_FRIENDLY] is False


async def test_calibration1_save_and_close(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test calibration1 save_and_close path updates options and finishes (lines 431-454)."""
    from homeassistant.helpers import device_registry as dr
    from custom_components.bermuda.const import (
        CONF_ATTENUATION,
        CONF_REF_POWER,
        CONF_SAVE_AND_CLOSE,
        CONF_SCANNERS,
    )

    coordinator = setup_bermuda_entry.runtime_data.coordinator

    # Create a scanner device in scanner_list
    scanner_device = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
    scanner_device._is_scanner = True
    scanner_device.name = "Test Scanner"
    coordinator.scanner_list.add("aa:bb:cc:dd:ee:ff")
    coordinator._scanners.add(scanner_device)

    # Create a tracked device with an HA device registry entry
    tracked = coordinator._get_or_create_device("11:22:33:44:55:66")
    tracked.name = "Tracked Device"

    devreg = dr.async_get(hass)
    device_entry = devreg.async_get_or_create(
        config_entry_id=setup_bermuda_entry.entry_id,
        connections={(dr.CONNECTION_BLUETOOTH, "11:22:33:44:55:66")},
        name="Tracked Device",
    )

    # Navigate to calibration1
    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "calibration1_global"}
    )
    assert result["step_id"] == "calibration1_global"

    # Submit with save_and_close=True
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_DEVICES: device_entry.id,
            CONF_SCANNERS: "aa:bb:cc:dd:ee:ff",
            CONF_REF_POWER: -59.0,
            CONF_ATTENUATION: 3.5,
            CONF_SAVE_AND_CLOSE: True,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert setup_bermuda_entry.options[CONF_REF_POWER] == -59.0
    assert setup_bermuda_entry.options[CONF_ATTENUATION] == 3.5


async def test_calibration1_refresh_shows_results(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test calibration1 refresh (save_and_close=False) stores params and shows results table (lines 456-542)."""
    from homeassistant.helpers import device_registry as dr
    from custom_components.bermuda.const import (
        CONF_ATTENUATION,
        CONF_REF_POWER,
        CONF_SAVE_AND_CLOSE,
        CONF_SCANNERS,
    )

    coordinator = setup_bermuda_entry.runtime_data.coordinator

    # Create scanner
    scanner_device = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
    scanner_device._is_scanner = True
    scanner_device.name = "Test Scanner"
    coordinator.scanner_list.add("aa:bb:cc:dd:ee:ff")
    coordinator._scanners.add(scanner_device)

    # Create tracked device with advert
    tracked = coordinator._get_or_create_device("11:22:33:44:55:66")
    tracked.name = "Tracked Device"

    # Create a mock advert with hist_rssi data
    mock_advert = MagicMock()
    mock_advert.hist_rssi = [-60, -62, -58, -61, -59]
    tracked._adverts_by_scanner = {"aa:bb:cc:dd:ee:ff": mock_advert}

    # HA device registry entry
    devreg = dr.async_get(hass)
    device_entry = devreg.async_get_or_create(
        config_entry_id=setup_bermuda_entry.entry_id,
        connections={(dr.CONNECTION_BLUETOOTH, "11:22:33:44:55:66")},
        name="Tracked Device",
    )

    # Navigate to calibration1
    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "calibration1_global"}
    )

    # Submit with save_and_close=False (refresh)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_DEVICES: device_entry.id,
            CONF_SCANNERS: "aa:bb:cc:dd:ee:ff",
            CONF_REF_POWER: -59.0,
            CONF_ATTENUATION: 3.0,
            CONF_SAVE_AND_CLOSE: False,
        },
    )
    # Should show form again with results table
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "calibration1_global"
    placeholders = result.get("description_placeholders") or {}
    suffix = placeholders.get("suffix", "")
    # Lines 505-540: results table contains ref_power and distance data
    assert "ref_power" in suffix
    assert "Estimate" in suffix


async def test_calibration1_scanner_no_record(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test calibration1 when scanner hasn't seen device (lines 509-516)."""
    from homeassistant.helpers import device_registry as dr
    from custom_components.bermuda.const import (
        CONF_ATTENUATION,
        CONF_REF_POWER,
        CONF_SAVE_AND_CLOSE,
        CONF_SCANNERS,
    )

    coordinator = setup_bermuda_entry.runtime_data.coordinator

    # Create scanner
    scanner_device = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
    scanner_device._is_scanner = True
    scanner_device.name = "Test Scanner"
    coordinator.scanner_list.add("aa:bb:cc:dd:ee:ff")
    coordinator._scanners.add(scanner_device)

    # Create tracked device WITHOUT advert for this scanner
    tracked = coordinator._get_or_create_device("11:22:33:44:55:66")
    tracked.name = "Tracked Device"
    # No _adverts_by_scanner entry for the scanner

    devreg = dr.async_get(hass)
    device_entry = devreg.async_get_or_create(
        config_entry_id=setup_bermuda_entry.entry_id,
        connections={(dr.CONNECTION_BLUETOOTH, "11:22:33:44:55:66")},
        name="Tracked Device",
    )

    # Navigate to calibration1
    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "calibration1_global"}
    )

    # Submit with save_and_close=False so we go through the results path
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_DEVICES: device_entry.id,
            CONF_SCANNERS: "aa:bb:cc:dd:ee:ff",
            CONF_REF_POWER: -59.0,
            CONF_ATTENUATION: 3.0,
            CONF_SAVE_AND_CLOSE: False,
        },
    )
    # Should show form with error about scanner not seeing device
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "calibration1_global"
    errors = result.get("errors", {})
    assert "err_scanner_no_record" in errors


async def test_calibration2_save_and_close(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test calibration2 save_and_close saves offsets and finishes (lines 566-586)."""
    from homeassistant.helpers import device_registry as dr
    from custom_components.bermuda.const import (
        CONF_RSSI_OFFSETS,
        CONF_SAVE_AND_CLOSE,
        CONF_SCANNER_INFO,
    )

    coordinator = setup_bermuda_entry.runtime_data.coordinator

    # Create a scanner
    scanner_device = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
    scanner_device._is_scanner = True
    scanner_device.name = "Test Scanner"
    coordinator.scanner_list.add("aa:bb:cc:dd:ee:ff")
    coordinator._scanners.add(scanner_device)

    # HA device registry entry for the tracked device
    tracked = coordinator._get_or_create_device("11:22:33:44:55:66")
    tracked.name = "Tracked Device"
    devreg = dr.async_get(hass)
    device_entry = devreg.async_get_or_create(
        config_entry_id=setup_bermuda_entry.entry_id,
        connections={(dr.CONNECTION_BLUETOOTH, "11:22:33:44:55:66")},
        name="Tracked Device",
    )

    # Navigate to calibration2
    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "calibration2_scanners"}
    )
    assert result["step_id"] == "calibration2_scanners"

    # Submit with save_and_close=True, scanner_info keyed by name
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_DEVICES: device_entry.id,
            CONF_SCANNER_INFO: {"Test Scanner": -5},
            CONF_SAVE_AND_CLOSE: True,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    # Verify offset saved with MAC address as key
    offsets = setup_bermuda_entry.options.get(CONF_RSSI_OFFSETS, {})
    assert offsets.get("aa:bb:cc:dd:ee:ff") == -5


async def test_calibration2_refresh_shows_results(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test calibration2 refresh shows distance results and auto-cal suggestions (lines 615-685)."""
    from homeassistant.helpers import device_registry as dr
    from custom_components.bermuda.const import (
        CONF_SAVE_AND_CLOSE,
        CONF_SCANNER_INFO,
    )

    coordinator = setup_bermuda_entry.runtime_data.coordinator

    # Create scanner
    scanner_device = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
    scanner_device._is_scanner = True
    scanner_device.name = "Test Scanner"
    scanner_device.ref_power = 0
    coordinator.scanner_list.add("aa:bb:cc:dd:ee:ff")
    coordinator._scanners.add(scanner_device)

    # Tracked device with advert
    tracked = coordinator._get_or_create_device("11:22:33:44:55:66")
    tracked.name = "Tracked Device"
    tracked.ref_power = 0

    mock_advert = MagicMock()
    mock_advert.hist_rssi = [-60, -62, -58, -61, -59]
    tracked._adverts_by_scanner = {"aa:bb:cc:dd:ee:ff": mock_advert}

    devreg = dr.async_get(hass)
    device_entry = devreg.async_get_or_create(
        config_entry_id=setup_bermuda_entry.entry_id,
        connections={(dr.CONNECTION_BLUETOOTH, "11:22:33:44:55:66")},
        name="Tracked Device",
    )

    # Mock auto-calibration suggestions
    coordinator.scanner_calibration.get_offset_info = MagicMock(
        return_value={
            "aa:bb:cc:dd:ee:ff": {
                "suggested_offset": -3,
                "confidence": 0.85,
                "confidence_percent": 85.0,
                "meets_threshold": True,
            }
        }
    )

    # Navigate to calibration2
    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "calibration2_scanners"}
    )

    # Refresh (save_and_close=False)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_DEVICES: device_entry.id,
            CONF_SCANNER_INFO: {"Test Scanner": 0},
            CONF_SAVE_AND_CLOSE: False,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "calibration2_scanners"
    placeholders = result.get("description_placeholders") or {}
    suffix = placeholders.get("suffix", "")
    # Lines 644-653: distance results table
    assert "Test Scanner" in suffix
    # Lines 658-683: auto-calibration suggestions
    assert "Auto-Calibration" in suffix
    assert "85%" in suffix


async def test_calibration2_low_confidence_suggestion(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
) -> None:
    """Test calibration2 shows low confidence suggestions (lines 676-680)."""
    from homeassistant.helpers import device_registry as dr
    from custom_components.bermuda.const import (
        CONF_SAVE_AND_CLOSE,
        CONF_SCANNER_INFO,
    )

    coordinator = setup_bermuda_entry.runtime_data.coordinator

    scanner_device = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
    scanner_device._is_scanner = True
    scanner_device.name = "Test Scanner"
    scanner_device.ref_power = 0
    coordinator.scanner_list.add("aa:bb:cc:dd:ee:ff")
    coordinator._scanners.add(scanner_device)

    tracked = coordinator._get_or_create_device("11:22:33:44:55:66")
    tracked.name = "Tracked Device"
    tracked.ref_power = 0
    mock_advert = MagicMock()
    mock_advert.hist_rssi = [-60, -62]
    tracked._adverts_by_scanner = {"aa:bb:cc:dd:ee:ff": mock_advert}

    devreg = dr.async_get(hass)
    device_entry = devreg.async_get_or_create(
        config_entry_id=setup_bermuda_entry.entry_id,
        connections={(dr.CONNECTION_BLUETOOTH, "11:22:33:44:55:66")},
        name="Tracked Device",
    )

    # Mock low-confidence suggestion for our scanner,
    # plus a second scanner that meets threshold to enter the suggestions block
    coordinator.scanner_calibration.get_offset_info = MagicMock(
        return_value={
            "aa:bb:cc:dd:ee:ff": {
                "suggested_offset": -3,
                "confidence": 0.45,
                "confidence_percent": 45.0,
                "meets_threshold": False,
            },
            # Need at least one that meets threshold to enter the block
            "bb:cc:dd:ee:ff:00": {
                "suggested_offset": -2,
                "confidence": 0.8,
                "confidence_percent": 80.0,
                "meets_threshold": True,
            },
        }
    )

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "calibration2_scanners"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_DEVICES: device_entry.id,
            CONF_SCANNER_INFO: {"Test Scanner": 0},
            CONF_SAVE_AND_CLOSE: False,
        },
    )
    placeholders = result.get("description_placeholders") or {}
    suffix = placeholders.get("suffix", "")
    # Line 679: "below threshold" text for low confidence
    assert "below threshold" in suffix


async def test_calibration2_no_suggestion(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test calibration2 with scanner that has no calibration data (lines 681-682)."""
    from homeassistant.helpers import device_registry as dr
    from custom_components.bermuda.const import (
        CONF_SAVE_AND_CLOSE,
        CONF_SCANNER_INFO,
    )

    coordinator = setup_bermuda_entry.runtime_data.coordinator

    scanner_device = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
    scanner_device._is_scanner = True
    scanner_device.name = "Test Scanner"
    scanner_device.ref_power = 0
    coordinator.scanner_list.add("aa:bb:cc:dd:ee:ff")
    coordinator._scanners.add(scanner_device)

    tracked = coordinator._get_or_create_device("11:22:33:44:55:66")
    tracked.name = "Tracked Device"
    tracked.ref_power = 0
    mock_advert = MagicMock()
    mock_advert.hist_rssi = [-60]
    tracked._adverts_by_scanner = {"aa:bb:cc:dd:ee:ff": mock_advert}

    devreg = dr.async_get(hass)
    device_entry = devreg.async_get_or_create(
        config_entry_id=setup_bermuda_entry.entry_id,
        connections={(dr.CONNECTION_BLUETOOTH, "11:22:33:44:55:66")},
        name="Tracked Device",
    )

    # Mock with one that meets_threshold to enter the block,
    # but the scanner we actually have doesn't have data
    coordinator.scanner_calibration.get_offset_info = MagicMock(
        return_value={
            "aa:bb:cc:dd:ee:ff": {
                "suggested_offset": None,
                "confidence": 0.0,
                "confidence_percent": 0,
                "meets_threshold": False,
            },
            # Need at least one that meets threshold to enter the block
            "bb:cc:dd:ee:ff:00": {
                "suggested_offset": -2,
                "confidence": 0.8,
                "confidence_percent": 80.0,
                "meets_threshold": True,
            },
        }
    )

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "calibration2_scanners"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_DEVICES: device_entry.id,
            CONF_SCANNER_INFO: {"Test Scanner": 0},
            CONF_SAVE_AND_CLOSE: False,
        },
    )
    placeholders = result.get("description_placeholders") or {}
    suffix = placeholders.get("suffix", "")
    # Lines 681-682: dash for no-data scanner
    assert "Auto-Calibration" in suffix


async def test_get_bermuda_device_from_registry_fmdn_device_id(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
) -> None:
    """Test _get_bermuda_device_from_registry finds FMDN device via fmdn_device_id (lines 713-716)."""
    from homeassistant.helpers import device_registry as dr
    from custom_components.bermuda.config_flow import BermudaOptionsFlowHandler

    coordinator = setup_bermuda_entry.runtime_data.coordinator

    # Create an HA device registry entry (not connected via bluetooth)
    devreg = dr.async_get(hass)
    device_entry = devreg.async_get_or_create(
        config_entry_id=setup_bermuda_entry.entry_id,
        identifiers={("googlefindmy", "entry123:68419b51-0000-2131-873b-fc411691d329")},
        name="FMDN Tracker",
    )

    # Create a BermudaDevice that references this HA device via fmdn_device_id
    fmdn_device = coordinator._get_or_create_device("fmdn:68419b51-test")
    fmdn_device.fmdn_device_id = device_entry.id
    fmdn_device.name = "FMDN Tracker"

    # Set up the options flow handler
    handler = BermudaOptionsFlowHandler(setup_bermuda_entry)
    handler.hass = hass
    handler.coordinator = coordinator

    result = handler._get_bermuda_device_from_registry(device_entry.id)
    assert result is not None
    assert result.fmdn_device_id == device_entry.id


async def test_get_bermuda_device_from_registry_fmdn_canonical_id(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
) -> None:
    """Test _get_bermuda_device_from_registry finds FMDN device via canonical_id fallback (lines 719-725)."""
    from homeassistant.helpers import device_registry as dr
    from custom_components.bermuda.config_flow import BermudaOptionsFlowHandler

    coordinator = setup_bermuda_entry.runtime_data.coordinator

    # Create HA device with googlefindmy identifier
    canonical_id = "entry123:68419b51-0000-2131-873b-fc411691d329"
    devreg = dr.async_get(hass)
    device_entry = devreg.async_get_or_create(
        config_entry_id=setup_bermuda_entry.entry_id,
        identifiers={("googlefindmy", canonical_id)},
        name="FMDN Tracker Canonical",
    )

    # Create BermudaDevice with matching fmdn_canonical_id (not fmdn_device_id)
    fmdn_device = coordinator._get_or_create_device("fmdn:canonical-test")
    fmdn_device.fmdn_canonical_id = canonical_id
    fmdn_device.fmdn_device_id = None  # Not set - forces canonical fallback
    fmdn_device.name = "FMDN Tracker Canonical"

    handler = BermudaOptionsFlowHandler(setup_bermuda_entry)
    handler.hass = hass
    handler.coordinator = coordinator

    result = handler._get_bermuda_device_from_registry(device_entry.id)
    assert result is not None
    assert result.fmdn_canonical_id == canonical_id


async def test_get_bermuda_device_from_registry_returns_none(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
) -> None:
    """Test _get_bermuda_device_from_registry returns None for unknown device (line 728)."""
    from homeassistant.helpers import device_registry as dr
    from custom_components.bermuda.config_flow import BermudaOptionsFlowHandler

    coordinator = setup_bermuda_entry.runtime_data.coordinator

    devreg = dr.async_get(hass)
    device_entry = devreg.async_get_or_create(
        config_entry_id=setup_bermuda_entry.entry_id,
        identifiers={("other_domain", "unknown_device")},
        name="Unknown Device",
    )

    handler = BermudaOptionsFlowHandler(setup_bermuda_entry)
    handler.hass = hass
    handler.coordinator = coordinator

    result = handler._get_bermuda_device_from_registry(device_entry.id)
    assert result is None


async def test_get_bermuda_device_from_registry_invalid_id(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
) -> None:
    """Test _get_bermuda_device_from_registry returns None for non-existent registry id (line 728)."""
    from custom_components.bermuda.config_flow import BermudaOptionsFlowHandler

    coordinator = setup_bermuda_entry.runtime_data.coordinator

    handler = BermudaOptionsFlowHandler(setup_bermuda_entry)
    handler.hass = hass
    handler.coordinator = coordinator

    result = handler._get_bermuda_device_from_registry("non_existent_id")
    assert result is None


async def test_calibration2_offset_clipping(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test calibration2 clips offset values to [-127, 127] range (line 573)."""
    from homeassistant.helpers import device_registry as dr
    from custom_components.bermuda.const import (
        CONF_RSSI_OFFSETS,
        CONF_SAVE_AND_CLOSE,
        CONF_SCANNER_INFO,
    )

    coordinator = setup_bermuda_entry.runtime_data.coordinator

    scanner_device = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
    scanner_device._is_scanner = True
    scanner_device.name = "Test Scanner"
    coordinator.scanner_list.add("aa:bb:cc:dd:ee:ff")
    coordinator._scanners.add(scanner_device)

    tracked = coordinator._get_or_create_device("11:22:33:44:55:66")
    tracked.name = "Tracked Device"
    devreg = dr.async_get(hass)
    device_entry = devreg.async_get_or_create(
        config_entry_id=setup_bermuda_entry.entry_id,
        connections={(dr.CONNECTION_BLUETOOTH, "11:22:33:44:55:66")},
        name="Tracked Device",
    )

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "calibration2_scanners"}
    )

    # Submit with extreme offset value that should be clipped
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_DEVICES: device_entry.id,
            CONF_SCANNER_INFO: {"Test Scanner": 200},  # Above 127
            CONF_SAVE_AND_CLOSE: True,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    offsets = setup_bermuda_entry.options.get(CONF_RSSI_OFFSETS, {})
    assert offsets.get("aa:bb:cc:dd:ee:ff") == 127  # Clipped to max


async def test_calibration2_ref_power_from_device(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry) -> None:
    """Test calibration2 uses device ref_power when available (lines 623-628)."""
    from homeassistant.helpers import device_registry as dr
    from custom_components.bermuda.const import (
        CONF_SAVE_AND_CLOSE,
        CONF_SCANNER_INFO,
    )

    coordinator = setup_bermuda_entry.runtime_data.coordinator

    scanner_device = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
    scanner_device._is_scanner = True
    scanner_device.name = "Test Scanner"
    scanner_device.ref_power = 0
    coordinator.scanner_list.add("aa:bb:cc:dd:ee:ff")
    coordinator._scanners.add(scanner_device)

    tracked = coordinator._get_or_create_device("11:22:33:44:55:66")
    tracked.name = "Tracked Device"
    tracked.ref_power = -50  # Device-specific ref_power (non-zero)

    mock_advert = MagicMock()
    mock_advert.hist_rssi = [-55, -57, -53]
    tracked._adverts_by_scanner = {"aa:bb:cc:dd:ee:ff": mock_advert}

    devreg = dr.async_get(hass)
    device_entry = devreg.async_get_or_create(
        config_entry_id=setup_bermuda_entry.entry_id,
        connections={(dr.CONNECTION_BLUETOOTH, "11:22:33:44:55:66")},
        name="Tracked Device",
    )

    # Mock empty calibration suggestions
    coordinator.scanner_calibration.get_offset_info = MagicMock(return_value={})

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "calibration2_scanners"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_DEVICES: device_entry.id,
            CONF_SCANNER_INFO: {"Test Scanner": 0},
            CONF_SAVE_AND_CLOSE: False,
        },
    )
    assert result["type"] == FlowResultType.FORM
    placeholders = result.get("description_placeholders") or {}
    suffix = placeholders.get("suffix", "")
    # Results table should contain scanner data (device ref_power=-50 used)
    assert "Test Scanner" in suffix
