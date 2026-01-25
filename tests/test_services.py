"""Test Bermuda services module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.bermuda.const import ADDR_TYPE_PRIVATE_BLE_DEVICE, CONF_DEVICES
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
