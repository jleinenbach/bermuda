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
        mock_service_handler.async_redact_data = AsyncMock(side_effect=lambda x: x)
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
        mock_coordinator.service_handler.async_redact_data = AsyncMock(side_effect=lambda x: x)

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

        # Mock service handler - verify the IRK data goes through redaction
        mock_coordinator.service_handler = MagicMock()
        mock_coordinator.service_handler.async_redact_data = AsyncMock(return_value={"redacted": True})

        # Mock _manager
        mock_coordinator._manager = MagicMock()
        mock_coordinator._manager.async_diagnostics = AsyncMock(return_value={})

        # Mock service_dump_devices
        mock_coordinator.service_dump_devices = AsyncMock(return_value={})

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        result = await async_get_config_entry_diagnostics(hass, mock_entry)

        # The IRK manager data is part of the one dict that is redacted
        mock_coordinator.service_handler.async_redact_data.assert_awaited_once()
        redacted_input = mock_coordinator.service_handler.async_redact_data.await_args.args[0]
        assert redacted_input["irk_manager"] == {"irk_keys": "sensitive_data"}
        assert result == {"redacted": True}

    @pytest.mark.asyncio
    async def test_dumps_devices_raw_and_redacts_whole_result(self, hass: HomeAssistant) -> None:
        """The device dump is taken unredacted and redacted once with the rest of the result."""
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
        mock_coordinator.service_handler.async_redact_data = AsyncMock(side_effect=lambda x: x)
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
        # The dump itself is not redacted; the whole result is, in one call
        call_args = mock_coordinator.service_dump_devices.call_args[0][0]
        assert call_args.data.get("redact") is False
        mock_coordinator.service_handler.async_redact_data.assert_awaited_once()
        assert result["devices"] == {"dump": "data"}


class TestDiagnosticsIntegration:
    """Integration tests for diagnostics module."""

    def test_module_imports_correctly(self) -> None:
        """Test that the module can be imported without errors."""
        from custom_components.bermuda import diagnostics

        assert hasattr(diagnostics, "async_get_config_entry_diagnostics")


def _real_diag_coordinator(hass: HomeAssistant, devices: dict | None = None) -> MagicMock:
    """Coordinator mock with a real service handler and real IRK/FMDN managers."""
    from custom_components.bermuda.bermuda_irk import BermudaIrkManager  # noqa: PLC0415
    from custom_components.bermuda.fmdn.manager import BermudaFmdnManager  # noqa: PLC0415
    from custom_components.bermuda.services import BermudaServiceHandler  # noqa: PLC0415

    coord = MagicMock()
    coord.hass = hass
    coord.scanner_list = []
    coord.options = {}
    coord.pb_state_sources = {}
    coord.devices = devices or {}
    coord.count_active_devices = MagicMock(return_value=0)
    coord.count_active_scanners = MagicMock(return_value=0)
    coord.irk_manager = BermudaIrkManager()
    coord.fmdn.manager = BermudaFmdnManager()
    coord.area_selection.get_auto_learning_diagnostics = MagicMock(return_value={})
    coord.area_selection.get_reference_tracker_diagnostics = MagicMock(return_value={})
    coord._manager.async_diagnostics = AsyncMock(return_value={})
    handler = BermudaServiceHandler(coord)
    coord.service_handler = handler
    coord.service_dump_devices = handler.async_dump_devices
    return coord


async def _diagnostics_text(hass: HomeAssistant, coord: MagicMock) -> tuple[dict, str]:
    from homeassistant.helpers.json import json_bytes  # noqa: PLC0415

    entry = MagicMock()
    entry.runtime_data.coordinator = coord
    result = await async_get_config_entry_diagnostics(hass, entry)
    return result, json_bytes(result).decode().lower()


