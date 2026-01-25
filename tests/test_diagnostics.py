"""Test Bermuda diagnostics module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.bermuda.diagnostics import async_get_config_entry_diagnostics


class TestAsyncGetConfigEntryDiagnostics:
    """Tests for async_get_config_entry_diagnostics function."""

    @pytest.mark.asyncio
    async def test_returns_diagnostics_dict(self, hass: HomeAssistant) -> None:
        """Test that function returns diagnostics dictionary."""
        mock_coordinator = MagicMock()
        mock_coordinator.count_active_devices = MagicMock(return_value=5)
        mock_coordinator.count_active_scanners = MagicMock(return_value=3)
        mock_coordinator.devices = {"dev1": MagicMock(), "dev2": MagicMock()}
        mock_coordinator.scanner_list = ["scanner1", "scanner2", "scanner3"]

        # Mock IRK manager
        mock_irk_manager = MagicMock()
        mock_irk_manager.get_diagnostics_no_redactions = MagicMock(return_value={"irk": "data"})
        mock_coordinator.irk_manager = mock_irk_manager

        # Mock FMDN manager
        mock_fmdn_manager = MagicMock()
        mock_fmdn_manager.get_diagnostics_no_redactions = MagicMock(return_value={"fmdn": "data"})
        mock_coordinator.fmdn = MagicMock()
        mock_coordinator.fmdn.manager = mock_fmdn_manager

        # Mock service handler
        mock_service_handler = MagicMock()
        mock_service_handler.redact_data = MagicMock(side_effect=lambda x: f"redacted_{x}")
        mock_coordinator.service_handler = mock_service_handler

        # Mock _manager for BT diagnostics
        mock_manager = MagicMock()
        mock_manager.async_diagnostics = AsyncMock(return_value={"bt": "data"})
        mock_coordinator._manager = mock_manager

        # Mock service_dump_devices
        mock_coordinator.service_dump_devices = AsyncMock(return_value={"devices": "dump"})

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        result = await async_get_config_entry_diagnostics(hass, mock_entry)

        assert "active_devices" in result
        assert "active_scanners" in result
        assert "irk_manager" in result
        assert "fmdn_manager" in result
        assert "devices" in result
        assert "bt_manager" in result

    @pytest.mark.asyncio
    async def test_formats_active_counts_correctly(self, hass: HomeAssistant) -> None:
        """Test that active device/scanner counts are formatted correctly."""
        mock_coordinator = MagicMock()
        mock_coordinator.count_active_devices = MagicMock(return_value=10)
        mock_coordinator.count_active_scanners = MagicMock(return_value=5)
        mock_coordinator.devices = {f"dev{i}": MagicMock() for i in range(15)}
        mock_coordinator.scanner_list = ["scanner1", "scanner2", "scanner3", "scanner4", "scanner5", "scanner6"]

        # Mock IRK manager
        mock_coordinator.irk_manager = MagicMock()
        mock_coordinator.irk_manager.get_diagnostics_no_redactions = MagicMock(return_value={})

        # Mock FMDN manager
        mock_coordinator.fmdn = MagicMock()
        mock_coordinator.fmdn.manager = MagicMock()
        mock_coordinator.fmdn.manager.get_diagnostics_no_redactions = MagicMock(return_value={})

        # Mock service handler
        mock_coordinator.service_handler = MagicMock()
        mock_coordinator.service_handler.redact_data = MagicMock(side_effect=lambda x: x)

        # Mock _manager
        mock_coordinator._manager = MagicMock()
        mock_coordinator._manager.async_diagnostics = AsyncMock(return_value={})

        # Mock service_dump_devices
        mock_coordinator.service_dump_devices = AsyncMock(return_value={})

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        result = await async_get_config_entry_diagnostics(hass, mock_entry)

        assert result["active_devices"] == "10/15"
        assert result["active_scanners"] == "5/6"

    @pytest.mark.asyncio
    async def test_redacts_irk_manager_data(self, hass: HomeAssistant) -> None:
        """Test that IRK manager data is redacted."""
        mock_coordinator = MagicMock()
        mock_coordinator.count_active_devices = MagicMock(return_value=0)
        mock_coordinator.count_active_scanners = MagicMock(return_value=0)
        mock_coordinator.devices = {}
        mock_coordinator.scanner_list = []

        # Mock IRK manager with sensitive data
        mock_coordinator.irk_manager = MagicMock()
        mock_coordinator.irk_manager.get_diagnostics_no_redactions = MagicMock(
            return_value={"irk_keys": "sensitive_data"}
        )

        # Mock FMDN manager
        mock_coordinator.fmdn = MagicMock()
        mock_coordinator.fmdn.manager = MagicMock()
        mock_coordinator.fmdn.manager.get_diagnostics_no_redactions = MagicMock(return_value={})

        # Mock service handler - verify redact_data is called
        mock_coordinator.service_handler = MagicMock()
        mock_coordinator.service_handler.redact_data = MagicMock(return_value={"redacted": True})

        # Mock _manager
        mock_coordinator._manager = MagicMock()
        mock_coordinator._manager.async_diagnostics = AsyncMock(return_value={})

        # Mock service_dump_devices
        mock_coordinator.service_dump_devices = AsyncMock(return_value={})

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        result = await async_get_config_entry_diagnostics(hass, mock_entry)

        # Verify redact_data was called with IRK manager data
        mock_coordinator.service_handler.redact_data.assert_any_call({"irk_keys": "sensitive_data"})

    @pytest.mark.asyncio
    async def test_calls_service_dump_devices_with_redact(self, hass: HomeAssistant) -> None:
        """Test that service_dump_devices is called with redact=True."""
        mock_coordinator = MagicMock()
        mock_coordinator.count_active_devices = MagicMock(return_value=0)
        mock_coordinator.count_active_scanners = MagicMock(return_value=0)
        mock_coordinator.devices = {}
        mock_coordinator.scanner_list = []

        # Mock managers
        mock_coordinator.irk_manager = MagicMock()
        mock_coordinator.irk_manager.get_diagnostics_no_redactions = MagicMock(return_value={})
        mock_coordinator.fmdn = MagicMock()
        mock_coordinator.fmdn.manager = MagicMock()
        mock_coordinator.fmdn.manager.get_diagnostics_no_redactions = MagicMock(return_value={})
        mock_coordinator.service_handler = MagicMock()
        mock_coordinator.service_handler.redact_data = MagicMock(side_effect=lambda x: x)
        mock_coordinator._manager = MagicMock()
        mock_coordinator._manager.async_diagnostics = AsyncMock(return_value={})

        # Mock service_dump_devices to capture the call
        mock_coordinator.service_dump_devices = AsyncMock(return_value={"dump": "data"})

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        result = await async_get_config_entry_diagnostics(hass, mock_entry)

        # Verify service_dump_devices was called
        mock_coordinator.service_dump_devices.assert_called_once()
        # Check that the call had redact=True
        call_args = mock_coordinator.service_dump_devices.call_args[0][0]
        assert call_args.data.get("redact") is True


class TestDiagnosticsIntegration:
    """Integration tests for diagnostics module."""

    def test_module_imports_correctly(self) -> None:
        """Test that the module can be imported without errors."""
        from custom_components.bermuda import diagnostics

        assert hasattr(diagnostics, "async_get_config_entry_diagnostics")
