"""Tests for FMDN integration error handling.

These tests verify that the FMDN integration gracefully handles errors from
the googlefindmy integration, preventing crashes in the Bermuda update loop.

See CLAUDE.md Lesson #53: External Integration Calls Need Defensive Exception Handling.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from custom_components.bermuda.fmdn.integration import FmdnIntegration


@pytest.fixture
def mock_coordinator() -> MagicMock:
    """Create a mock coordinator with required attributes."""
    coordinator = MagicMock()
    coordinator.hass = MagicMock()
    coordinator.hass.data = {}
    coordinator.dr = MagicMock()
    coordinator.er = MagicMock()
    coordinator.metadevices = {}
    coordinator._do_fmdn_device_init = True
    return coordinator


@pytest.fixture
def fmdn_integration(mock_coordinator: MagicMock) -> FmdnIntegration:
    """Create an FmdnIntegration instance for testing."""
    return FmdnIntegration(mock_coordinator)


class TestExtractCanonicalIdErrorHandling:
    """Tests for _extract_canonical_id defensive handling."""

    def test_handles_none_device(self, fmdn_integration: FmdnIntegration) -> None:
        """Test that None device returns None without error."""
        result = fmdn_integration._extract_canonical_id(None)
        assert result is None

    def test_handles_device_without_identifiers(self, fmdn_integration: FmdnIntegration) -> None:
        """Test device with no identifiers attribute."""
        device = MagicMock(spec=[])  # No attributes at all
        result = fmdn_integration._extract_canonical_id(device)
        assert result is None

    def test_handles_device_with_none_identifiers(self, fmdn_integration: FmdnIntegration) -> None:
        """Test device with identifiers=None."""
        device = MagicMock()
        device.identifiers = None
        result = fmdn_integration._extract_canonical_id(device)
        assert result is None

    def test_handles_non_iterable_identifiers(self, fmdn_integration: FmdnIntegration) -> None:
        """Test device with non-iterable identifiers."""
        device = MagicMock()
        device.identifiers = 12345  # Not iterable
        result = fmdn_integration._extract_canonical_id(device)
        assert result is None

    def test_handles_malformed_identifier_tuple(self, fmdn_integration: FmdnIntegration) -> None:
        """Test device with malformed identifier tuples."""
        device = MagicMock()
        device.identifiers = [
            ("googlefindmy",),  # Too short
            None,  # None entry
            "not_a_tuple",  # String instead of tuple
            ("googlefindmy", "entry:device"),  # Valid one
        ]
        result = fmdn_integration._extract_canonical_id(device)
        assert result == "entry:device"

    def test_handles_empty_identifiers(self, fmdn_integration: FmdnIntegration) -> None:
        """Test device with empty identifiers list."""
        device = MagicMock()
        device.identifiers = []
        result = fmdn_integration._extract_canonical_id(device)
        assert result is None


class TestProcessFmdnEntityErrorHandling:
    """Tests for _process_fmdn_entity defensive handling."""

    def test_handles_entity_without_domain(self, fmdn_integration: FmdnIntegration) -> None:
        """Test entity with no domain attribute."""
        entity = MagicMock(spec=[])  # No attributes
        # Should not raise
        fmdn_integration._process_fmdn_entity(entity)

    def test_handles_entity_with_none_domain(self, fmdn_integration: FmdnIntegration) -> None:
        """Test entity where domain access raises."""
        entity = MagicMock()
        type(entity).domain = PropertyMock(side_effect=AttributeError("no domain"))
        # Should not raise
        fmdn_integration._process_fmdn_entity(entity)

    def test_handles_entity_without_device_id(
        self, fmdn_integration: FmdnIntegration, mock_coordinator: MagicMock
    ) -> None:
        """Test entity with no device_id."""
        entity = MagicMock()
        entity.domain = "device_tracker"
        entity.entity_id = "device_tracker.test"
        entity.device_id = None
        # Should not raise, should return early
        fmdn_integration._process_fmdn_entity(entity)

    def test_handles_device_registry_error(
        self, fmdn_integration: FmdnIntegration, mock_coordinator: MagicMock
    ) -> None:
        """Test when device registry access fails."""
        entity = MagicMock()
        entity.domain = "device_tracker"
        entity.entity_id = "device_tracker.test"
        entity.device_id = "test_device_id"

        mock_coordinator.dr.async_get.side_effect = KeyError("device not found")
        # Should not raise
        fmdn_integration._process_fmdn_entity(entity)

    def test_handles_device_with_missing_attributes(
        self, fmdn_integration: FmdnIntegration, mock_coordinator: MagicMock
    ) -> None:
        """Test when device has missing required attributes."""
        entity = MagicMock()
        entity.domain = "device_tracker"
        entity.entity_id = "device_tracker.test"
        entity.device_id = "test_device_id"

        # Device exists but has no id attribute
        device = MagicMock(spec=[])
        mock_coordinator.dr.async_get.return_value = device
        # Should not raise
        fmdn_integration._process_fmdn_entity(entity)


class TestDiscoverMetadevicesErrorHandling:
    """Tests for discover_metadevices defensive handling."""

    def test_handles_config_entries_error(self, fmdn_integration: FmdnIntegration, mock_coordinator: MagicMock) -> None:
        """Test when config_entries access fails."""
        mock_coordinator.hass.config_entries.async_entries.side_effect = KeyError("no entries")
        # Should not raise
        fmdn_integration.discover_metadevices()

    def test_handles_entity_iteration_error(
        self, fmdn_integration: FmdnIntegration, mock_coordinator: MagicMock
    ) -> None:
        """Test when iterating entities raises."""
        entry = MagicMock()
        entry.entry_id = "test_entry"
        mock_coordinator.hass.config_entries.async_entries.return_value = [entry]
        mock_coordinator.er.entities.get_entries_for_config_entry_id.side_effect = TypeError("bad type")
        # Should not raise
        fmdn_integration.discover_metadevices()

    def test_handles_single_entity_error_continues_loop(
        self, fmdn_integration: FmdnIntegration, mock_coordinator: MagicMock
    ) -> None:
        """Test that a single entity error doesn't stop processing other entities."""
        entry = MagicMock()
        entry.entry_id = "test_entry"
        mock_coordinator.hass.config_entries.async_entries.return_value = [entry]

        # First entity will cause an error, second should still be processed
        entity1 = MagicMock()
        entity1.domain = "device_tracker"
        entity1.entity_id = "device_tracker.bad"
        type(entity1).device_id = PropertyMock(side_effect=RuntimeError("unexpected"))

        entity2 = MagicMock()
        entity2.domain = "device_tracker"
        entity2.entity_id = "device_tracker.good"
        entity2.device_id = None  # Will return early but won't crash

        mock_coordinator.er.entities.get_entries_for_config_entry_id.return_value = [entity1, entity2]
        # Should not raise
        fmdn_integration.discover_metadevices()


