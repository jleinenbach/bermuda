"""Test Bermuda services module."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.json import json_bytes

from custom_components.bermuda.area_selection import AreaTests
from custom_components.bermuda.const import ADDR_TYPE_PRIVATE_BLE_DEVICE, CONF_DEVICES, DOMAIN
from custom_components.bermuda.services import (
    DUMP_DEVICE_SOFT_LIMIT,
    BermudaServiceHandler,
)


class TestBermudaServiceHandler:
    """Tests for BermudaServiceHandler class."""

    def _create_handler(
        self,
        scanner_list: list[str] | None = None,
        devices: dict | None = None,
        options: dict | None = None,
        pb_state_sources: dict | None = None,
    ) -> BermudaServiceHandler:
        """Create a BermudaServiceHandler instance for testing."""
        mock_coordinator = MagicMock()
        mock_coordinator.scanner_list = scanner_list or []
        mock_coordinator.devices = devices or {}
        mock_coordinator.options = options or {}
        mock_coordinator.pb_state_sources = pb_state_sources or {}
        mock_coordinator.count_active_devices = MagicMock(return_value=5)
        mock_coordinator.count_active_scanners = MagicMock(return_value=3)

        return BermudaServiceHandler(mock_coordinator)

    def test_init(self) -> None:
        """Test that handler initializes correctly."""
        handler = self._create_handler()

        assert handler.redactions == {}
        assert handler.stamp_redactions_expiry is None

    def test_redaction_list_update_scanners(self) -> None:
        """Test that redaction_list_update adds scanners."""
        handler = self._create_handler(scanner_list=["AA:BB:CC:DD:EE:FF"])

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            handler.redaction_list_update()

        # Should have redactions for different MAC formats
        assert "aa:bb:cc:dd:ee:ff" in handler.redactions
        # Should update expiry stamp
        assert handler.stamp_redactions_expiry is not None

    def test_redaction_list_update_configured_devices_mac(self) -> None:
        """Test that redaction_list_update adds configured MAC devices."""
        handler = self._create_handler(options={CONF_DEVICES: ["11:22:33:44:55:66"]})

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            handler.redaction_list_update()

        assert "11:22:33:44:55:66" in handler.redactions

    def test_redaction_list_update_configured_devices_ibeacon(self) -> None:
        """Test that redaction_list_update adds configured iBeacon devices."""
        ibeacon_addr = "12345678-1234-1234-1234-123456789012_1_2"
        handler = self._create_handler(options={CONF_DEVICES: [ibeacon_addr]})

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            handler.redaction_list_update()

        assert ibeacon_addr in handler.redactions

    def test_redaction_list_update_other_devices(self) -> None:
        """Test that redaction_list_update adds other devices."""
        mock_device = MagicMock()
        mock_device.address_type = "public"

        handler = self._create_handler(devices={"aa:bb:cc:dd:ee:01": mock_device})

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            handler.redaction_list_update()

        assert "aa:bb:cc:dd:ee:01" in handler.redactions

    def test_redaction_list_update_private_ble_device(self) -> None:
        """Test that redaction_list_update handles Private BLE devices."""
        mock_device = MagicMock()
        mock_device.address_type = ADDR_TYPE_PRIVATE_BLE_DEVICE

        handler = self._create_handler(devices={"irk_address": mock_device})

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            handler.redaction_list_update()

        assert "irk_address" in handler.redactions
        assert "IRK_DEV" in handler.redactions["irk_address"]

    def test_redact_data_string_full_match(self) -> None:
        """Test that redact_data handles full string match."""
        handler = self._create_handler()
        handler.redactions = {"aa:bb:cc:dd:ee:ff": "REDACTED_MAC"}
        handler.stamp_redactions_expiry = 200.0

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            result = handler.redact_data("AA:BB:CC:DD:EE:FF")

        assert result == "REDACTED_MAC"

    def test_redact_data_string_partial_match(self) -> None:
        """Test that redact_data handles partial string match."""
        handler = self._create_handler()
        handler.redactions = {"aa:bb:cc:dd:ee:ff": "REDACTED"}
        handler.stamp_redactions_expiry = 200.0

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            result = handler.redact_data("Device: aa:bb:cc:dd:ee:ff found")

        assert "REDACTED" in result

    def test_redact_data_dict(self) -> None:
        """Test that redact_data handles dicts."""
        handler = self._create_handler()
        handler.redactions = {"aa:bb:cc:dd:ee:ff": "REDACTED_MAC"}
        handler.stamp_redactions_expiry = 200.0

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            result = handler.redact_data({"key": "aa:bb:cc:dd:ee:ff", "other": "value"})

        assert result["key"] == "REDACTED_MAC"
        assert result["other"] == "value"

    def test_redact_data_list(self) -> None:
        """Test that redact_data handles lists."""
        handler = self._create_handler()
        handler.redactions = {"aa:bb:cc:dd:ee:ff": "REDACTED"}
        handler.stamp_redactions_expiry = 200.0

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            result = handler.redact_data(["aa:bb:cc:dd:ee:ff", "other"])

        assert result[0] == "REDACTED"
        assert result[1] == "other"

    def test_redact_data_other_types(self) -> None:
        """Test that redact_data passes through other types."""
        handler = self._create_handler()
        handler.stamp_redactions_expiry = 200.0

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            assert handler.redact_data(42) == 42
            assert handler.redact_data(None) is None
            assert handler.redact_data(3.14) == 3.14

    def test_redact_data_dataclass(self) -> None:
        """MAC addresses inside a dataclass value (such as AreaTests in dump_devices) are redacted."""
        handler = self._create_handler()
        handler.stamp_redactions_expiry = 200.0
        tests = AreaTests(
            scannername=("aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66"), offline_scanner_addrs="aa:bb:cc:dd:ee:ff"
        )

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            result = handler.redact_data({"area_tests": tests})

        rendered = json_bytes(result).decode()
        assert "aa:bb:cc:dd:ee:ff" not in rendered
        assert "11:22:33:44:55:66" not in rendered
        assert result["area_tests"]["decision_path"] == "UNKNOWN"
        assert tests.scannername == ("aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66")

    def test_redact_data_tuple(self) -> None:
        """MAC addresses inside a tuple are redacted."""
        handler = self._create_handler()
        handler.stamp_redactions_expiry = 200.0

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            result = handler.redact_data(("aa:bb:cc:dd:ee:ff", "other"))

        assert "aa:bb:cc:dd:ee:ff" not in json_bytes(result).decode()
        assert result[1] == "other"

    def test_redact_data_generic_mac_redaction(self) -> None:
        """Test that redact_data handles generic MAC addresses."""
        handler = self._create_handler()
        handler.stamp_redactions_expiry = 200.0

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            # A MAC that's not in the redaction list should still be redacted generically
            result = handler.redact_data("unknown: 99:88:77:66:55:44")

        # Generic redaction keeps first and last octets
        assert "99" in result
        assert "44" in result
        assert "xx" in result

    def test_redact_data_two_hits_in_one_string(self) -> None:
        """T-R3: two known addresses in one string are both replaced."""
        handler = self._create_handler(scanner_list=["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"])

        result = handler.redact_data("aa:bb:cc:dd:ee:01__aa:bb:cc:dd:ee:02")

        assert result.count("::SCANNER_") == 2
        assert "xx:xx" not in result

    def test_redact_other_no_raw_id(self) -> None:
        """T-R13: placeholders for non-MAC ids carry no raw id and distinct numbers."""
        seen = MagicMock()
        seen.address_type = "fmdn"
        handler = self._create_handler(
            options={CONF_DEVICES: ["fmdn:cfgonealpha", "fmdn:cfgtwobravo"]},
            devices={"fmdn:seencharlie": seen},
        )

        handler.redaction_list_update()

        values = [handler.redactions[k] for k in ("fmdn:cfgonealpha", "fmdn:cfgtwobravo", "fmdn:seencharlie")]
        for raw, value in zip(("cfgonealpha", "cfgtwobravo", "seencharlie"), values, strict=True):
            assert raw not in value.lower()
        assert len(set(values)) == 3

    @pytest.mark.asyncio
    async def test_async_dump_devices_all(self) -> None:
        """Test async_dump_devices returns all devices."""
        mock_device = MagicMock()
        mock_device.to_dict = MagicMock(return_value={"name": "Test Device"})

        handler = self._create_handler(
            devices={"aa:bb:cc:dd:ee:ff": mock_device},
        )

        mock_call = MagicMock()
        mock_call.data = {}

        result = await handler.async_dump_devices(mock_call)

        assert "aa:bb:cc:dd:ee:ff" in result
        assert result["aa:bb:cc:dd:ee:ff"]["name"] == "Test Device"

    @pytest.mark.asyncio
    async def test_async_dump_devices_specific_address(self) -> None:
        """Test async_dump_devices with specific address filter."""
        mock_device1 = MagicMock()
        mock_device1.to_dict = MagicMock(return_value={"name": "Device 1"})
        mock_device2 = MagicMock()
        mock_device2.to_dict = MagicMock(return_value={"name": "Device 2"})

        handler = self._create_handler(
            devices={
                "aa:bb:cc:dd:ee:01": mock_device1,
                "aa:bb:cc:dd:ee:02": mock_device2,
            },
        )

        mock_call = MagicMock()
        mock_call.data = {"addresses": "AA:BB:CC:DD:EE:01"}

        result = await handler.async_dump_devices(mock_call)

        assert "aa:bb:cc:dd:ee:01" in result
        assert "aa:bb:cc:dd:ee:02" not in result

    @pytest.mark.asyncio
    async def test_async_dump_devices_configured_only(self) -> None:
        """Test async_dump_devices with configured_devices filter."""
        mock_device = MagicMock()
        mock_device.to_dict = MagicMock(return_value={"name": "Test Device"})

        handler = self._create_handler(
            scanner_list=["scanner:address"],
            devices={"scanner:address": mock_device},
            options={CONF_DEVICES: []},
        )

        mock_call = MagicMock()
        mock_call.data = {"configured_devices": True}

        result = await handler.async_dump_devices(mock_call)

        assert "scanner:address" in result

    @pytest.mark.asyncio
    async def test_async_dump_devices_with_redaction(self) -> None:
        """Test async_dump_devices with redaction enabled."""
        mock_device = MagicMock()
        mock_device.to_dict = MagicMock(return_value={"mac": "aa:bb:cc:dd:ee:ff"})

        handler = self._create_handler(
            devices={"aa:bb:cc:dd:ee:ff": mock_device},
        )

        mock_call = MagicMock()
        mock_call.data = {"redact": True}

        with patch("custom_components.bermuda.services.monotonic_time_coarse", return_value=100.0):
            result = await handler.async_dump_devices(mock_call)

        # MAC should be redacted in the result
        assert "aa:bb:cc:dd:ee:ff" not in str(result)

    @pytest.mark.asyncio
    async def test_async_dump_devices_soft_limit(self) -> None:
        """Test async_dump_devices applies soft limit for large device counts."""
        # Create many mock devices
        devices = {}
        for i in range(DUMP_DEVICE_SOFT_LIMIT + 100):
            mock_device = MagicMock()
            mock_device.to_dict = MagicMock(return_value={"name": f"Device {i}"})
            devices[f"aa:bb:cc:dd:{i:02d}:{i:02d}"] = mock_device

        handler = self._create_handler(
            devices=devices,
            scanner_list=["scanner:01"],
            options={CONF_DEVICES: ["cfg:device"]},
        )

        mock_call = MagicMock()
        mock_call.data = {}

        result = await handler.async_dump_devices(mock_call)

        # Should have summary indicating limit was applied
        assert "summary" in result
        assert result["summary"]["limited"] is True


class TestServicesIntegration:
    """Integration tests for services module."""

    def test_module_imports_correctly(self) -> None:
        """Test that the module can be imported without errors."""
        from custom_components.bermuda import services

        assert hasattr(services, "BermudaServiceHandler")
        assert hasattr(services, "DUMP_DEVICE_SOFT_LIMIT")

    def test_dump_device_soft_limit_is_reasonable(self) -> None:
        """Test that DUMP_DEVICE_SOFT_LIMIT is a reasonable value."""
        assert DUMP_DEVICE_SOFT_LIMIT > 100
        assert DUMP_DEVICE_SOFT_LIMIT < 10000


class TestResetRoomAutoLearningService:
    """Tests for the reset_room_auto_learning service handler."""

    @pytest.mark.asyncio
    async def test_service_passes_areas_and_returns_counts(self) -> None:
        """The handler passes the area ids to the coordinator and returns its counts."""
        mock_coordinator = MagicMock()
        mock_coordinator.async_reset_room_auto_learning = AsyncMock(return_value={"buero": 7})
        handler = BermudaServiceHandler(mock_coordinator)
        mock_call = MagicMock()
        mock_call.data = {"area_id": ["buero"]}

        result = await handler.async_reset_room_auto_learning(mock_call)

        mock_coordinator.async_reset_room_auto_learning.assert_awaited_once_with(["buero"])
        assert result == {"buero": 7}


def test_every_translation_describes_every_service() -> None:
    """Each translation names and describes every declared service and each of its fields."""
    base = Path(__file__).parent.parent / "custom_components" / "bermuda"
    declared = yaml.safe_load((base / "services.yaml").read_text(encoding="utf-8"))
    for translation in sorted((base / "translations").glob("*.json")):
        services = json.loads(translation.read_text(encoding="utf-8")).get("services", {})
        assert set(services) == set(declared), translation.name
        for name, spec in declared.items():
            entry = services[name]
            assert _text(entry, "name") and _text(entry, "description"), (translation.name, name)
            fields = entry.get("fields", {})
            assert set(fields) == set(spec.get("fields", {})), (translation.name, name)
            for field_name, field in fields.items():
                assert _text(field, "name") and _text(field, "description"), (translation.name, name, field_name)


def test_every_raised_translation_key_has_an_english_message() -> None:
    """Each translation_key raised in services.py has a non-empty English message."""
    base = Path(__file__).parent.parent / "custom_components" / "bermuda"
    keys = set(re.findall(r'translation_key="([a-z_]+)"', (base / "services.py").read_text(encoding="utf-8")))
    assert keys, "no translation_key found in services.py"
    exceptions = json.loads((base / "translations" / "en.json").read_text(encoding="utf-8")).get("exceptions", {})
    for key in keys:
        assert _text(exceptions.get(key, {}), "message"), key


def _text(entry: dict[str, object], key: str) -> bool:
    """Return whether entry[key] is a string with visible text."""
    value = entry.get(key)
    return isinstance(value, str) and bool(value.strip())


async def _async_setup(hass: HomeAssistant) -> bool:
    """Run the integration's async_setup, imported here so the module collects without it."""
    from custom_components.bermuda import async_setup  # noqa: PLC0415

    return await async_setup(hass, {})