class TestDiagnosticsRedaction:
    """The diagnostics never contain raw identifiers."""

    @pytest.mark.asyncio
    async def test_diagnostics_reference_tracker_redacted(self, hass: HomeAssistant) -> None:
        """T-R7: reference tracker and scanner addresses are redacted."""
        coord = _real_diag_coordinator(hass)
        coord.area_selection.get_reference_tracker_diagnostics = MagicMock(
            return_value={
                "configured_count": 1,
                "configured_addresses": ["a1:b2:c3:d4:e5:f6"],
                "aggregation_by_area": {"area.raum_a": {"primary_scanner": "0a:1b:2c:3d:4e:5f"}},
            }
        )

        _result, text = await _diagnostics_text(hass, coord)

        assert "a1:b2:c3:d4:e5:f6" not in text
        assert "0a:1b:2c:3d:4e:5f" not in text

    @pytest.mark.asyncio
    async def test_diagnostics_irk_truncated(self, hass: HomeAssistant) -> None:
        """T-R11: an IRK without a meta device appears at most with its first two bytes."""
        irk = bytes.fromhex("8f1e2d3c4b5a69788796a5b4c3d2e1f0")
        coord = _real_diag_coordinator(hass)
        coord.irk_manager.add_irk(irk)

        _result, text = await _diagnostics_text(hass, coord)

        assert irk.hex() not in text
        assert irk.hex()[4:] not in text

    @pytest.mark.asyncio
    async def test_diagnostics_fmdn_ids_redacted(self, hass: HomeAssistant) -> None:
        """T-R12: FMDN EIDs, device ids and canonical ids are redacted, entries kept."""
        eid = bytes.fromhex("0f1e2d3c4b5a69788796a5b4c3d2e1f00102030a")
        meta = MagicMock()
        meta.address_type = "fmdn"
        meta.to_dict = MagicMock(return_value={"name": "tracker"})
        coord = _real_diag_coordinator(hass, devices={"fmdn:canonzulu42": meta})
        coord.fmdn.manager.record_eid_seen(
            eid,
            "c1:d2:e3:f4:a5:b6",
            resolution_status="devyankee17",
            device_id="devyankee17",
            canonical_id="canonzulu42",
        )
        raw_eids = len(coord.fmdn.manager.get_diagnostics_no_redactions()["resolved_eids"])

        result, text = await _diagnostics_text(hass, coord)

        for raw in (eid.hex(), "devyankee17", "canonzulu42", "c1:d2:e3:f4:a5:b6", "c1d2e3f4a5b6"):
            assert raw not in text
        assert len(result["fmdn_manager"]["resolved_eids"]) == raw_eids

    @pytest.mark.asyncio
    async def test_diagnostics_fmdn_ids_of_silent_tracker_redacted(self, hass: HomeAssistant) -> None:
        """T-R16: a tracker without an EID seen since start still has its FMDN ids redacted."""
        from custom_components.bermuda.const import ADDR_TYPE_FMDN_DEVICE  # noqa: PLC0415

        meta = MagicMock()
        meta.address_type = ADDR_TYPE_FMDN_DEVICE
        meta.fmdn_device_id = "devquiet0815"
        meta.fmdn_canonical_id = "canonquiet4711"
        meta.to_dict = MagicMock(
            return_value={"name": "tracker", "fmdn_device_id": "devquiet0815", "fmdn_canonical_id": "canonquiet4711"}
        )
        coord = _real_diag_coordinator(hass, devices={"fmdn:other-key": meta})

        _result, text = await _diagnostics_text(hass, coord)

        assert "devquiet0815" not in text
        assert "canonquiet4711" not in text

    @pytest.mark.asyncio
    async def test_diagnostics_ble_device_object_redacted_for_endpoint_encoder(self, hass: HomeAssistant) -> None:
        """T-R17: an object in the Bluetooth diagnostics is redacted before the endpoint encoder calls repr()."""
        import json  # noqa: PLC0415

        from bleak.backends.device import BLEDevice  # noqa: PLC0415
        from homeassistant.helpers.json import ExtendedJSONEncoder  # noqa: PLC0415

        coord = _real_diag_coordinator(hass)
        coord._manager.async_diagnostics = AsyncMock(
            return_value={"all_history": [{"device": BLEDevice("AB:CD:EF:01:23:45", "tracker", None)}]}
        )

        entry = MagicMock()
        entry.runtime_data.coordinator = coord
        result = await async_get_config_entry_diagnostics(hass, entry)
        # The diagnostics endpoint serializes with ExtendedJSONEncoder, which falls back to repr().
        text = json.dumps(result, cls=ExtendedJSONEncoder).lower()

        assert "ab:cd:ef:01:23:45" not in text
        assert "bledevice(" in text