class TestHandleAdvertisementErrorHandling:
    """Tests for handle_advertisement defensive handling."""

    def test_handles_extract_eids_error(self, fmdn_integration: FmdnIntegration, mock_coordinator: MagicMock) -> None:
        """Test when extract_eids raises."""
        device = MagicMock()
        device.address = "AA:BB:CC:DD:EE:FF"
        service_data: dict[str, Any] = {"key": "value"}

        with patch.object(fmdn_integration, "extract_eids", side_effect=ValueError("bad data")):
            # Should not raise
            fmdn_integration.handle_advertisement(device, service_data)

    def test_handles_resolution_error_continues_loop(
        self, fmdn_integration: FmdnIntegration, mock_coordinator: MagicMock
    ) -> None:
        """Test that resolution error for one EID doesn't stop processing others."""
        device = MagicMock()
        device.address = "AA:BB:CC:DD:EE:FF"
        device.metadevice_type = set()
        service_data: dict[str, Any] = {"key": "value"}

        eid1 = b"\x01\x02\x03"
        eid2 = b"\x04\x05\x06"

        with patch.object(fmdn_integration, "extract_eids", return_value={eid1, eid2}):
            # First EID causes error, second should be processed
            call_count = 0

            def side_effect(*args: Any, **kwargs: Any) -> tuple[list[Any], Any]:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise RuntimeError("unexpected error")
                return ([], None)

            with patch.object(fmdn_integration, "process_resolution_all_with_status", side_effect=side_effect):
                # Should not raise
                fmdn_integration.handle_advertisement(device, service_data)
                # Should have been called twice (both EIDs attempted)
                assert call_count == 2

    def test_handles_register_source_error(
        self, fmdn_integration: FmdnIntegration, mock_coordinator: MagicMock
    ) -> None:
        """Test when register_source raises."""
        device = MagicMock()
        device.address = "AA:BB:CC:DD:EE:FF"
        device.metadevice_type = set()
        service_data: dict[str, Any] = {"key": "value"}

        eid = b"\x01\x02\x03"
        match = MagicMock()
        match.device_id = "test_device"
        match.canonical_id = "entry:device"

        with patch.object(fmdn_integration, "extract_eids", return_value={eid}):
            with patch.object(fmdn_integration, "process_resolution_all_with_status", return_value=([match], None)):
                with patch.object(fmdn_integration, "register_source", side_effect=RuntimeError("register failed")):
                    # Should not raise
                    fmdn_integration.handle_advertisement(device, service_data)


class TestGetResolverErrorHandling:
    """Tests for get_resolver defensive handling."""

    def test_handles_missing_domain_bucket(
        self, fmdn_integration: FmdnIntegration, mock_coordinator: MagicMock
    ) -> None:
        """Test when googlefindmy domain is not in hass.data."""
        mock_coordinator.hass.data = {}
        result = fmdn_integration.get_resolver()
        assert result is None

    def test_handles_non_dict_bucket(self, fmdn_integration: FmdnIntegration, mock_coordinator: MagicMock) -> None:
        """Test when domain bucket is not a dict."""
        mock_coordinator.hass.data = {"googlefindmy": "not_a_dict"}
        result = fmdn_integration.get_resolver()
        assert result is None

    def test_handles_missing_resolver(self, fmdn_integration: FmdnIntegration, mock_coordinator: MagicMock) -> None:
        """Test when eid_resolver key is missing."""
        mock_coordinator.hass.data = {"googlefindmy": {}}
        result = fmdn_integration.get_resolver()
        assert result is None

    def test_handles_resolver_without_resolve_eid(
        self, fmdn_integration: FmdnIntegration, mock_coordinator: MagicMock
    ) -> None:
        """Test when resolver doesn't have resolve_eid method."""
        resolver = MagicMock(spec=[])  # No methods
        mock_coordinator.hass.data = {"googlefindmy": {"eid_resolver": resolver}}
        result = fmdn_integration.get_resolver()
        assert result is None

    def test_handles_resolver_with_non_callable_resolve_eid(
        self, fmdn_integration: FmdnIntegration, mock_coordinator: MagicMock
    ) -> None:
        """Test when resolve_eid is not callable."""
        resolver = MagicMock()
        resolver.resolve_eid = "not_a_function"
        mock_coordinator.hass.data = {"googlefindmy": {"eid_resolver": resolver}}
        result = fmdn_integration.get_resolver()
        assert result is None