class TestServicesRegisteredInAsyncSetup:
    """The services exist without a loaded entry and route to the loaded one."""

    @pytest.mark.asyncio
    async def test_services_registered_without_entry(self, hass: HomeAssistant) -> None:
        """async_setup registers both services before any config entry is set up."""
        assert await _async_setup(hass) is True

        assert hass.services.has_service(DOMAIN, "dump_devices")
        assert hass.services.has_service(DOMAIN, "reset_room_auto_learning")

    @pytest.mark.asyncio
    async def test_service_without_loaded_entry_raises_validation_error(self, hass: HomeAssistant) -> None:
        """Without a loaded entry the service fails with a validation error."""
        await _async_setup(hass)

        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN, "reset_room_auto_learning", {"area_id": ["buero"]}, blocking=True, return_response=True
            )

    @pytest.mark.asyncio
    async def test_dump_devices_routes_to_loaded_entry(self, hass: HomeAssistant) -> None:
        """dump_devices reaches the loaded entry's coordinator and keeps its schema."""
        await _async_setup(hass)
        coordinator = MagicMock(service_dump_devices=AsyncMock(return_value={"aa": {"name": "x"}}))
        entry = SimpleNamespace(runtime_data=SimpleNamespace(coordinator=coordinator))

        with patch.object(hass.config_entries, "async_loaded_entries", return_value=[entry]):
            response = await hass.services.async_call(
                DOMAIN, "dump_devices", {"redact": True}, blocking=True, return_response=True
            )
            with pytest.raises(vol.Invalid):
                await hass.services.async_call(
                    DOMAIN, "dump_devices", {"unknown": 1}, blocking=True, return_response=True
                )

        assert response == {"aa": {"name": "x"}}
        assert coordinator.service_dump_devices.await_args.args[0].data == {"redact": True}

    @pytest.mark.asyncio
    async def test_validation_error_uses_translated_key(self, hass: HomeAssistant) -> None:
        """The validation error without a loaded entry carries the integration's translation key."""
        await _async_setup(hass)

        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(DOMAIN, "dump_devices", {}, blocking=True, return_response=True)

        assert err.value.translation_domain == DOMAIN
        assert err.value.translation_key == "integration_not_loaded"

    @pytest.mark.asyncio
    async def test_service_routes_to_loaded_entry(self, hass: HomeAssistant) -> None:
        """With a loaded entry the call reaches its coordinator and returns its response."""
        await _async_setup(hass)
        coordinator = MagicMock(service_reset_room_auto_learning=AsyncMock(return_value={"buero": 2}))
        entry = SimpleNamespace(runtime_data=SimpleNamespace(coordinator=coordinator))

        with patch.object(hass.config_entries, "async_loaded_entries", return_value=[entry]):
            response = await hass.services.async_call(
                DOMAIN, "reset_room_auto_learning", {"area_id": "buero"}, blocking=True, return_response=True
            )

        assert response == {"buero": 2}
        call = coordinator.service_reset_room_auto_learning.await_args.args[0]
        assert call.data["area_id"] == ["buero"]
